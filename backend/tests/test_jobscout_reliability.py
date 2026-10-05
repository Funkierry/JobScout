"""Regression cases from the repository review; synthetic data only."""

import asyncio
import csv
import io
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.application_tracker.csv_import import parse_application_csv
from app.application_tracker.models import ApplicationInput, ApplicationStatus, CheckResult, StatusRecord
from app.application_tracker.store import ApplicationTrackerStore


def make_record(app, minute=10, status=ApplicationStatus.SECOND_INTERVIEW):
    stamp = datetime(2026, 10, 5, 3, minute, tzinfo=UTC)
    return StatusRecord(company=app.company, role=app.role, url=app.url, status=status, raw_status=status.value, confidence=0.9, evidence=f"{app.role}: {status.value}", checked_at=stamp, changed_at=stamp, check_result=CheckResult.SUCCESS)


@pytest.fixture
def stored(tmp_path):
    store = ApplicationTrackerStore(tmp_path / "tracker.db")
    app = store.add_application("review", ApplicationInput(company="Example", role="Backend", url="https://jobs.example.com/one", notes="Keep this note"))
    return store, app


def test_late_check_cannot_rewind_status_or_create_notification(stored):
    from app.application_tracker.store import StaleCheckError

    store, app = stored
    store.save_check("review", app.id, make_record(app))
    before = store.list_checks("review", app.id)
    with pytest.raises(StaleCheckError):
        store.save_check("review", app.id, make_record(app, 5, ApplicationStatus.FIRST_INTERVIEW))
    assert store.get_application("review", app.id).status is ApplicationStatus.SECOND_INTERVIEW
    assert store.list_checks("review", app.id) == before


def test_edited_identity_rejects_old_evidence(stored):
    from app.application_tracker.store import StaleCheckError

    store, app = stored
    store.update_application("review", app.id, {"role": "Frontend"})
    with pytest.raises(StaleCheckError):
        store.save_check("review", app.id, make_record(app))
    assert store.get_application("review", app.id).evidence == ""


def test_revision_rejects_edit_away_and_back(stored):
    from app.application_tracker.store import StaleCheckError

    store, app = stored
    store.update_application("review", app.id, {"role": "Frontend"})
    store.update_application("review", app.id, {"role": "Backend"})
    with pytest.raises(StaleCheckError):
        store.save_check("review", app.id, make_record(app), expected_revision=app.revision)


@pytest.mark.asyncio
async def test_csv_export_is_importable_and_preserves_formula_text(stored, monkeypatch):
    from app.gateway.routers import jobscout

    store, app = stored
    store.update_application("review", app.id, {"company": "=1+1"})
    monkeypatch.setattr(jobscout, "_get_tracker_store", AsyncMock(return_value=store))
    monkeypatch.setattr(jobscout, "get_effective_user_id", lambda: "review")
    response = await jobscout.export_tracker_csv.__wrapped__(request=None)
    cell = list(csv.reader(io.StringIO(response.body.decode("utf-8-sig"))))[1][0]
    assert not cell.startswith("=")
    imported = parse_application_csv(response.body)
    assert imported[0].company == "=1+1"
    assert imported[0].notes == "Keep this note"


def test_connections_close_after_transaction(stored):
    import sqlite3

    store, _ = stored
    with store._connect() as connection:
        assert connection.execute("SELECT 1").fetchone()[0] == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


@pytest.mark.asyncio
async def test_overlapping_refresh_requests_share_completed_work(stored, monkeypatch):
    from app.application_tracker.update_service import TrackerUpdateService

    store, app = stored
    entered, finish, second_read = asyncio.Event(), asyncio.Event(), asyncio.Event()
    loop = asyncio.get_running_loop()
    original_get = store.get_application

    def read(*args):
        result = original_get(*args)
        if entered.is_set():
            loop.call_soon_threadsafe(second_read.set)
        return result

    monkeypatch.setattr(store, "get_application", read)
    calls = []

    class Agent:
        async def run(self, application, **kwargs):
            calls.append(application)
            entered.set()
            await finish.wait()
            return make_record(app)

    first = TrackerUpdateService(store=store, agent=Agent())
    second = TrackerUpdateService(store=store, agent=Agent())
    task = asyncio.create_task(first.refresh_one("review", app.id))
    await entered.wait()
    waiting = asyncio.create_task(second.refresh_one("review", app.id))
    try:
        await asyncio.wait_for(second_read.wait(), 5)
    finally:
        finish.set()
        results = await asyncio.gather(task, waiting)
    assert len(calls) == 1
    assert results[1].skipped and results[1].reason == "already_refreshed"


def test_opportunity_list_uses_bounded_queries_and_preserves_links(stored, monkeypatch):
    store, application = stored
    for index in range(12):
        opportunity = store.create_opportunity("owner", company="Example", role=f"Role {index}")
        store.link_opportunity_thread("owner", opportunity.id, f"thread-{index}", "prep")
    other = store.create_opportunity("other", company="Private", role="Other")
    store.link_opportunity_thread("other", other.id, "private-thread", "prep")
    statements = []
    connect = store._connect

    def traced():
        connection = connect()
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(store, "_connect", traced)
    rows = store.list_opportunities("owner")
    assert len(rows) == 12 and all(row.prep_thread_id.startswith("thread-") for row in rows)
    assert len([sql for sql in statements if sql.lstrip().upper().startswith("SELECT")]) <= 3
