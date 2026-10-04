from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.application_tracker.models import (
    ApplicationInput,
    ApplicationStatus,
    CheckResult,
    StatusRecord,
)
from app.application_tracker.store import ApplicationTrackerStore
from app.application_tracker.update_service import TrackerUpdateService


class FakeAgent:
    def __init__(self) -> None:
        self.urls: list[str] = []

    async def run(
        self,
        application: ApplicationInput,
        **kwargs: Any,
    ) -> StatusRecord:
        del kwargs
        self.urls.append(application.url)
        checked_at = datetime(2026, 9, 25, 4, 0, tzinfo=UTC)
        return StatusRecord(
            company=application.company,
            role=application.role,
            url=application.url,
            status=ApplicationStatus.ASSESSMENT,
            raw_status="Assessment",
            confidence=0.91,
            evidence="Current status: Assessment",
            checked_at=checked_at,
            changed_at=checked_at,
            check_result=CheckResult.SUCCESS,
        )


class FailingAgent:
    async def run(
        self,
        application: ApplicationInput,
        **kwargs: Any,
    ) -> StatusRecord:
        del application, kwargs
        raise RuntimeError("browser crashed")


def _row(company: str, url: str) -> ApplicationInput:
    return ApplicationInput(company=company, role="AI PM", url=url)


@pytest.mark.asyncio
async def test_refresh_all_groups_domains_skips_terminal_and_emits_progress(
    tmp_path: Path,
) -> None:
    store = ApplicationTrackerStore(tmp_path / "tracker.db")
    store.import_applications(
        "user-1",
        [
            _row("B", "https://jobs.b.example/applications/1"),
            _row("A2", "https://jobs.a.example/applications/2"),
            _row("A1", "https://jobs.a.example/applications/1"),
            _row("Done", "https://jobs.c.example/applications/1"),
        ],
    )
    done = next(item for item in store.list_applications("user-1") if item.company == "Done")
    checked_at = datetime(2026, 9, 20, tzinfo=UTC)
    store.save_check(
        "user-1",
        done.id,
        StatusRecord(
            company=done.company,
            role=done.role,
            url=done.url,
            status=ApplicationStatus.OFFER,
            raw_status="Offer",
            confidence=0.99,
            evidence="Offer",
            checked_at=checked_at,
            changed_at=checked_at,
            check_result=CheckResult.SUCCESS,
        ),
    )
    agent = FakeAgent()
    service = TrackerUpdateService(store=store, agent=agent, concurrency=1)

    events = [event async for event in service.stream_refresh_all("user-1")]

    assert agent.urls == [
        "https://jobs.a.example/applications/1",
        "https://jobs.a.example/applications/2",
        "https://jobs.b.example/applications/1",
    ]
    assert events[0].type == "batch_started"
    assert events[0].total == 3
    assert [event.index for event in events if event.type == "row_started"] == [1, 2, 3]
    assert events[-1].type == "batch_completed"
    assert events[-1].completed == 3


@pytest.mark.asyncio
async def test_refresh_one_skips_terminal_row_without_agent_call(tmp_path: Path) -> None:
    store = ApplicationTrackerStore(tmp_path / "tracker.db")
    store.import_applications("user-1", [_row("Done", "https://jobs.example.com/1")])
    row = store.list_applications("user-1")[0]
    checked_at = datetime(2026, 9, 20, tzinfo=UTC)
    store.save_check(
        "user-1",
        row.id,
        StatusRecord(
            company=row.company,
            role=row.role,
            url=row.url,
            status=ApplicationStatus.REJECTED,
            raw_status="Rejected",
            confidence=0.99,
            evidence="Rejected",
            checked_at=checked_at,
            changed_at=checked_at,
            check_result=CheckResult.SUCCESS,
        ),
    )
    agent = FakeAgent()
    service = TrackerUpdateService(store=store, agent=agent)

    outcome = await service.refresh_one("user-1", row.id)

    assert outcome.skipped
    assert outcome.reason == "terminal_status"
    assert agent.urls == []


@pytest.mark.asyncio
async def test_refresh_one_persists_an_agent_failure_as_check_history(tmp_path: Path) -> None:
    store = ApplicationTrackerStore(tmp_path / "tracker.db")
    store.import_applications("user-1", [_row("Example", "https://jobs.example.com/1")])
    row = store.list_applications("user-1")[0]
    service = TrackerUpdateService(store=store, agent=FailingAgent())

    outcome = await service.refresh_one("user-1", row.id)

    history = store.list_checks("user-1", row.id)
    assert not outcome.skipped
    assert outcome.application.check_result is CheckResult.FETCH_FAILED
    assert outcome.application.status is ApplicationStatus.UNKNOWN
    assert len(history) == 1
    assert history[0].check_result is CheckResult.FETCH_FAILED


@pytest.mark.asyncio
async def test_domains_overlap_but_same_host_is_serial_and_progress_monotonic(tmp_path):
    store = ApplicationTrackerStore(tmp_path / "tracker.db")
    store.import_applications("u", [_row("A1", "https://a.example/1"), _row("A2", "https://a.example/2"), _row("B", "https://b.example/1")])
    started, release = asyncio.Event(), asyncio.Event()
    active, maximum = set(), 0

    class Agent(FakeAgent):
        async def run(self, application, **kwargs):
            nonlocal maximum
            host = application.url.split("/")[2]
            assert host not in active
            assert kwargs["interactive_login"] is False
            active.add(host)
            maximum = max(maximum, len(active))
            if len(active) == 2:
                started.set()
            await release.wait()
            try:
                return await super().run(application, **kwargs)
            finally:
                active.remove(host)

    service = TrackerUpdateService(store=store, agent=Agent(), concurrency=2)

    async def consume():
        return [event async for event in service.stream_refresh_all("u")]

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(started.wait(), 2)
        release.set()
        events = await asyncio.wait_for(task, 2)
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert maximum == 2
    assert [e.completed for e in events] == sorted(e.completed for e in events)
    assert events[-1].completed == 3


@pytest.mark.asyncio
async def test_closing_batch_cancels_every_active_worker(tmp_path):
    store = ApplicationTrackerStore(tmp_path / "tracker.db")
    store.import_applications("u", [_row("A", "https://a.example/1"), _row("B", "https://b.example/1")])
    both = asyncio.Event()
    active, cancelled = set(), set()

    class Agent:
        async def run(self, application, **kwargs):
            active.add(application.company)
            if len(active) == 2:
                both.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.add(application.company)

    stream = TrackerUpdateService(store=store, agent=Agent(), concurrency=2).stream_refresh_all("u")
    assert (await anext(stream)).type == "batch_started"
    assert (await anext(stream)).type == "row_started"
    await asyncio.wait_for(both.wait(), 2)
    await stream.aclose()
    assert cancelled == {"A", "B"}


@pytest.mark.asyncio
async def test_one_failed_domain_does_not_stop_other_domains(tmp_path):
    store = ApplicationTrackerStore(tmp_path / "tracker.db")
    store.import_applications("u", [_row("A", "https://a.example/1"), _row("B", "https://b.example/1")])

    class Agent(FakeAgent):
        async def run(self, application, **kwargs):
            if application.company == "A":
                raise RuntimeError("synthetic failure")
            return await super().run(application, **kwargs)

    events = [e async for e in TrackerUpdateService(store=store, agent=Agent()).stream_refresh_all("u")]
    results = {e.company: e.application for e in events if e.type == "row_completed"}
    assert results["A"].check_result is CheckResult.FETCH_FAILED
    assert results["B"].check_result is CheckResult.SUCCESS
    assert events[-1].completed == 2
