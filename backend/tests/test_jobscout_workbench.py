"""Synthetic action/feedback persistence and derived workbench contracts."""

import pytest

from app.application_tracker.models import ApplicationInput
from app.application_tracker.store import ApplicationTrackerStore
from app.application_tracker.workbench import FeedbackInput, PlanInput, WorkbenchStore, WriteConflict, pending_application


def test_preparation_actions_keep_quotes_and_inferences_separate():
    from datetime import date

    from app.jobscout.evidence import ResearchEvidence
    from app.jobscout.render import render_prep

    evidence = ResearchEvidence(as_of=date(2026, 10, 22))
    report = render_prep({}, [{"section": "gap", "claim": "需补充经验", "quote": "岗位要求 Python", "resume_quote": "使用 Python 分析销售数据", "url": "https://example.com/jd", "source_kind": "page"}], evidence)
    assert "### 准备行动" in report
    assert "使用 Python 分析销售数据" in report.split("### 准备行动")[1]
    assert "建议动作，不是公司面试安排" in report


@pytest.fixture
def workspace(tmp_path):
    return WorkbenchStore(ApplicationTrackerStore(tmp_path / "tracker.db"))


def test_plan_is_read_only_until_saved_and_isolated_by_owner_and_report(workspace):
    key = "a" * 64
    assert workspace.plan("alice", "thread", key)["revision"] == 0
    saved = workspace.save_plan("alice", "thread", key, PlanInput(revision=0, experience="真实项目经历", done=["experience"]))
    assert saved["revision"] == 1
    assert workspace.plan("bob", "thread", key)["experience"] == ""
    assert workspace.plan("alice", "thread", "b" * 64)["done"] == []
    assert WorkbenchStore(ApplicationTrackerStore(workspace.tracker.path)).plan("alice", "thread", key) == saved
    with pytest.raises(WriteConflict):
        workspace.save_plan("alice", "thread", key, PlanInput(revision=0, experience="旧标签页"))
    # Lost-response retries are idempotent, not extra completions/revisions.
    assert workspace.save_plan("alice", "thread", key, PlanInput(revision=0, experience="真实项目经历", done=["experience"])) == saved


def test_feedback_retry_and_owner_isolation(workspace):
    body = FeedbackInput(request_id="request-001", category="correction", original="进行中", correction="具体阶段未知", reason="官网没有说明简历筛选", next_check="宽泛状态不能当作筛选")
    first = workspace.add_feedback("alice", body)
    assert workspace.add_feedback("alice", body) == first
    assert workspace.feedback("bob") == []
    with pytest.raises(WriteConflict):
        workspace.add_feedback("alice", body.model_copy(update={"reason": "不同反馈"}))
    assert len(workspace.feedback("alice")) == 1
    with pytest.raises(ValueError):
        FeedbackInput(request_id="request-002", category="correction", reason=" ")


@pytest.mark.parametrize("change,kind", [({"source_summary": {"conflict": True}}, "conflict"), ({"check_result": "需登录"}, "login"), ({"stage": "一面", "stage_manual": True}, "interview"), ({"status": "进行中·具体阶段未知"}, "evidence")])
def test_pending_priority_does_not_invent_deadlines(change, kind):
    row = dict(id=1, company="样例", role="产品", status="未知", stage="", stage_manual=False, confidence=0, checked_at=None, check_result=None, source_summary=None)
    item = pending_application({**row, **change})
    assert item["kind"] == kind
    assert "deadline" not in item
    assert pending_application({**row, "status": "流程终止"}) is None


def test_pending_covers_all_pages_and_keeps_sources_unchanged(workspace):
    for n in range(55):
        workspace.tracker.add_application("alice", ApplicationInput(company=f"公司{n}", role="产品", url=f"https://example.com/{n}"))
    workspace.tracker.add_application("bob", ApplicationInput(company="私有公司", role="产品", url="https://example.com/b"))
    first = workspace.pending("alice", limit=20)
    assert first["total"] == 55
    assert len(first["items"]) == 20
    assert first["has_more"]
    assert "私有公司" not in str(first)
    before = workspace.tracker.list_applications("alice")
    workspace.add_feedback("alice", FeedbackInput(request_id="request-003", category="trial", reason="找不到入口", outcome="blocked", correction="已记录阻塞"))
    assert workspace.tracker.list_applications("alice") == before


def test_feedback_cannot_reference_another_users_application(workspace):
    row = workspace.tracker.add_application("bob", ApplicationInput(company="私有公司", role="产品", url="https://example.com/b"))
    with pytest.raises(ValueError):
        workspace.add_feedback("alice", FeedbackInput(request_id="request-004", category="correction", application_id=row.id, reason="纠错"))
    assert workspace.feedback("alice") == []


def test_thread_cleanup_removes_only_its_notes_and_feedback(workspace):
    for owner in ("alice", "bob"):
        workspace.save_plan(owner, "thread", "a" * 64, PlanInput(revision=0, materials="待补简历"))
        workspace.add_feedback(owner, FeedbackInput(request_id="request-005", category="report", thread_id="thread", reason="来源不足"))
    workspace.tracker.begin_thread_deletion("alice", "thread")
    workspace.tracker.finish_thread_deletion("alice", "thread")
    assert workspace.plan("alice", "thread", "a" * 64)["revision"] == 0
    assert workspace.feedback("alice") == []
    assert workspace.plan("bob", "thread", "a" * 64)["revision"] == 1
    assert len(workspace.feedback("bob")) == 1


@pytest.mark.asyncio
async def test_routes_check_thread_owner_before_read_or_write(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from fastapi import HTTPException

    from app.gateway.routers import jobscout

    monkeypatch.setattr(jobscout, "get_effective_user_id", lambda: "alice")
    monkeypatch.setattr(jobscout, "get_thread_store", lambda _: SimpleNamespace(get=AsyncMock(return_value=None)))
    database = AsyncMock()
    monkeypatch.setattr(jobscout, "_get_tracker_store", database)
    for route, kwargs in [
        (jobscout.get_preparation, dict(thread_id="private-thread", report_key="a" * 64)),
        (jobscout.save_preparation, dict(thread_id="private-thread", report_key="a" * 64, body=PlanInput(revision=0))),
        (jobscout.add_workbench_feedback, dict(body=FeedbackInput(request_id="request-006", category="report", thread_id="private-thread", reason="反馈"))),
    ]:
        with pytest.raises(HTTPException) as error:
            await route.__wrapped__(request=None, **kwargs)
        assert error.value.status_code == 404
    database.assert_not_awaited()
