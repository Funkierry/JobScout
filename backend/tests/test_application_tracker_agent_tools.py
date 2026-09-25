from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.application_tracker.agent.tools import (
    ApplicationTrackerToolbox,
    ScreenshotObservation,
)
from app.application_tracker.browser.live_session import InteractiveElement
from app.application_tracker.browser.models import BrowserAccessResult, LoginState
from app.application_tracker.models import (
    ApplicationInput,
    ApplicationStatus,
    CheckResult,
)


class FakeBrowser:
    def __init__(
        self,
        *,
        page_text: str = "Current status: Resume screening",
        open_result: BrowserAccessResult | None = None,
        element_name: str = "View details",
    ) -> None:
        self.page_text = page_text
        self.open_result = open_result or BrowserAccessResult(
            page_text=page_text,
            check_result=CheckResult.SUCCESS,
            login_state=LoginState.AUTHENTICATED,
        )
        self.element_name = element_name
        self.clicked_refs: list[int] = []
        self.screenshot_calls = 0
        self.login_calls = 0
        self.closed = False

    async def open_page(self) -> BrowserAccessResult:
        return self.open_result

    async def get_page_text(self) -> str:
        return self.page_text

    async def get_interactive_elements(self) -> list[InteractiveElement]:
        return [InteractiveElement(ref=7, role="button", name=self.element_name)]

    async def click(self, ref: int) -> None:
        self.clicked_refs.append(ref)
        self.page_text = "Current status: First interview"

    async def screenshot(self) -> bytes:
        self.screenshot_calls += 1
        return b"fake-png"

    async def request_human_login(self) -> BrowserAccessResult:
        self.login_calls += 1
        return BrowserAccessResult(
            page_text=self.page_text,
            check_result=CheckResult.SUCCESS,
            login_state=LoginState.AUTHENTICATED,
            login_attempted=True,
        )

    async def close(self) -> None:
        self.closed = True


def _application() -> ApplicationInput:
    return ApplicationInput(
        company="Example Co",
        role="AI Product Manager",
        url="https://jobs.example.com/applications/42",
        applied_at="2026-09-01",
    )


def _toolbox(browser: FakeBrowser) -> ApplicationTrackerToolbox:
    return ApplicationTrackerToolbox(
        application=_application(),
        browser=browser,
        checked_at=datetime(2026, 9, 25, 3, 0, tzinfo=UTC),
    )


def test_toolbox_exposes_only_the_six_scoped_agent_tools() -> None:
    toolbox = _toolbox(FakeBrowser())

    assert {tool.name for tool in toolbox.tools} == {
        "open_page",
        "get_page_text",
        "screenshot",
        "click",
        "request_human_login",
        "update_record",
    }


@pytest.mark.asyncio
async def test_click_uses_a_snapshot_reference_and_refreshes_page_text() -> None:
    browser = FakeBrowser()
    toolbox = _toolbox(browser)
    await toolbox.open_page()

    result = await toolbox.click(ref=7)

    assert browser.clicked_refs == [7]
    assert "First interview" in result
    assert toolbox.page_text == "Current status: First interview"


@pytest.mark.asyncio
async def test_click_rejects_destructive_application_actions() -> None:
    browser = FakeBrowser(element_name="Withdraw application")
    toolbox = _toolbox(browser)
    await toolbox.open_page()

    with pytest.raises(ValueError, match="read-only"):
        await toolbox.click(ref=7)

    assert browser.clicked_refs == []


@pytest.mark.asyncio
async def test_click_rejects_unrecognized_actions_even_if_not_explicitly_destructive() -> None:
    browser = FakeBrowser(element_name="Continue")
    toolbox = _toolbox(browser)
    await toolbox.open_page()

    with pytest.raises(ValueError, match="read-only"):
        await toolbox.click(ref=7)

    assert browser.clicked_refs == []


@pytest.mark.asyncio
async def test_human_login_tool_can_override_rules_but_only_once() -> None:
    browser = FakeBrowser()
    toolbox = _toolbox(browser)
    await toolbox.open_page()

    first = await toolbox.request_human_login()
    second = await toolbox.request_human_login()

    assert "human_login_finished" in first
    assert "error" in second.lower()
    assert browser.login_calls == 1


@pytest.mark.asyncio
async def test_update_record_rejects_ungrounded_dom_evidence() -> None:
    toolbox = _toolbox(FakeBrowser())
    await toolbox.get_page_text()

    result = await toolbox.update_record(
        status=ApplicationStatus.OFFER,
        raw_status="Offer sent",
        confidence=0.99,
        evidence="Congratulations, your offer is ready",
    )

    assert "error" in result.lower()
    assert toolbox.record is None


@pytest.mark.asyncio
async def test_unknown_status_rejects_nonempty_ungrounded_evidence() -> None:
    toolbox = _toolbox(FakeBrowser())
    await toolbox.get_page_text()

    result = await toolbox.update_record(
        status=ApplicationStatus.UNKNOWN,
        raw_status="Invented state",
        confidence=0.2,
        evidence="Invented evidence",
    )

    assert "error" in result.lower()
    assert toolbox.record is None


@pytest.mark.asyncio
async def test_screenshot_allows_visual_evidence_but_caps_confidence() -> None:
    toolbox = _toolbox(FakeBrowser(page_text=""))

    screenshot = await toolbox.screenshot()
    result = await toolbox.update_record(
        status=ApplicationStatus.FIRST_INTERVIEW,
        raw_status="First interview",
        confidence=0.94,
        evidence="Current status: First interview",
    )

    assert isinstance(screenshot, ScreenshotObservation)
    assert screenshot.media_type == "image/png"
    assert toolbox.record is not None
    assert toolbox.record.confidence == 0.69
    assert '"updated": true' in result
