"""Cancellation must close the browser and the async model request."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.application_tracker.agent.workflow import AgentRunConfig, ApplicationTrackerAgent
from app.application_tracker.browser.models import BrowserAccessConfig
from app.application_tracker.extractor import StatusExtractor
from app.application_tracker.models import ApplicationInput, CheckResult
from app.application_tracker.observations import SourceObservation


@pytest.mark.asyncio
async def test_check_deadline_cancels_and_closes_browser(tmp_path):
    closed = asyncio.Event()

    async def open_page():
        await asyncio.Event().wait()

    browser = SimpleNamespace(open_page=open_page, close=AsyncMock(side_effect=closed.set))
    agent = ApplicationTrackerAgent(model=Mock(), extractor=Mock(), browser_factory=SimpleNamespace(create=lambda **_: browser), run_config=AgentRunConfig(check_timeout_seconds=0.01))
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(agent.run(ApplicationInput(company="Fixture", role="Engineer", url="https://jobs.example.com/a"), user_id="fixture", browser_config=BrowserAccessConfig(profile_root=tmp_path, interactive_login=False)), 2)
    assert closed.is_set()


@pytest.mark.asyncio
async def test_async_extraction_cancellation_reaches_model():
    entered, closed = asyncio.Event(), asyncio.Event()

    async def predict(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    model = SimpleNamespace(ainvoke=predict, invoke=Mock(side_effect=AssertionError("must use cancellable async model")))
    extractor = StatusExtractor(model)
    task = asyncio.create_task(extractor.aextract(ApplicationInput(company="Fixture", role="Engineer", url="https://jobs.example.com/a"), "Engineer 当前状态：笔试"))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed.is_set()
        model.invoke.assert_not_called()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_async_extraction_keeps_grounding_contract():
    model = SimpleNamespace(ainvoke=AsyncMock(return_value={"status": "Offer", "raw_status": "已录用", "evidence": "已录用", "confidence": 0.9}))
    record = await StatusExtractor(model).aextract(ApplicationInput(company="Fixture", role="Engineer", url="https://jobs.example.com/a"), "Engineer 当前状态：笔试")
    assert record.check_result is CheckResult.FETCH_FAILED


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_invalid_check_deadline_is_rejected(value):
    with pytest.raises(ValueError):
        AgentRunConfig(check_timeout_seconds=value)


@pytest.mark.asyncio
@pytest.mark.parametrize("json_first", [False, True])
async def test_async_and_offline_extraction_match_including_json_priority(json_first):
    from test_application_tracker_extractor import StubStructuredModel

    response = {"status": "笔试", "raw_status": "笔试", "evidence": "当前状态：笔试", "confidence": 0.9}
    sync_model, async_model = StubStructuredModel(response), StubStructuredModel(response)
    application = ApplicationInput(company="Fixture", role="Engineer", url="https://jobs.example.com/a")
    text = "Engineer 当前状态：笔试"
    kwargs = {"checked_at": datetime(2026, 10, 4, tzinfo=UTC), "observations": [SourceObservation(kind="json", text=text)] if json_first else []}
    expected = StatusExtractor(sync_model).extract(application, text, **kwargs)
    actual = await StatusExtractor(async_model).aextract(application, text, **kwargs)
    assert actual.check_result is CheckResult.SUCCESS
    assert actual == expected
    assert len(async_model.calls) == 1


@pytest.mark.asyncio
async def test_workflow_deadline_reaches_model_and_closes_browser(tmp_path):
    from test_application_tracker_agent_workflow import FakeBrowser, FakeBrowserFactory

    model_closed = asyncio.Event()

    async def predict(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            model_closed.set()

    browser = FakeBrowser(page_text="Engineer 当前状态：笔试")
    agent = ApplicationTrackerAgent(model=Mock(), extractor=StatusExtractor(SimpleNamespace(ainvoke=predict)), browser_factory=FakeBrowserFactory(browser), run_config=AgentRunConfig(check_timeout_seconds=0.1))
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(agent.run(ApplicationInput(company="Fixture", role="Engineer", url="https://jobs.example.com/a"), user_id="fixture", browser_config=BrowserAccessConfig(profile_root=tmp_path, interactive_login=False)), 2)
    assert model_closed.is_set() and browser.closed
