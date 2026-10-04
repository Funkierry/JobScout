"""Orchestration for single and domain-concurrent batch tracker refreshes."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
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
    def __init__(self, *, store: ApplicationTrackerStore, agent: TrackerAgent, concurrency: int | None = None) -> None:
        self._store = store
        self._agent = agent
        self._concurrency = int(os.getenv("APPLICATION_TRACKER_BATCH_CONCURRENCY", "2")) if concurrency is None else concurrency
        if not 1 <= self._concurrency <= 8:
            raise ValueError("tracker batch concurrency must be between 1 and 8")

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

        groups: dict[str, list[tuple[int, StoredApplication]]] = {}
        for index, application in enumerate(pending, start=1):
            host = (urlsplit(application.url).hostname or "").lower().rstrip(".")
            groups.setdefault(host, []).append((index, application))
        domains: asyncio.Queue = asyncio.Queue()
        for rows in groups.values():
            domains.put_nowait(rows)
        events: asyncio.Queue[ProgressEvent] = asyncio.Queue(maxsize=128)

        async def worker() -> None:
            while not domains.empty():
                rows = domains.get_nowait()
                for index, application in rows:
                    fields = {"index": index, "total": total, "application_id": application.id, "company": application.company}
                    await events.put(ProgressEvent(type="row_started", **fields))

                    async def on_browser_event(event: BrowserEvent) -> None:
                        await events.put(ProgressEvent(type="browser", message=event.message, **fields))

                    try:
                        saved = await self._run_and_save(user_id, application, on_browser_event=on_browser_event)
                        await events.put(ProgressEvent(type="row_completed", application=saved, **fields))
                    except Exception as exc:
                        await events.put(ProgressEvent(type="row_failed", message=f"检查失败，继续处理其余记录（{type(exc).__name__}）", **fields))

        tasks = [asyncio.create_task(worker()) for _ in range(min(self._concurrency, len(groups)))]
        completed = 0
        try:
            while completed < total:
                event = await events.get()
                if event.type in {"row_completed", "row_failed"}:
                    completed += 1
                yield replace(event, completed=completed)
            await asyncio.gather(*tasks)
            yield ProgressEvent(type="batch_completed", total=total, completed=completed)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

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
            interactive_login=False,
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
        interactive_login: bool | None = None,
    ) -> StatusRecord:
        previous = application.to_previous_record()
        try:
            return await self._agent.run(
                application.to_input(),
                user_id=user_id,
                previous=previous,
                on_event=on_event,
                **({"interactive_login": interactive_login} if interactive_login is not None else {}),
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
