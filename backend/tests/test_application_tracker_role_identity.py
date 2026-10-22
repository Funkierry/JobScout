"""Replay model title variations without merging distinct roles or owners."""

from datetime import UTC, datetime, timedelta

import pytest
from test_application_tracker_store import _record

from app.application_tracker.models import ApplicationInput, ApplicationStatus, DiscoveredApplication
from app.application_tracker.store import ApplicationTrackerStore


def discovery(role):
    return DiscoveredApplication(role=role, role_evidence=role, status="已投递", raw_status="已投递", confidence=0.9, evidence="已投递")


def test_rank_suffix_does_not_lose_registered_status_or_create_duplicate(tmp_path):
    store = ApplicationTrackerStore(tmp_path / "tracker.db")
    row = store.add_application("u", ApplicationInput(company="Example", role="AI 产品经理", url="https://jobs.example.com/applications"))
    record = _record(ApplicationStatus.UNKNOWN, checked_at=datetime(2026, 10, 1, tzinfo=UTC)).model_copy(update={"url": row.url, "company": row.company, "role": row.role, "discovered_applications": [discovery("AI 产品经理第 1 志愿")]})
    saved = store.save_check("u", row.id, record)
    assert saved.status.value == "已投递"
    assert len(store.list_applications("u")) == 1


def test_later_clean_title_reuses_child_on_same_portal_only(tmp_path):
    store = ApplicationTrackerStore(tmp_path / "tracker.db")
    url = "https://jobs.example.com/applications"
    root = store.add_application("u", ApplicationInput(company="Example", role="主岗位", url=url))
    record = _record(ApplicationStatus.UNKNOWN, checked_at=datetime(2026, 10, 1, tzinfo=UTC)).model_copy(update={"url": url, "company": root.company, "role": root.role, "discovered_applications": [discovery("副岗位第 2 志愿")]})
    store.save_check("u", root.id, record)
    other = store.add_application("other", ApplicationInput(company="Example", role="副岗位", url=url))
    elsewhere = store.add_application("u", ApplicationInput(company="Example", role="副岗位", url="https://jobs.example.com/another-application"))
    record = record.model_copy(update={"checked_at": record.checked_at + timedelta(days=1), "discovered_applications": [discovery("副岗位")]})
    store.save_check("u", root.id, record)
    assert len(store.list_applications("u")) == 3
    assert store.get_application("u", elsewhere.id).checked_at is None
    assert store.list_applications("other")[0].id == other.id
    assert store.list_applications("other")[0].checked_at is None


def test_role_alias_keeps_actual_title_words_and_rejects_ambiguity():
    from app.application_tracker.roles import role_key, select_role

    assert role_key("2027届产品经理第 2 志愿") == "2027届产品经理"
    assert role_key("第一志愿项目经理") == "第一志愿项目经理"
    assert role_key("工程师二级") == "工程师二级"
    assert select_role([discovery("产品经理第 1 志愿"), discovery("产品经理第 2 志愿")], "产品经理") is None


@pytest.mark.parametrize("primary_role", ["产品经理", "其他岗位"])
def test_ambiguous_badges_cannot_update_primary_through_child_alias(tmp_path, primary_role):
    store = ApplicationTrackerStore(tmp_path / "tracker.db")
    row = store.add_application("u", ApplicationInput(company="Example", role=primary_role, url="https://jobs.example.com/applications"))
    record = _record(ApplicationStatus.UNKNOWN, checked_at=datetime(2026, 10, 1, tzinfo=UTC)).model_copy(
        update={"url": row.url, "company": row.company, "role": row.role, "discovered_applications": [discovery("产品经理第 1 志愿"), discovery("产品经理第 2 志愿")]}
    )
    saved = store.save_check("u", row.id, record)
    assert saved.status is ApplicationStatus.UNKNOWN
    assert store.get_application("u", row.id).status is ApplicationStatus.UNKNOWN
    assert len(store.list_applications("u")) == 1
