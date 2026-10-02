"""Exercise the same extension hooks used by lead and delegated agents."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from langchain.agents.middleware import ModelResponse
from langchain_core.messages import AIMessage, ToolMessage

from app.jobscout.middleware import JobScoutLinkMiddleware
from app.jobscout.run_evidence import EvidenceRegistry


def runtime(mode="interview_prep", **overrides):
    return SimpleNamespace(context={"user_id": "u", "thread_id": "t", "run_id": "r", "jobscout_mode": mode, **overrides})


def request(rt, name="web_search", url="https://example.cn/a"):
    return SimpleNamespace(runtime=rt, tool_call={"id": "call", "name": name, "args": {"url": url}})


def response(text):
    return ModelResponse(result=[AIMessage(content=text, id="answer")])


def test_child_sources_reach_root_before_checkpoint_and_have_audit_count():
    registry = EvidenceRegistry()
    lead = JobScoutLinkMiddleware(registry=registry)
    child = JobScoutLinkMiddleware(registry=registry)
    journal = Mock()
    rt = runtime(__run_journal=journal)
    lead.before_agent({"messages": []}, rt)
    child_rt = runtime(None, is_subagent=True)
    child.before_agent({"messages": []}, child_rt)
    child.wrap_tool_call(request(child_rt), lambda _: ToolMessage(content='[{"url":"https://example.cn/a"}]', tool_call_id="call"))
    child.after_agent({}, child_rt)
    output = lead.wrap_model_call(SimpleNamespace(runtime=rt), lambda _: response("[真](https://example.cn/a) [假](https://example.cn/b)"))
    message = output.result[0]
    assert message.content == "[真](https://example.cn/a) 假（来源未核验）"
    assert message.additional_kwargs["jobscout_links"]["removed_count"] == 1
    assert message.additional_kwargs["jobscout_links"]["run_id"] == "r"
    assert journal.record_middleware.call_args.kwargs["changes"]["removed_count"] == 1
    lead.after_agent({}, rt)
    assert registry.size == 0


@pytest.mark.parametrize("mode", [None, "base_match"])
def test_generic_and_base_modes_are_exact_noops(mode):
    guard = JobScoutLinkMiddleware(registry=EvidenceRegistry())
    rt = runtime(mode)
    guard.before_agent({}, rt)
    original = response("[链接](https://example.cn/private)")
    assert guard.wrap_model_call(SimpleNamespace(runtime=rt), lambda _: original) is original


def test_previous_history_and_other_run_or_user_cannot_whitelist_a_link():
    registry = EvidenceRegistry()
    guard = JobScoutLinkMiddleware(registry=registry)
    rt = runtime()
    guard.before_agent({"messages": [ToolMessage(name="web_search", content='[{"url":"https://invented.cn/old"}]', tool_call_id="old")]}, rt)
    for foreign in (runtime(run_id="other"), runtime(user_id="other"), runtime(thread_id="other")):
        guard.before_agent({}, foreign)
        guard.wrap_tool_call(request(foreign), lambda _: ToolMessage(content='[{"url":"https://invented.cn/old"}]', tool_call_id="call"))
    output = guard.wrap_model_call(SimpleNamespace(runtime=rt), lambda _: response("[旧](https://invented.cn/old)"))
    assert output.result[0].content == "旧（来源未核验）"


def test_async_tool_and_model_hooks_and_error_cleanup():
    async def exercise():
        registry = EvidenceRegistry()
        guard = JobScoutLinkMiddleware(registry=registry)
        rt = runtime()
        await guard.abefore_agent({}, rt)

        async def fetch(_):
            return ToolMessage(content="# document", tool_call_id="call")

        await guard.awrap_tool_call(request(rt, "web_fetch"), fetch)

        async def model(_):
            return response("[来源](https://example.cn/a)")

        result = await guard.awrap_model_call(SimpleNamespace(runtime=rt), model)
        assert result.result[0].content == "[来源](https://example.cn/a)"

        async def cancelled(_):
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await guard.awrap_model_call(SimpleNamespace(runtime=rt), cancelled)
        assert registry.size == 0

    asyncio.run(exercise())


def test_registry_bounded_expiry_and_late_child_cannot_recreate_finished_run():
    now = [0.0]
    registry = EvidenceRegistry(max_runs=2, max_urls=2, ttl_seconds=10, clock=lambda: now[0])
    keys = [("u", "t", str(i)) for i in range(3)]
    for key in keys:
        registry.start(key)
        registry.add(key, {"https://example.cn/a", "https://example.cn/b", "https://example.cn/c"})
    assert registry.size == 2
    assert len(registry.urls(keys[-1])) == 2
    registry.finish(keys[-1])
    registry.add(keys[-1], {"https://example.cn/late"})
    assert registry.urls(keys[-1]) == set()
    now[0] = 11
    assert registry.size == 0


def test_real_graph_values_and_checkpoint_filter_three_parallel_child_sources():
    from _agent_e2e_helpers import FakeToolCallingModel
    from langchain.agents import create_agent
    from langchain_core.messages import HumanMessage
    from langchain_core.tools import tool
    from langgraph.checkpoint.memory import InMemorySaver

    from app.jobscout.run_evidence import RUN_EVIDENCE
    from deerflow.agents.middlewares.configured_extensions import load_configured_extension_middlewares

    config = SimpleNamespace(extensions=SimpleNamespace(middlewares=["app.jobscout.middleware:JobScoutLinkMiddleware"]))
    ctx = runtime(run_id="graph-test").context

    @tool
    def web_search(query: str) -> str:
        """Return a synthetic source, without any network access."""
        return '[{"url":"https://example.cn/' + query + '"}]'

    async def exercise():
        started = 0
        all_started = asyncio.Event()

        @tool
        async def task(topic: str) -> str:
            """Run isolated research using a fake model and an actual child graph."""
            nonlocal started
            started += 1
            if started == 3:
                all_started.set()
            await asyncio.wait_for(all_started.wait(), timeout=10)
            child = create_agent(
                FakeToolCallingModel(
                    responses=[
                        AIMessage(content="", tool_calls=[{"id": "reused-provider-id", "name": "web_search", "args": {"query": topic}}]),
                        AIMessage(content="Only a summary: https://invented.cn/from-task"),
                    ]
                ),
                tools=[web_search],
                middleware=load_configured_extension_middlewares(config),
            )
            await child.ainvoke({"messages": [HumanMessage(content=topic)]}, context={k: v for k, v in {**ctx, "is_subagent": True}.items() if k != "jobscout_mode"})
            return "Only a summary: https://invented.cn/from-task"

        checkpointer = InMemorySaver()
        root = create_agent(
            FakeToolCallingModel(
                responses=[
                    AIMessage(content="", tool_calls=[{"id": topic, "name": "task", "args": {"topic": topic}} for topic in ("a", "b", "c")]),
                    AIMessage(content="[A](https://example.cn/a) [B](https://example.cn/b) [C](https://example.cn/c) [猜测](https://invented.cn/from-task)"),
                ]
            ),
            tools=[task],
            middleware=load_configured_extension_middlewares(config),
            checkpointer=checkpointer,
        )
        run_config = {"configurable": {"thread_id": "fixture-thread"}}
        states = [state async for state in root.astream({"messages": [HumanMessage(content="research")]}, config=run_config, context=ctx, stream_mode="values")]
        for state in states:
            for message in state["messages"]:
                if isinstance(message, AIMessage):
                    assert "https://invented.cn" not in message.content
        final = (await root.aget_state(run_config)).values["messages"][-1]
        assert final.content == "[A](https://example.cn/a) [B](https://example.cn/b) [C](https://example.cn/c) 猜测（来源未核验）"
        assert final.additional_kwargs["jobscout_links"]["seen_url_count"] == 3
        assert not RUN_EVIDENCE.active(("u", "t", "graph-test"))

    asyncio.run(exercise())


def test_audit_failure_and_content_blocks_do_not_expose_raw_response():
    guard = JobScoutLinkMiddleware(registry=EvidenceRegistry())
    journal = Mock()
    journal.record_middleware.side_effect = RuntimeError("storage unavailable")
    rt = runtime(__run_journal=journal)
    guard.before_agent({}, rt)
    answer = response([{"type": "text", "text": "[猜测](https://invented.cn/a)"}, {"type": "image_url", "image_url": {"url": "https://invented.cn/image"}}])
    final = guard.wrap_model_call(SimpleNamespace(runtime=rt), lambda _: answer).result[0]
    assert final.content == "猜测（来源未核验）"
    assert final.additional_kwargs["jobscout_links"]["trace_submitted"] is False


def test_registry_eviction_during_lead_run_fails_closed():
    registry = EvidenceRegistry(max_runs=1)
    guard = JobScoutLinkMiddleware(registry=registry)
    rt = runtime()
    guard.before_agent({}, rt)
    guard.wrap_tool_call(request(rt), lambda _: ToolMessage(content='[{"url":"https://example.cn/a"}]', tool_call_id="call"))
    guard.before_agent({}, runtime(run_id="other"))
    final = guard.wrap_model_call(SimpleNamespace(runtime=rt), lambda _: response("[来源](https://example.cn/a)")).result[0]
    assert final.content == "来源（来源未核验）"


@pytest.mark.parametrize("hook", ["wrap_tool_call", "wrap_model_call"])
def test_recovered_tool_or_model_failure_preserves_prior_successful_sources(hook):
    registry = EvidenceRegistry()
    guard = JobScoutLinkMiddleware(registry=registry)
    rt = runtime()
    guard.before_agent({}, rt)
    guard.wrap_tool_call(request(rt), lambda _: ToolMessage(content='[{"url":"https://example.cn/a"}]', tool_call_id="call"))

    def failure(_):
        raise ValueError("synthetic recoverable failure")

    with pytest.raises(ValueError):
        getattr(guard, hook)(request(rt), failure)
    final = guard.wrap_model_call(SimpleNamespace(runtime=rt), lambda _: response("[已有来源](https://example.cn/a)")).result[0]
    assert final.content == "[已有来源](https://example.cn/a)"
    guard.after_agent({}, rt)
    assert registry.size == 0
