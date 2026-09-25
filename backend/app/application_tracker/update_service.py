"""Orchestration for single and sequential batch tracker refreshes."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlsplit

from app.application_tracker.browser.models import BrowserEvent
from app.application_tracker.models import (
    ApplicationInput,
    ApplicationStatus,
    CheckResult,
    StatusRecord,
)
from app.application_tracker.store import (
    ApplicationTrackerStore,
    StoredApplication,
)


class TrackerAgent(Protocol):
    async def run(
        self,
        application: ApplicationInput,
        **kwargs: Any,
    ) -> StatusRecord: ...


class ApplicationNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class RefreshOutcome:
    application: StoredApplication
    skipped: bool = False
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    type: str
    index: int = 0
    total: int = 0
    completed: int = 0
    application_id: int | None = None
    company: str | None = None
    message: str | None = None
    application: StoredApplication | None = None


class TrackerUpdateService:
    def __init__(self, *, store: ApplicationTrackerStore, agent: TrackerAgent) -> None:
        self._store = store
        self._agent = agent

    async def refresh_one(
        self,
        user_id: str,
        application_id: int,
    ) -> RefreshOutcome:
        application = await asyncio.to_thread(
            self._store.get_application,
            user_id,
            application_id,
        )
        if application is None:
            raise ApplicationNotFoundError(f"Application {application_id} was not found")
        if application.terminal:
            return RefreshOutcome(
                application=application,
                skipped=True,
                reason="terminal_status",
            )

        record = await self._run_agent_record(user_id, application)
        saved = await asyncio.to_thread(
            self._store.save_check,
            user_id,
            application.id,
            record,
        )
        return RefreshOutcome(application=saved)

    async def stream_refresh_all(self, user_id: str) -> AsyncIterator[ProgressEvent]:
        applications = await asyncio.to_thread(self._store.list_applications, user_id)
        pending = sorted(
            (item for item in applications if not item.terminal),
            key=lambda item: ((urlsplit(item.url).hostname or "").lower(), item.url),
        )
        total = len(pending)
        yield ProgressEvent(type="batch_started", total=total)

        completed = 0
        for index, application in enumerate(pending, start=1):
            yield ProgressEvent(
                type="row_started",
                index=index,
                total=total,
                completed=completed,
                application_id=application.id,
                company=application.company,
            )
            browser_events: asyncio.Queue[BrowserEvent] = asyncio.Queue()

            async def on_browser_event(event: BrowserEvent) -> None:
                await browser_events.put(event)

            task = asyncio.create_task(
                self._run_and_save(
                    user_id,
                    application,
                    on_browser_event=on_browser_event,
                )
            )
            try:
                while not task.done():
                    try:
                        event = await asyncio.wait_for(browser_events.get(), timeout=0.1)
                    except TimeoutError:
                        continue
                    yield ProgressEvent(
                        type="browser",
                        index=index,
                        total=total,
                        completed=completed,
                        application_id=application.id,
                        company=application.company,
                        message=event.message,
                    )
                while not browser_events.empty():
                    event = browser_events.get_nowait()
                    yield ProgressEvent(
                        type="browser",
                        index=index,
                        total=total,
                        completed=completed,
                        application_id=application.id,
                        company=application.company,
                        message=event.message,
                    )
                try:
                    saved = await task
                except Exception as exc:
                    completed += 1
                    yield ProgressEvent(
                        type="row_failed",
                        index=index,
                        total=total,
                        completed=completed,
                        application_id=application.id,
                        company=application.company,
                        message=f"检查失败，已继续下一条（{type(exc).__name__}）",
                    )
                    continue
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            completed += 1
            yield ProgressEvent(
                type="row_completed",
                index=index,
                total=total,
                completed=completed,
                application_id=application.id,
                company=application.company,
                application=saved,
            )
        yield ProgressEvent(
            type="batch_completed",
            total=total,
            completed=completed,
        )

    async def _run_and_save(
        self,
        user_id: str,
        application: StoredApplication,
        *,
        on_browser_event: Any,
    ) -> StoredApplication:
        record = await self._run_agent_record(
            user_id,
            application,
            on_event=on_browser_event,
        )
        return await asyncio.to_thread(
            self._store.save_check,
            user_id,
            application.id,
            record,
        )

    async def _run_agent_record(
        self,
        user_id: str,
        application: StoredApplication,
        *,
        on_event: Any = None,
    ) -> StatusRecord:
        previous = application.to_previous_record()
        try:
            return await self._agent.run(
                application.to_input(),
                user_id=user_id,
                previous=previous,
                on_event=on_event,
            )
        except Exception:
            checked_at = datetime.now(UTC)
            return StatusRecord(
                company=application.company,
                role=application.role,
                url=application.url,
                status=previous.status if previous is not None else ApplicationStatus.UNKNOWN,
                raw_status=previous.raw_status if previous is not None else "",
                confidence=previous.confidence if previous is not None else 0,
                evidence=previous.evidence if previous is not None else "",
                checked_at=checked_at,
                changed_at=previous.changed_at if previous is not None else checked_at,
                check_result=CheckResult.FETCH_FAILED,
            )
