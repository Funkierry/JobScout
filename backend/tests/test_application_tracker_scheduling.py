"""Deterministic scheduler, limits and transactional notifications."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from app.application_tracker.models import ApplicationInput, ApplicationStatus, CheckResult, StatusRecord
from app.application_tracker.scheduling import RefreshSettings, SchedulingStore, run_scheduled_refresh
from app.application_tracker.store import ApplicationTrackerStore

NOW = datetime(2026, 10, 4, 12, tzinfo=UTC)


def setup_store(tmp_path):
    tracker = ApplicationTrackerStore(tmp_path / "tracker.db")
    rows = [tracker.add_application("u", ApplicationInput(company="Example", role=f"Role {i}", url=f"https://jobs.example.com/{i}")) for i in range(4)]
    return tracker, SchedulingStore(tracker), rows


def record(row, status, *, now=NOW, check=CheckResult.SUCCESS):
    return StatusRecord(company=row.company, role=row.role, url=row.url, status=status, raw_status=status.value, evidence=f"Current: {status.value}", confidence=1, checked_at=now, changed_at=now, check_result=check)


def test_atomic_budget_survives_new_store_and_limits_concurrent_reservations(tmp_path):
    tracker, store, rows = setup_store(tmp_path)
    store.configure("u", RefreshSettings(enabled=True, daily_limit=2))
    with ThreadPoolExecutor(max_workers=4) as pool:
        accepted = list(pool.map(lambda row: store.reserve("u", "occurrence-1", row.id, now=NOW), rows))
    assert sum(accepted) == 2
    other = SchedulingStore(ApplicationTrackerStore(tracker.path))
    assert not other.reserve("u", "occurrence-2", rows[0].id, now=NOW)
    assert other.reserve("u", "tomorrow", rows[0].id, now=NOW + timedelta(days=1))


def test_occurrence_dedupe_disabled_user_and_foreign_application(tmp_path):
    tracker, store, rows = setup_store(tmp_path)
    assert not store.reserve("u", "one", rows[0].id, now=NOW)
    store.configure("u", RefreshSettings(enabled=True))
    assert store.reserve("u", "one", rows[0].id, now=NOW)
    assert not store.reserve("u", "one", rows[0].id, now=NOW + timedelta(days=1))
    assert not store.reserve("other", "one", rows[0].id, now=NOW)
    assert not store.reserve("u", "two", rows[0].id, now=NOW + timedelta(minutes=5))


def test_notifications_only_for_confirmed_change_and_are_owner_scoped(tmp_path):
    tracker, store, rows = setup_store(tmp_path)
    row = rows[0]
    tracker.save_check("u", row.id, record(row, ApplicationStatus.APPLIED))
    assert not store.notifications("u")
    tracker.save_check("u", row.id, record(row, ApplicationStatus.FIRST_INTERVIEW, now=NOW + timedelta(days=1)))
    tracker.save_check("u", row.id, record(row, ApplicationStatus.FIRST_INTERVIEW, now=NOW + timedelta(days=2)))
    notices = store.notifications("u")
    assert len(notices) == 1 and notices[0]["old_status"] == "已投递" and notices[0]["new_status"] == "一面"
    assert "一面" in notices[0]["evidence"]
    assert not store.notifications("other")
    assert not store.mark_read("other", notices[0]["id"])
    assert store.mark_read("u", notices[0]["id"])
    assert store.notifications("u")[0]["read"]
    tracker.save_check("u", row.id, record(row, ApplicationStatus.REJECTED, check=CheckResult.FETCH_FAILED, now=NOW + timedelta(days=3)))
    tracker.save_check("u", row.id, record(row, ApplicationStatus.UNKNOWN, now=NOW + timedelta(days=4)))
    assert len(store.notifications("u")) == 1


@pytest.mark.asyncio
async def test_scheduled_refresh_skips_terminal_and_cleans_agent_under_budget(tmp_path):
    tracker, store, rows = setup_store(tmp_path)
    tracker.save_check("u", rows[0].id, record(rows[0], ApplicationStatus.OFFER))
    store.configure("u", RefreshSettings(enabled=True, daily_limit=1))
    calls = []

    class Agent:
        async def run(self, application, **kwargs):
            assert kwargs["interactive_login"] is False
            calls.append(application.url)
            row = next(row for row in rows if row.url == application.url)
            return record(row, ApplicationStatus.RESUME_SCREENING)

        async def aclose(self):
            calls.append("closed")

    result = await run_scheduled_refresh(tracker, "u", "one", agent_factory=lambda settings: Agent(), now=NOW)
    assert result["checked"] == 1 and len(calls) == 2 and calls[-1] == "closed"
    assert rows[0].url not in calls


@pytest.mark.asyncio
async def test_disabled_schedule_does_not_construct_agent(tmp_path):
    tracker, _, _ = setup_store(tmp_path)
    result = await run_scheduled_refresh(tracker, "u", "one", agent_factory=lambda _: pytest.fail("must remain off"), now=NOW)
    assert result["checked"] == 0


@pytest.mark.asyncio
async def test_cancelled_run_keeps_budget_and_closes_agent(tmp_path):
    tracker, store, rows = setup_store(tmp_path)
    store.configure("u", RefreshSettings(enabled=True, daily_limit=1))
    started = asyncio.Event()
    closed = []

    class Agent:
        async def run(self, *args, **kwargs):
            started.set()
            await asyncio.Event().wait()

        async def aclose(self):
            closed.append(True)

    task = asyncio.create_task(run_scheduled_refresh(tracker, "u", "one", agent_factory=lambda _: Agent(), now=NOW))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed and not store.reserve("u", "two", rows[-1].id, now=NOW)


@pytest.mark.asyncio
async def test_graph_rejects_forged_external_context(monkeypatch, tmp_path):
    from app.application_tracker.scheduled_graph import build_refresh_graph

    monkeypatch.setenv("JOBSCOUT_SCHEDULED_REFRESH_ENABLED", "1")
    graph = build_refresh_graph(execute=lambda *args: pytest.fail("untrusted request"))
    with pytest.raises(PermissionError):
        await graph.ainvoke({"messages": []}, context={"user_id": "u", "is_internal": False, "non_interactive": True})


@pytest.mark.asyncio
async def test_tracker_no_model_mode_never_initializes_fallback(monkeypatch, tmp_path):
    from test_application_tracker_agent_workflow import FakeBrowser, FakeBrowserFactory

    from app.application_tracker.agent.workflow import AgentRunConfig, ApplicationTrackerAgent
    from app.application_tracker.browser.models import BrowserAccessConfig

    monkeypatch.setattr("deerflow.models.create_chat_model", lambda **kwargs: pytest.fail("model cost forbidden"))
    browser = FakeBrowser(page_text="No known status")
    agent = ApplicationTrackerAgent.from_model_name(browser_factory=FakeBrowserFactory(browser), run_config=AgentRunConfig(allow_model_fallback=False))
    result = await agent.run(ApplicationInput(company="Example", role="Role", url="https://jobs.example.com/a"), user_id="u", browser_config=BrowserAccessConfig(profile_root=tmp_path))
    assert result.check_result is CheckResult.FETCH_FAILED and browser.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["full", "delta"])
async def test_graph_runs_only_owned_scheduler_occurrence(monkeypatch, mode):
    from app.application_tracker.scheduled_graph import build_refresh_graph
    from app.application_tracker.scheduling import task_id_for

    monkeypatch.setenv("JOBSCOUT_SCHEDULED_REFRESH_ENABLED", "1")
    calls = []

    async def execute(owner, occurrence):
        calls.append((owner, occurrence))
        return {"checked": 1, "failed": 0, "skipped": 2}

    graph = build_refresh_graph(execute=execute, mode=mode, snapshot_frequency=20)
    context = {"user_id": "u", "is_internal": True, "non_interactive": True}
    config = {"metadata": {"scheduled_task_id": task_id_for("u"), "scheduled_task_run_id": "one"}}
    result = await graph.ainvoke({"messages": []}, config=config, context=context)
    assert calls == [("u", "one")] and len(result["messages"]) == 1
    config["metadata"]["scheduled_task_id"] = task_id_for("other")
    with pytest.raises(PermissionError):
        await graph.ainvoke({"messages": []}, config=config, context=context)
    monkeypatch.delenv("JOBSCOUT_SCHEDULED_REFRESH_ENABLED")
    with pytest.raises(PermissionError):
        await graph.ainvoke({"messages": []}, config=config, context=context)


@pytest.mark.asyncio
async def test_schedule_routes_use_native_repository_and_gate(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from fastapi import HTTPException

    from app.gateway import deps
    from app.gateway.routers import jobscout

    tracker, store, _ = setup_store(tmp_path)
    repo = SimpleNamespace(get=AsyncMock(return_value=None), create=AsyncMock(), update=AsyncMock(), pause_with_queue_cancellation=AsyncMock(return_value="paused"))
    monkeypatch.setattr(jobscout, "_tracker_store", lambda: tracker)
    monkeypatch.setattr(jobscout, "get_effective_user_id", lambda: "u")
    monkeypatch.setattr(deps, "get_scheduled_task_repo", lambda _: repo)
    monkeypatch.setattr(deps, "get_config", lambda: SimpleNamespace(scheduler=SimpleNamespace(enabled=True, min_once_delay_seconds=60)))
    monkeypatch.delenv("JOBSCOUT_SCHEDULED_REFRESH_ENABLED", raising=False)
    with pytest.raises(HTTPException) as error:
        await jobscout.configure_tracker_schedule.__wrapped__(RefreshSettings(enabled=True), request=None)
    assert error.value.status_code == 409 and not store.settings("u").enabled
    monkeypatch.setenv("JOBSCOUT_SCHEDULED_REFRESH_ENABLED", "1")
    await jobscout.configure_tracker_schedule.__wrapped__(RefreshSettings(enabled=True, interval_minutes=120), request=None)
    assert repo.create.await_args.kwargs["assistant_id"] == "jobscout-tracker-refresh"
    assert repo.create.await_args.kwargs["schedule_spec"] == {"every_seconds": 7200}
    assert store.settings("u").enabled
    repo.get.return_value = {"assistant_id": "jobscout-tracker-refresh", "status": "enabled"}
    repo.pause_with_queue_cancellation.return_value = "executing"
    with pytest.raises(HTTPException):
        await jobscout.configure_tracker_schedule.__wrapped__(RefreshSettings(enabled=False), request=None)
    assert not store.settings("u").enabled
    repo.pause_with_queue_cancellation.return_value = "paused"
    await jobscout.configure_tracker_schedule.__wrapped__(RefreshSettings(enabled=False), request=None)
    assert repo.pause_with_queue_cancellation.await_args.kwargs["user_id"] == "u"


def test_mail_notifies_only_resolved_changed_status(tmp_path):
    from test_application_tracker_email import mail
    from test_application_tracker_email import setup_store as mail_store

    from app.application_tracker.email.extract import extract_event

    tracker, ledger, row = mail_store(tmp_path)
    tracker.save_check("u", row.id, record(row, ApplicationStatus.APPLIED, now=NOW - timedelta(days=1)))
    message = mail()
    event = extract_event(message, tracker.list_applications("u"))
    assert ledger.save("u", message, event)
    assert not ledger.save("u", message, event)
    notices = SchedulingStore(tracker).notifications("u")
    assert len(notices) == 1 and notices[0]["source"] == "email"
    assert notices[0]["new_status"] == "笔试" and notices[0]["evidence"] == event.quote


def test_invalid_timezone_is_validation_error():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RefreshSettings(timezone="invalid/zone")
