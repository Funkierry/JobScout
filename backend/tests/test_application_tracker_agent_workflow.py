from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from app.application_tracker.agent.workflow import AgentRunConfig, ApplicationTrackerAgent
from app.application_tracker.browser.live_session import InteractiveElement
from app.application_tracker.browser.models import BrowserAccessConfig, BrowserAccessResult, LoginState
from app.application_tracker.models import (
    ApplicationInput,
    ApplicationStatus,
    CheckResult,
    StatusRecord,
)


class StubExtractor:
    def __init__(self, record: StatusRecord) -> None:
        self.record = record
        self.calls = 0

    def extract(self, *args: Any, **kwargs: Any) -> StatusRecord:
        del args, kwargs
        self.calls += 1
        return self.record


class StubPlanner:
    def __init__(self, responses: Sequence[AIMessage] = ()) -> None:
        self.responses = list(responses)
        self.calls: list[list[BaseMessage]] = []
        self.bound_tool_names: set[str] = set()

    def bind_tools(self, tools: list[Any]) -> StubPlanner:
        self.bound_tool_names = {tool.name for tool in tools}
        return self

    async def ainvoke(self, messages: list[BaseMessage]) -> AIMessage:
        self.calls.append(messages)
        if not self.responses:
            raise AssertionError("planner must not be called")
        return self.responses.pop(0)


class FakeBrowser:
    def __init__(
        self,
        *,
        page_text: str,
        check_result: CheckResult = CheckResult.SUCCESS,
        login_state: LoginState = LoginState.AUTHENTICATED,
        after_click_text: str | None = None,
        login_text: str | None = None,
    ) -> None:
        self.page_text = page_text
        self.check_result = check_result
        self.login_state = login_state
        self.after_click_text = after_click_text
        self.login_text = login_text
        self.clicks: list[int] = []
        self.screenshot_calls = 0
        self.login_calls = 0
        self.closed = False

    async def open_page(self) -> BrowserAccessResult:
        return BrowserAccessResult(
            page_text=self.page_text if self.check_result is CheckResult.SUCCESS else "",
            check_result=self.check_result,
            login_state=self.login_state,
        )

    async def get_page_text(self) -> str:
        return self.page_text

    async def get_interactive_elements(self) -> list[InteractiveElement]:
        return [InteractiveElement(ref=3, role="button", name="View details")]

    async def click(self, ref: int) -> None:
        self.clicks.append(ref)
        if self.after_click_text is not None:
            self.page_text = self.after_click_text

    async def screenshot(self) -> bytes:
        self.screenshot_calls += 1
        return b"fake-image"

    async def request_human_login(self) -> BrowserAccessResult:
        self.login_calls += 1
        if self.login_text is None:
            return BrowserAccessResult(
                page_text="",
                check_result=CheckResult.LOGIN_REQUIRED,
                login_state=LoginState.LOGIN_REQUIRED,
                login_attempted=True,
                error_code="login_timeout",
            )
        self.page_text = self.login_text
        self.check_result = CheckResult.SUCCESS
        self.login_state = LoginState.AUTHENTICATED
        return BrowserAccessResult(
            page_text=self.page_text,
            check_result=CheckResult.SUCCESS,
            login_state=LoginState.AUTHENTICATED,
            login_attempted=True,
        )

    async def close(self) -> None:
        self.closed = True


class FakeBrowserFactory:
    def __init__(self, browser: FakeBrowser) -> None:
        self.browser = browser

    def create(
        self,
        *,
        url: str,
        user_id: str,
        config: BrowserAccessConfig,
        on_event: Any = None,
    ) -> FakeBrowser:
        del url, user_id, config, on_event
        return self.browser


def _application() -> ApplicationInput:
    return ApplicationInput(
        company="Example Co",
        role="AI Product Manager",
        url="https://jobs.example.com/applications/42",
        applied_at="2026-09-01",
    )


def _record(
    status: ApplicationStatus,
    *,
    confidence: float,
    raw_status: str,
    evidence: str,
) -> StatusRecord:
    checked_at = datetime(2026, 9, 25, 3, 0, tzinfo=UTC)
    return StatusRecord(
        company="Example Co",
        role="AI Product Manager",
        url="https://jobs.example.com/applications/42",
        status=status,
        raw_status=raw_status,
        confidence=confidence,
        evidence=evidence,
        checked_at=checked_at,
        changed_at=checked_at,
        check_result=CheckResult.SUCCESS,
    )


def _browser_config(tmp_path: Any) -> BrowserAccessConfig:
    return BrowserAccessConfig(
        profile_root=tmp_path,
        allow_private_addresses=True,
    )


@pytest.mark.asyncio
async def test_clear_page_uses_extractor_without_calling_planner(tmp_path: Any) -> None:
    browser = FakeBrowser(page_text="Current status: Resume screening")
    extractor = StubExtractor(
        _record(
            ApplicationStatus.RESUME_SCREENING,
            confidence=0.93,
            raw_status="Resume screening",
            evidence="Current status: Resume screening",
        )
    )
    planner = StubPlanner()
    agent = ApplicationTrackerAgent(
        model=planner,
        extractor=extractor,
        browser_factory=FakeBrowserFactory(browser),
    )

    result = await agent.run(
        _application(),
        user_id="local-user",
        browser_config=_browser_config(tmp_path),
    )

    assert result.status is ApplicationStatus.RESUME_SCREENING
    assert extractor.calls == 1
    assert planner.calls == []
    assert browser.closed


@pytest.mark.asyncio
async def test_agent_can_click_details_then_update_record(tmp_path: Any) -> None:
    browser = FakeBrowser(
        page_text="Application overview",
        after_click_text="Current status: First interview",
    )
    extractor = StubExtractor(
        _record(
            ApplicationStatus.UNKNOWN,
            confidence=0.2,
            raw_status="",
            evidence="",
        )
    )
    planner = StubPlanner(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "click",
                        "args": {"ref": 3},
                        "id": "click-1",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "update_record",
                        "args": {
                            "status": "一面",
                            "raw_status": "First interview",
                            "confidence": 0.9,
                            "evidence": "Current status: First interview",
                        },
                        "id": "update-1",
                    }
                ],
            ),
        ]
    )
    agent = ApplicationTrackerAgent(
        model=planner,
        extractor=extractor,
        browser_factory=FakeBrowserFactory(browser),
    )

    result = await agent.run(
        _application(),
        user_id="local-user",
        browser_config=_browser_config(tmp_path),
    )

    assert result.status is ApplicationStatus.FIRST_INTERVIEW
    assert result.confidence == 0.9
    assert browser.clicks == [3]
    assert len(planner.calls) == 2


@pytest.mark.asyncio
async def test_screenshot_is_forwarded_to_vision_model_and_marked_low_confidence(
    tmp_path: Any,
) -> None:
    browser = FakeBrowser(page_text="")
    extractor = StubExtractor(
        _record(
            ApplicationStatus.UNKNOWN,
            confidence=0,
            raw_status="",
            evidence="",
        )
    )
    planner = StubPlanner(
        [
            AIMessage(
                content="",
                tool_calls=[{"name": "screenshot", "args": {}, "id": "shot-1"}],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "update_record",
                        "args": {
                            "status": "测评",
                            "raw_status": "Online assessment",
                            "confidence": 0.92,
                            "evidence": "Current stage: Online assessment",
                        },
                        "id": "update-2",
                    }
                ],
            ),
        ]
    )
    agent = ApplicationTrackerAgent(
        model=planner,
        extractor=extractor,
        browser_factory=FakeBrowserFactory(browser),
    )

    result = await agent.run(
        _application(),
        user_id="local-user",
        browser_config=_browser_config(tmp_path),
    )

    assert result.status is ApplicationStatus.ASSESSMENT
    assert result.confidence == 0.69
    assert browser.screenshot_calls == 1
    assert any(isinstance(message, HumanMessage) and isinstance(message.content, list) and any(block.get("type") == "image_url" for block in message.content) for message in planner.calls[1])


@pytest.mark.asyncio
async def test_agent_requests_human_login_then_updates_from_reopened_page(
    tmp_path: Any,
) -> None:
    browser = FakeBrowser(
        page_text="",
        check_result=CheckResult.LOGIN_REQUIRED,
        login_state=LoginState.LOGIN_REQUIRED,
        login_text="Current status: Written test",
    )
    extractor = StubExtractor(
        _record(
            ApplicationStatus.UNKNOWN,
            confidence=0,
            raw_status="",
            evidence="",
        )
    )
    planner = StubPlanner(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "request_human_login",
                        "args": {},
                        "id": "login-1",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "update_record",
                        "args": {
                            "status": "笔试",
                            "raw_status": "Written test",
                            "confidence": 0.91,
                            "evidence": "Current status: Written test",
                        },
                        "id": "update-3",
                    }
                ],
            ),
        ]
    )
    agent = ApplicationTrackerAgent(
        model=planner,
        extractor=extractor,
        browser_factory=FakeBrowserFactory(browser),
    )

    result = await agent.run(
        _application(),
        user_id="local-user",
        browser_config=_browser_config(tmp_path),
    )

    assert result.status is ApplicationStatus.WRITTEN_TEST
    assert browser.login_calls == 1
    assert extractor.calls == 0


@pytest.mark.asyncio
async def test_last_allowed_planner_step_still_executes_update_tool(
    tmp_path: Any,
) -> None:
    browser = FakeBrowser(page_text="Current status: Assessment")
    extractor = StubExtractor(
        _record(
            ApplicationStatus.UNKNOWN,
            confidence=0,
            raw_status="",
            evidence="",
        )
    )
    planner = StubPlanner(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "update_record",
                        "args": {
                            "status": "测评",
                            "raw_status": "Assessment",
                            "confidence": 0.88,
                            "evidence": "Current status: Assessment",
                        },
                        "id": "last-update",
                    }
                ],
            )
        ]
    )
    agent = ApplicationTrackerAgent(
        model=planner,
        extractor=extractor,
        browser_factory=FakeBrowserFactory(browser),
        run_config=AgentRunConfig(max_agent_steps=1),
    )

    result = await agent.run(
        _application(),
        user_id="local-user",
        browser_config=_browser_config(tmp_path),
    )

    assert result.status is ApplicationStatus.ASSESSMENT
    assert len(planner.calls) == 1
