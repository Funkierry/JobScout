"""Generic progress is distinct from a confirmed stage, including legacy data."""

from datetime import UTC, datetime, timedelta

import pytest

from app.application_tracker.confidence import assess_status
from app.application_tracker.models import ApplicationInput, ApplicationStatus, CheckResult, StatusRecord
from app.application_tracker.observations import SourceObservation
from app.application_tracker.status_semantics import normalize_generic_active_status
from app.application_tracker.store import ApplicationTrackerStore

GENERIC = "进行中·具体阶段未知"


@pytest.mark.parametrize("phrase", ["进行中", "处理中", "审核中", "待审核", "in progress"])
@pytest.mark.parametrize("old", [ApplicationStatus.UNKNOWN, ApplicationStatus.RESUME_SCREENING])
def test_generic_progress_never_claims_resume_screening(phrase, old):
    status, confidence = normalize_generic_active_status(old, raw_status=phrase, evidence=f"当前申请状态：{phrase}", confidence=1)
    assert status.value == GENERIC
    assert confidence <= 0.75


def test_explicit_resume_screening_is_preserved():
    status, _ = normalize_generic_active_status(ApplicationStatus.RESUME_SCREENING, raw_status="简历审核中", evidence="当前申请状态：简历审核中", confidence=0.9)
    assert status is ApplicationStatus.RESUME_SCREENING


def test_explicit_resume_stage_in_evidence_overrides_generic_raw_word():
    status, _ = normalize_generic_active_status(ApplicationStatus.RESUME_SCREENING, raw_status="审核中", evidence="当前申请状态：简历审核中", confidence=0.9)
    assert status is ApplicationStatus.RESUME_SCREENING


def test_rules_accept_generic_but_do_not_confuse_it_with_specific_stages():
    text = "岗位：Python 工程师\n当前申请状态：进行中"
    result = assess_status(ApplicationStatus.UNKNOWN, raw_status="进行中", evidence=text, role="Python 工程师", sources=[SourceObservation(kind="dom", text=text)])
    assert result.status.value == GENERIC


def test_legacy_reclassification_is_idempotent_and_preserves_manual_stage_and_history(tmp_path):
    path = tmp_path / "tracker.db"
    store = ApplicationTrackerStore(path)
    now = datetime(2026, 10, 21, tzinfo=UTC)
    ids = []
    for owner in ("owner", "other"):
        row = store.add_application(owner, ApplicationInput(company="Example", role="Python 工程师", url=f"https://example.test/{owner}"))
        store.save_check(
            owner,
            row.id,
            StatusRecord(
                company=row.company,
                role=row.role,
                url=row.url,
                status=ApplicationStatus.RESUME_SCREENING,
                raw_status="进行中",
                evidence="当前申请状态：进行中",
                confidence=0.75,
                checked_at=now,
                changed_at=now,
                check_result=CheckResult.SUCCESS,
            ),
        )
        # Simulate a persisted pre-fix classification, independent of new write validation.
        with store._connect() as connection:
            connection.execute("UPDATE applications SET status='简历筛选',stage='简历筛选' WHERE id=?", (row.id,))
        ids.append(row.id)
    store.replace_stages("owner", [*store.list_stages("owner"), "自己准备材料"])
    store.update_application("owner", ids[0], {"stage": "自己准备材料"})
    history = store.list_checks("owner", ids[0])
    old = store.get_application("owner", ids[0])
    fixed = ApplicationTrackerStore(path)
    row = fixed.get_application("owner", ids[0])
    assert row.status.value == GENERIC and row.stage == "自己准备材料" and row.stage_manual
    assert row.revision == old.revision + 1
    assert row.checked_at == old.checked_at and row.changed_at == old.changed_at
    assert fixed.list_checks("owner", ids[0]) == history
    assert fixed.get_application("other", ids[1]).stage == GENERIC
    page = fixed.list_applications_page("other", stage=GENERIC)
    assert page["filtered_total"] == 1 and page["summary"]["review"] == 1
    again = ApplicationTrackerStore(path)
    assert again.get_application("owner", ids[0]).revision == row.revision
    refreshed = again.save_check(
        "owner",
        row.id,
        StatusRecord(
            company=row.company,
            role=row.role,
            url=row.url,
            status=ApplicationStatus(GENERIC),
            raw_status=row.raw_status,
            evidence=row.evidence,
            confidence=0.75,
            checked_at=now + timedelta(minutes=1),
            changed_at=now,
            check_result=CheckResult.SUCCESS,
        ),
    )
    assert not refreshed.changed
    with again._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobscout_notifications").fetchone()[0] == 0
