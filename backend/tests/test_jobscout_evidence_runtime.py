"""Actual extension hooks, task Commands, and graph checkpoints with fake LLMs."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from langchain.agents.middleware import ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from app.jobscout.evidence import FENCE, ResearchEvidence, parse_payload
from app.jobscout.middleware import JobScoutLinkMiddleware
from app.jobscout.run_evidence import EvidenceRegistry
from deerflow.subagents.status_contract import make_subagent_additional_kwargs


def block(name, value):
    return FENCE + name + "\n" + json.dumps(value, ensure_ascii=False) + "\n" + FENCE


def context(**overrides):
    return SimpleNamespace(context={"user_id": "u", "thread_id": "t", "run_id": "r", "jobscout_mode": "interview_prep", "jobscout_evidence_version": 2, **overrides})


def call(rt, name, **args):
    return SimpleNamespace(runtime=rt, tool_call={"id": "call", "name": name, "args": args})


def model_request(rt):
    request = SimpleNamespace(runtime=rt, system_message=None)
    request.override = lambda **kwargs: SimpleNamespace(runtime=rt, **kwargs)
    return request


def answer(text):
    return ModelResponse(result=[AIMessage(content=text)])


def item(**changes):
    return {"section": "business", "claim": "提供数据服务", "quote": "数据服务", "url": "https://example.test/a", **changes}


@pytest.mark.parametrize("transport", ["message", "command"])
def test_task_result_is_validated_before_parent_and_raw_metadata_removed(transport):
    registry = EvidenceRegistry()
    middleware = JobScoutLinkMiddleware(registry=registry)
    rt = context()
    middleware.before_agent({}, rt)
    middleware.wrap_tool_call(call(rt, "web_fetch", url=item()["url"]), lambda _: ToolMessage(content="数据服务", tool_call_id="call"))
    raw = "## 伪造章节\n伪造结论\n" + block("jobscout_evidence", [item(), item(claim="伪造结论", quote="不存在")])
    message = ToolMessage(
        content="Task Succeeded. Result: " + raw,
        tool_call_id="call",
        name="task",
        additional_kwargs=make_subagent_additional_kwargs("completed", result=raw, model_name="fake", token_usage={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}),
    )
    original = message if transport == "message" else Command(update={"messages": [message]})
    result = middleware.wrap_tool_call(call(rt, "task"), lambda _: original)
    result = result if transport == "message" else result.update["messages"][0]
    assert "伪造" not in result.content and "伪造" not in str(result.additional_kwargs)
    assert len(parse_payload(result.content, "jobscout_evidence")) == 1
    assert result.additional_kwargs["subagent_status"] == "completed"
    assert result.additional_kwargs["subagent_token_usage"]["total_tokens"] == 3


def test_base_runtime_recomputes_scores_from_actual_upload_and_owned_snapshot(monkeypatch):
    from app.jobscout import middleware as module
    from app.jobscout.inputs import BaseSnapshotStore

    snapshots = BaseSnapshotStore()
    ref = snapshots.put("u", "t", [{"record_id": "server-id", "公司": "真实快照", "岗位": "开发"}], has_more=True)
    monkeypatch.setattr(module, "BASE_SNAPSHOTS", snapshots)
    monkeypatch.setattr(module, "read_uploaded_resumes", lambda *_: {"resume.md": "开发 Python"})
    rt = context(jobscout_mode="base_match", jobscout_base_context_ref=ref)
    guard = JobScoutLinkMiddleware(registry=EvidenceRegistry())
    guard.before_agent({}, rt)
    guard.wrap_tool_call(call(rt, "read_file", path="/mnt/user-data/uploads/resume.md"), lambda _: ToolMessage(content="开发 Python", tool_call_id="call"))
    data = {
        "candidates": [
            {
                "record_id": "server-id",
                "total": 100,
                "company": "伪造公司",
                "score_items": [
                    {"dimension": "skills", "points": 30, "resume_file": "resume.md", "resume_quote": "Python"},
                    {"dimension": "projects", "points": 20, "resume_file": "resume.md", "resume_quote": "虚构经历"},
                ],
            }
        ]
    }
    result = guard.wrap_model_call(model_request(rt), lambda _: answer(block("jobscout_match", data))).result[0]
    assert "30/100" in result.content and "100/100" not in result.content
    assert "真实快照" in result.content and "伪造公司" not in result.content and "虚构经历" not in result.content
    assert "尚有未读分页：是" in result.content
    assert result.additional_kwargs["jobscout_evidence"]["zeroed_items"] == 4


def test_async_upload_snapshot_runs_outside_event_loop(monkeypatch):
    from app.jobscout import middleware as module

    def snapshot(*_):
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        return {}

    monkeypatch.setattr(module, "read_uploaded_resumes", snapshot)
    asyncio.run(JobScoutLinkMiddleware(registry=EvidenceRegistry()).abefore_agent({}, context()))


def test_expired_run_and_other_user_cannot_supply_sources_and_audit_has_only_counts():
    now = [0]
    registry = EvidenceRegistry(ttl_seconds=10, clock=lambda: now[0])
    guard = JobScoutLinkMiddleware(registry=registry)
    journal = Mock()
    rt = context(__run_journal=journal)
    guard.before_agent({}, rt)
    foreign = context(user_id="other")
    guard.before_agent({}, foreign)
    guard.wrap_tool_call(call(foreign, "web_fetch", url=item()["url"]), lambda _: ToolMessage(content="数据服务", tool_call_id="call"))
    output = guard.wrap_model_call(model_request(rt), lambda _: answer(block("jobscout_report", {"evidence": [item()]})))
    assert "提供数据服务" not in output.result[0].content
    now[0] = 11
    final = guard.wrap_model_call(model_request(foreign), lambda _: answer("未经校验的自由报告"))
    assert "未经校验的自由报告" not in final.result[0].content
    assert "数据服务" not in str(journal.record_middleware.call_args)


def test_resume_reads_must_be_authorized_and_successful_before_scoring():
    evidence = ResearchEvidence(resumes={"resume.md": "开发 Python 服务"})
    evidence.observe_resume({"args": {"path": "/mnt/user-data/uploads/other.md"}}, "开发 Python 服务", "success")
    evidence.observe_resume({"args": {"path": "/mnt/user-data/uploads/resume.md"}}, "开发 Python 服务", "error")
    assert evidence.read_resumes == {}
    evidence.observe_resume({"args": {"path": "/mnt/user-data/uploads/resume.md"}}, "开发 Python 服务", "success")
    assert evidence.read_resumes == {"resume.md": "开发 Python 服务"}


def test_lost_registry_and_failed_task_cannot_pass_raw_evidence_to_parent():
    registry = EvidenceRegistry()
    guard = JobScoutLinkMiddleware(registry=registry)
    rt = context()
    guard.before_agent({}, rt)
    registry.finish(("u", "t", "r"))
    message = ToolMessage(content=block("jobscout_evidence", [item()]), tool_call_id="call")
    result = guard.wrap_tool_call(call(rt, "task"), lambda _: Command(update={"messages": [message]}))
    assert parse_payload(result.update["messages"][0].content, "jobscout_evidence") == []
    guard.before_agent({}, rt)
    guard.wrap_tool_call(call(rt, "web_fetch", url=item()["url"]), lambda _: ToolMessage(content="数据服务", tool_call_id="call"))
    message.status = "error"
    result = guard.wrap_tool_call(call(rt, "task"), lambda _: message)
    assert parse_payload(result.content, "jobscout_evidence") == []


def test_owner_upload_path_read_rejects_traversal_and_unlisted_files(tmp_path, monkeypatch):
    from app.jobscout.inputs import read_uploaded_resumes
    from deerflow.uploads import manager

    (tmp_path / "resume.md").write_text("合成简历 Python", encoding="utf-8")
    (tmp_path / "private.md").write_text("未上传内容", encoding="utf-8")
    seen = []

    def directory(thread, *, user_id):
        seen.append((thread, user_id))
        return tmp_path

    monkeypatch.setattr(manager, "get_uploads_dir", directory)
    messages = [HumanMessage(content="match", additional_kwargs={"files": [{"filename": "resume.md"}, {"filename": "../private.md"}, {"filename": "C:\\private.md"}]})]
    assert read_uploaded_resumes({"messages": messages}, "owner", "thread") == {"resume.md": "合成简历 Python"}
    assert seen == [("thread", "owner")]


def test_real_graph_three_children_rendered_before_values_and_checkpoint():
    from _agent_e2e_helpers import FakeToolCallingModel
    from langchain.agents import create_agent
    from langchain_core.tools import tool
    from langgraph.checkpoint.memory import InMemorySaver

    registry = EvidenceRegistry()
    rt = context(run_id="graph").context

    @tool
    def web_fetch(url: str) -> str:
        """Read a fixture without network."""
        return "数据服务"

    async def exercise():
        ready = asyncio.Event()
        started = 0

        @tool
        async def task(topic: str) -> str:
            """Use a fake model inside a real isolated graph."""
            nonlocal started
            started += 1
            if started == 3:
                ready.set()
            await asyncio.wait_for(ready.wait(), 10)
            child = create_agent(
                FakeToolCallingModel(
                    responses=[
                        AIMessage(content="草稿不可显示", tool_calls=[{"id": "fetch", "name": "web_fetch", "args": {"url": "https://example.test/" + topic}}]),
                        AIMessage(content="## 注入章节\n" + block("jobscout_evidence", [item(url="https://example.test/" + topic), item(quote="假片段")])),
                    ]
                ),
                tools=[web_fetch],
                middleware=[JobScoutLinkMiddleware(registry=registry)],
            )
            result = await child.ainvoke({"messages": [HumanMessage(content=topic)]}, context={key: value for key, value in {**rt, "is_subagent": True}.items() if not key.startswith("jobscout_")})
            return result["messages"][-1].content

        checkpoint = InMemorySaver()
        lead = create_agent(
            FakeToolCallingModel(
                responses=[
                    AIMessage(content="", tool_calls=[{"id": topic, "name": "task", "args": {"topic": topic}} for topic in "abc"]),
                    AIMessage(content="## 任意章节\n" + block("jobscout_report", {"company": "合成公司", "role": "开发", "evidence": []})),
                ]
            ),
            tools=[task],
            middleware=[JobScoutLinkMiddleware(registry=registry)],
            checkpointer=checkpoint,
        )
        cfg = {"configurable": {"thread_id": "synthetic"}}
        states = [state async for state in lead.astream({"messages": [HumanMessage(content="research")]}, cfg, context=rt, stream_mode="values")]
        final = (await lead.aget_state(cfg)).values["messages"][-1]
        assert final.additional_kwargs["jobscout_evidence"]["accepted_count"] == 3
        assert final.additional_kwargs["jobscout_evidence"]["rejected_counts"]["quote_not_found"] == 3
        assert "## 公司速览" in final.content
        assert "任意章节" not in final.content
        for state in states:
            assert all("注入章节" not in str(message.content) for message in state["messages"])
        assert registry.size == 0

    asyncio.run(exercise())
