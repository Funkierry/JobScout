"""Synthetic regressions for JobScout's HTTP/event-loop boundaries."""

import asyncio
import threading
from contextlib import aclosing
from types import SimpleNamespace
from unittest.mock import AsyncMock

import anyio
import pytest

from app.application_tracker.models import ApplicationInput
from app.application_tracker.store import ApplicationTrackerStore
from app.gateway.routers import jobscout


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["list_tracker_applications", "list_opportunities", "get_tracker_stages", "tracker_notifications", "tracker_mail_events"])
async def test_repository_initialization_never_runs_on_request_loop(tmp_path, monkeypatch, endpoint):
    store = ApplicationTrackerStore(tmp_path / "fixture.db")
    loop_thread = threading.get_ident()

    def factory():
        assert threading.get_ident() != loop_thread, "SQLite initialization would block all Gateway requests"
        return store

    monkeypatch.setattr(jobscout, "_tracker_store", factory)
    monkeypatch.setattr(jobscout, "get_effective_user_id", lambda: "fixture")
    await getattr(jobscout, endpoint).__wrapped__(request=None)


@pytest.mark.asyncio
async def test_single_refresh_disconnect_cancels_agent_and_closes_resources(tmp_path, monkeypatch):
    store = ApplicationTrackerStore(tmp_path / "fixture.db")
    row = store.add_application("fixture", ApplicationInput(company="Fixture", role="Engineer", url="https://jobs.example.com/application"))
    entered, disconnected, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def run(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    agent = SimpleNamespace(run=run, aclose=AsyncMock())
    monkeypatch.setattr(jobscout, "_tracker_store", lambda: store)
    monkeypatch.setattr(jobscout, "_new_tracker_agent", lambda: agent)
    monkeypatch.setattr(jobscout, "get_effective_user_id", lambda: "fixture")
    request = SimpleNamespace(is_disconnected=AsyncMock(side_effect=lambda: disconnected.is_set()))
    task = asyncio.create_task(jobscout.refresh_tracker_application.__wrapped__(application_id=row.id, request=request))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        disconnected.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert cancelled.is_set()
        agent.aclose.assert_awaited_once()
        assert store.list_checks("fixture", row.id) == []
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_heartbeat_does_not_cancel_slow_refresh_and_closes_iterator():
    from app.application_tracker.tasks import keepalive_stream

    release, closed = asyncio.Event(), asyncio.Event()

    async def events():
        try:
            await release.wait()
            yield "finished"
        finally:
            closed.set()

    stream = keepalive_stream(events(), interval=0.01)
    try:
        assert await asyncio.wait_for(anext(stream), 2) is None
        assert not closed.is_set()
        release.set()
        assert await asyncio.wait_for(anext(stream), 2) == "finished"
    finally:
        await stream.aclose()
    assert closed.is_set()


@pytest.mark.asyncio
async def test_heartbeat_consumer_cancel_drains_pending_iteration():
    from app.application_tracker.tasks import keepalive_stream

    closed = asyncio.Event()

    async def events():
        try:
            await asyncio.Event().wait()
            yield "unreachable"
        finally:
            closed.set()

    stream = keepalive_stream(events(), interval=0.01)
    assert await asyncio.wait_for(anext(stream), 2) is None
    await stream.aclose()
    assert closed.is_set()


@pytest.mark.asyncio
async def test_http_batch_close_drains_workers_before_closing_launcher(tmp_path, monkeypatch):
    from app.application_tracker.update_service import ProgressEvent

    released = asyncio.Event()

    async def progress(_):
        try:
            yield ProgressEvent(type="batch_started")
            await asyncio.Event().wait()
        finally:
            released.set()

    async def close():
        assert released.is_set(), "Workers must stop before the Playwright launcher closes"

    monkeypatch.setattr(jobscout, "_tracker_store", lambda: ApplicationTrackerStore(tmp_path / "fixture.db"))
    monkeypatch.setattr(jobscout, "get_effective_user_id", lambda: "fixture")
    monkeypatch.setattr(jobscout, "_new_tracker_agent", lambda: SimpleNamespace(aclose=close))
    monkeypatch.setattr(jobscout, "TrackerUpdateService", lambda **_: SimpleNamespace(stream_refresh_all=progress))
    response = await jobscout.refresh_all_tracker_applications.__wrapped__(request=None)
    assert "batch_started" in await anext(response.body_iterator)
    await response.body_iterator.aclose()
    assert released.is_set()


@pytest.mark.asyncio
async def test_stream_cleanup_survives_asgi_cancellation_scope():
    from app.application_tracker.tasks import keepalive_stream

    entered, closed = asyncio.Event(), asyncio.Event()

    async def events():
        try:
            yield "started"
        finally:
            await anyio.lowlevel.checkpoint()
            closed.set()

    async def consume():
        async with aclosing(keepalive_stream(events())) as stream:
            async for _ in stream:
                entered.set()
                await anyio.sleep_forever()

    async with anyio.create_task_group() as group:
        group.start_soon(consume)
        await asyncio.wait_for(entered.wait(), 2)
        group.cancel_scope.cancel()
    assert closed.is_set()
