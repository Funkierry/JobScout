from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from app.application_tracker.models import (
    ApplicationInput,
    ApplicationStatus,
    CheckResult,
    DiscoveredApplication,
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


def test_opportunity_links_match_prep_and_tracker_without_crossing_users(tmp_path: Path) -> None:
    store = ApplicationTrackerStore(tmp_path / "tracker.db")
    opportunity = store.create_opportunity(
        "user-1",
        company="Example Co",
        role="AI PM",
        recruitment_type="社招",
        source_kind="feishu",
        source_url="https://example.feishu.cn/base/abc",
        source_table_id="tblJobs",
        source_record_id="rec1",
        jd_text="负责 Agent 产品",
    )
    duplicate = store.create_opportunity(
        "user-1",
        company="Example Co",
        role="AI PM",
        recruitment_type="社招",
        source_kind="feishu",
        source_url="https://example.feishu.cn/base/abc",
        source_table_id="tblJobs",
        source_record_id="rec1",
        jd_text="负责 Agent 产品",
    )
    application = store.add_application("user-1", _input())
    assert duplicate.id == opportunity.id
    assert store.link_opportunity_application("user-1", opportunity.id, application.id)
    assert store.link_opportunity_thread("user-1", opportunity.id, "prep-thread", "prep")
    assert store.link_opportunity_thread("user-1", opportunity.id, "match-thread", "match")
    linked = store.get_opportunity("user-1", opportunity.id)
    assert linked.application_ids == [application.id]
    assert linked.prep_thread_id == "prep-thread"
    assert linked.match_thread_id == "match-thread"
    assert store.list_opportunities("user-2") == []
    assert not store.link_opportunity_application("user-2", opportunity.id, application.id)
    assert not store.link_opportunity_thread("user-2", opportunity.id, "foreign-thread", "prep")


def test_application_can_only_link_one_opportunity_and_existing_rows_remain_unlinked(tmp_path: Path) -> None:
    store = ApplicationTrackerStore(tmp_path / "tracker.db")
    application = store.add_application("user-1", _input())
    first = store.create_opportunity("user-1", company="Example Co", role="AI PM")
    second = store.create_opportunity("user-1", company="Example Co", role="AI PM")
    assert store.get_opportunity("user-1", first.id).application_ids == []
    assert store.link_opportunity_application("user-1", first.id, application.id)
    assert store.link_opportunity_application("user-1", first.id, application.id)
    with pytest.raises(ValueError, match="already linked"):
        store.link_opportunity_application("user-1", second.id, application.id)


def test_match_candidates_survive_reload_and_stay_user_scoped(tmp_path: Path) -> None:
    store = ApplicationTrackerStore(tmp_path / "tracker.db")
    store.replace_match_candidates(
        "user-1",
        "match-thread",
        source_url="https://example.feishu.cn/base/abc",
        source_table_id="tblJobs",
        candidates=[{"record_id": "rec1", "company": "Example Co", "role": "AI PM", "jd_text": "Agent"}],
    )
    assert store.list_match_candidates("user-2", "match-thread") == []
    assert store.list_match_candidates("user-1", "match-thread")[0].record_id == "rec1"
    store.replace_match_candidates(
        "user-1",
        "match-thread",
        source_url="https://example.feishu.cn/base/abc",
        source_table_id="tblJobs",
        candidates=[{"record_id": "rec2", "company": "Example Co", "role": "Engineer", "jd_text": "Python"}],
    )
    assert [row.record_id for row in store.list_match_candidates("user-1", "match-thread")] == ["rec2"]


def test_store_migrates_existing_database_for_application_date_evidence(tmp_path: Path) -> None:
    path = tmp_path / "tracker.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE applications (id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL DEFAULT '')")

    ApplicationTrackerStore(path)

    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(applications)")}
    assert "applied_at_evidence" in columns


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


def test_store_writes_official_application_date_and_keeps_it_when_later_page_omits_it(tmp_path: Path) -> None:
    store = ApplicationTrackerStore(tmp_path / "tracker.db")
    row = store.add_application(
        "user-1",
        ApplicationInput(company="Example Co", role="AI Product Manager", url="https://jobs.example.com/applications/1"),
    )
    checked_at = datetime(2026, 9, 20, 1, 0, tzinfo=UTC)
    official = _record(ApplicationStatus.RESUME_SCREENING, checked_at=checked_at).model_copy(
        update={
            "applied_at": date(2026, 9, 10),
            "applied_at_evidence": "投递时间：2026-09-10",
        }
    )

    first = store.save_check("user-1", row.id, official)
    later = store.save_check(
        "user-1",
        row.id,
        _record(ApplicationStatus.RESUME_SCREENING, checked_at=datetime(2026, 9, 25, 1, 0, tzinfo=UTC)),
    )

    assert first.applied_at == date(2026, 9, 10)
    assert first.applied_at_evidence == "投递时间：2026-09-10"
    assert later.applied_at == date(2026, 9, 10)
    assert later.applied_at_evidence == "投递时间：2026-09-10"


def test_store_does_not_expose_another_users_application(tmp_path: Path) -> None:
    store = ApplicationTrackerStore(tmp_path / "tracker.db")
    store.import_applications("user-1", [_input()])
    application_id = store.list_applications("user-1")[0].id

    assert store.get_application("user-2", application_id) is None
    assert store.list_checks("user-2", application_id) == []


def test_discovered_roles_replace_unidentified_placeholder(tmp_path: Path) -> None:
    store = ApplicationTrackerStore(tmp_path / "tracker.db")
    source_url = "https://jobs.example.com/my-applications"
    placeholder = store.add_application("user-1", ApplicationInput(company="Example Co", role="待识别岗位", url=source_url))
    unrelated = store.add_application("user-1", ApplicationInput(company="Another Co", role="待识别岗位", url="https://other.example.com/applications"))
    checked_at = datetime(2026, 9, 27, 1, 0, tzinfo=UTC)
    record = StatusRecord(
        company="Example Co",
        role="待识别岗位",
        url=source_url,
        status=ApplicationStatus.UNKNOWN,
        confidence=0,
        checked_at=checked_at,
        changed_at=checked_at,
        check_result=CheckResult.SUCCESS,
        discovered_applications=[
            DiscoveredApplication(
                role="AI 产品经理",
                role_evidence="岗位：AI 产品经理",
                status=ApplicationStatus.RESUME_SCREENING,
                raw_status="简历筛选中",
                confidence=0.92,
                evidence="岗位：AI 产品经理 当前状态：简历筛选中",
                applied_at="2026-09-10",
                applied_at_evidence="岗位：AI 产品经理 投递时间：2026-09-10",
            ),
            DiscoveredApplication(
                role="数据平台实习生",
                role_evidence="岗位：数据平台实习生",
                status=ApplicationStatus.APPLIED,
                raw_status="已投递",
                confidence=0.91,
                evidence="岗位：数据平台实习生 当前状态：已投递",
                applied_at="2026-09-12",
                applied_at_evidence="岗位：数据平台实习生 投递时间：2026-09-12",
            ),
        ],
    )

    returned = store.save_check("user-1", placeholder.id, record)

    rows = store.list_applications("user-1")
    assert store.get_application("user-1", placeholder.id) is None
    assert {row.role for row in rows} == {"AI 产品经理", "数据平台实习生", "待识别岗位"}
    assert {row.role: row.status for row in rows if row.company == "Example Co"} == {
        "AI 产品经理": ApplicationStatus.RESUME_SCREENING,
        "数据平台实习生": ApplicationStatus.APPLIED,
    }
    assert store.get_application("user-1", unrelated.id) is not None
    assert returned.role in {"AI 产品经理", "数据平台实习生"}
    assert {row.role: row.applied_at for row in rows if row.company == "Example Co"} == {
        "AI 产品经理": date(2026, 9, 10),
        "数据平台实习生": date(2026, 9, 12),
    }
    assert len(store.list_checks("user-1", returned.id)) == 1


def test_unidentified_placeholder_remains_without_discovered_role(tmp_path: Path) -> None:
    store = ApplicationTrackerStore(tmp_path / "tracker.db")
    placeholder = store.add_application("user-1", ApplicationInput(company="Example Co", role="待识别岗位", url="https://jobs.example.com/my-applications"))
    checked_at = datetime(2026, 9, 27, 1, 0, tzinfo=UTC)
    record = StatusRecord(
        company=placeholder.company,
        role=placeholder.role,
        url=placeholder.url,
        status=ApplicationStatus.UNKNOWN,
        confidence=0,
        checked_at=checked_at,
        changed_at=checked_at,
        check_result=CheckResult.SUCCESS,
    )

    returned = store.save_check("user-1", placeholder.id, record)

    assert returned.id == placeholder.id
    assert returned.role == "待识别岗位"
