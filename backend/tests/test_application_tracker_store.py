from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.application_tracker.models import (
    ApplicationInput,
    ApplicationStatus,
    CheckResult,
    StatusRecord,
)
from app.application_tracker.store import ApplicationTrackerStore


def _input(url: str = "https://jobs.example.com/applications/1") -> ApplicationInput:
    return ApplicationInput(
        company="Example Co",
        role="AI Product Manager",
        url=url,
        applied_at="2026-09-01",
        notes="Campus",
    )


def _record(
    status: ApplicationStatus,
    *,
    checked_at: datetime,
) -> StatusRecord:
    raw_status = status.value
    return StatusRecord(
        company="Example Co",
        role="AI Product Manager",
        url="https://jobs.example.com/applications/1",
        status=status,
        raw_status=raw_status,
        confidence=0.9,
        evidence=f"Current status: {raw_status}",
        checked_at=checked_at,
        changed_at=checked_at,
        check_result=CheckResult.SUCCESS,
    )


def test_store_imports_rows_and_isolates_users(tmp_path: Path) -> None:
    store = ApplicationTrackerStore(tmp_path / "tracker.db")

    summary = store.import_applications("user-1", [_input()])
    second_summary = store.import_applications(
        "user-1",
        [
            ApplicationInput(
                company="Renamed Co",
                role="Product Manager",
                url="https://jobs.example.com/applications/1",
                notes="Updated",
            )
        ],
    )

    rows = store.list_applications("user-1")
    assert summary.inserted == 1
    assert second_summary.updated == 1
    assert len(rows) == 1
    assert rows[0].company == "Renamed Co"
    assert rows[0].status is ApplicationStatus.UNKNOWN
    assert store.list_applications("user-2") == []


def test_store_records_every_check_and_marks_only_later_status_changes(
    tmp_path: Path,
) -> None:
    store = ApplicationTrackerStore(tmp_path / "tracker.db")
    store.import_applications("user-1", [_input()])
    application_id = store.list_applications("user-1")[0].id
    first_at = datetime(2026, 9, 20, 1, 0, tzinfo=UTC)
    second_at = datetime(2026, 9, 25, 1, 0, tzinfo=UTC)

    first = store.save_check(
        "user-1",
        application_id,
        _record(ApplicationStatus.RESUME_SCREENING, checked_at=first_at),
    )
    changed = store.save_check(
        "user-1",
        application_id,
        _record(ApplicationStatus.FIRST_INTERVIEW, checked_at=second_at),
    )

    history = store.list_checks("user-1", application_id)
    assert not first.changed
    assert first.previous_status is None
    assert changed.changed
    assert changed.previous_status is ApplicationStatus.RESUME_SCREENING
    assert changed.status is ApplicationStatus.FIRST_INTERVIEW
    assert len(history) == 2
    assert not history[0].status_changed
    assert history[1].status_changed
    assert history[1].old_status is ApplicationStatus.RESUME_SCREENING
    assert history[1].new_status is ApplicationStatus.FIRST_INTERVIEW


def test_store_does_not_expose_another_users_application(tmp_path: Path) -> None:
    store = ApplicationTrackerStore(tmp_path / "tracker.db")
    store.import_applications("user-1", [_input()])
    application_id = store.list_applications("user-1")[0].id

    assert store.get_application("user-2", application_id) is None
    assert store.list_checks("user-2", application_id) == []
