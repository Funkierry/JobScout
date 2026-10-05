"""Synthetic provider usage: no credentials, browser or network required."""

from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from deerflow.config.token_budget_config import TokenBudgetConfig


def reply(tokens=1100, **kwargs):
    return AIMessage("fixture", usage_metadata={"input_tokens": tokens - 100, "output_tokens": 100, "total_tokens": tokens}, **kwargs)


@pytest.mark.asyncio
async def test_structured_extraction_and_planner_share_one_budget():
    from app.jobscout.budget import BudgetedModel, BudgetExceeded, RunTokenBudget

    budget = RunTokenBudget(TokenBudgetConfig(enabled=True, max_tokens=1000))
    structured = Mock(ainvoke=AsyncMock(return_value={"raw": reply(), "parsed": {"status": "unknown"}, "parsing_error": None}))
    provider = Mock()
    provider.with_structured_output.return_value = structured
    provider.bind_tools.return_value = Mock(ainvoke=AsyncMock())
    model = BudgetedModel(provider, budget)
    assert await model.with_structured_output(dict).ainvoke([]) == {"status": "unknown"}
    with pytest.raises(BudgetExceeded):
        await model.bind_tools([]).ainvoke([])
    provider.bind_tools.return_value.ainvoke.assert_not_called()
    provider.with_structured_output.assert_called_once_with(dict, include_raw=True)


def test_missing_usage_stops_future_calls_and_input_limit_is_honored():
    from app.jobscout.budget import BudgetExceeded, RunTokenBudget

    for response in (AIMessage("no usage"), reply(600)):
        budget = RunTokenBudget(TokenBudgetConfig(enabled=True, max_tokens=1000, max_input_tokens=400))
        budget.record(response)
        with pytest.raises(BudgetExceeded):
            budget.check()


@pytest.mark.asyncio
async def test_real_research_graph_stops_before_tools_or_another_model_call(monkeypatch):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    from app.jobscout.agent import assemble_research_agent
    from deerflow.config.app_config import AppConfig

    class FakeModel(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    first = reply(tool_calls=[{"name": "read_file", "args": {"path": "/mnt/user-data/uploads/resume.md"}, "id": "read-1", "type": "tool_call"}])
    model = FakeModel(responses=[first, AIMessage("must not be called")])
    monkeypatch.setattr("deerflow.models.factory.create_chat_model", lambda **kwargs: model)
    config = AppConfig.model_validate(
        {"models": [{"name": "fake", "use": "langchain_openai:ChatOpenAI", "model": "fake"}], "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"}, "token_budget": {"enabled": True, "max_tokens": 1000}}
    )
    context = {"user_id": "owner", "thread_id": "budget", "run_id": "budget-1", "jobscout_mode": "base_match", "jobscout_evidence_version": 2}
    graph = assemble_research_agent(mode="base_match", context=context, app_config=config, resumes={"resume.md": "fixture"})
    result = await graph.ainvoke({"messages": [HumanMessage("fixture")]}, context=context)
    assert model.i == 1
    assert not any(message.type == "tool" for message in result["messages"])
    final = result["messages"][-1]
    assert not final.tool_calls
    assert final.additional_kwargs["jobscout_evidence"]["version"] == 2
    assert "预算" in final.content


def test_research_children_receive_the_same_budget(monkeypatch):
    from app.jobscout.budget import BudgetedModel, BudgetExceeded, RunTokenBudget

    budget = RunTokenBudget(TokenBudgetConfig(enabled=True, max_tokens=1000))
    root = BudgetedModel(Mock(invoke=Mock(return_value=reply(600))), budget)
    child = BudgetedModel(Mock(invoke=Mock(return_value=reply(600))), budget)
    root.invoke([])
    with pytest.raises(BudgetExceeded):
        child.invoke([])
    with pytest.raises(BudgetExceeded):
        root.invoke([])
    assert budget.total == 1200


@pytest.mark.asyncio
async def test_actual_child_spend_prevents_root_continuation(monkeypatch):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    from app.jobscout.agent import assemble_research_agent
    from deerflow.config.app_config import AppConfig

    class FakeModel(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    root = FakeModel(responses=[reply(600, tool_calls=[{"name": "task", "id": "task-one", "args": {"prompt": "fixture"}}]), AIMessage("must not call")])
    child = FakeModel(responses=[reply(600), AIMessage("must not call")])
    factory = Mock(side_effect=[root, child])
    monkeypatch.setattr("deerflow.models.factory.create_chat_model", factory)
    config = AppConfig.model_validate({"models": [{"name": "fake", "model": "fake", "use": "fake:Model"}], "sandbox": {"use": "fake:Sandbox"}, "token_budget": {"enabled": True, "max_tokens": 1000}})
    context = {"user_id": "owner", "thread_id": "budget-child", "run_id": "budget-child", "jobscout_mode": "interview_prep", "jobscout_evidence_version": 2}
    graph = assemble_research_agent(mode="interview_prep", context=context, app_config=config, resumes={})
    result = await graph.ainvoke({"messages": [HumanMessage("fixture")]}, context=context)
    assert root.i == child.i == 1
    assert "预算" in result["messages"][-1].content
