from __future__ import annotations

import pytest

from app.application_tracker.browser.detector import LoginDetector
from app.application_tracker.browser.models import LoginState


class FakeLocator:
    def __init__(self, *, count: int = 0, text: str = "") -> None:
        self._count = count
        self._text = text

    async def count(self) -> int:
        return self._count

    async def inner_text(self, *, timeout: float | None = None) -> str:
        del timeout
        return self._text


class FakePage:
    def __init__(
        self,
        url: str,
        *,
        has_password: bool = False,
        has_challenge: bool = False,
        text: str = "",
    ) -> None:
        self.url = url
        self.has_password = has_password
        self.has_challenge = has_challenge
        self.text = text

    def locator(self, selector: str) -> FakeLocator:
        if selector == 'input[type="password"]':
            return FakeLocator(count=int(self.has_password))
        if selector == "body":
            return FakeLocator(text=self.text)
        return FakeLocator(count=int(self.has_challenge))


@pytest.mark.asyncio
async def test_login_detector_recognizes_login_url_markers() -> None:
    inspection = await LoginDetector().inspect(FakePage("https://accounts.example.com/sso/signin?next=%2Fapplications"))

    assert inspection.state is LoginState.LOGIN_REQUIRED
    assert inspection.reason == "login_url"


@pytest.mark.asyncio
async def test_login_detector_recognizes_password_input_without_url_marker() -> None:
    inspection = await LoginDetector().inspect(FakePage("https://jobs.example.com/applications", has_password=True))

    assert inspection.state is LoginState.LOGIN_REQUIRED
    assert inspection.reason == "password_input"


@pytest.mark.asyncio
async def test_login_detector_hands_captcha_to_a_human() -> None:
    inspection = await LoginDetector().inspect(
        FakePage(
            "https://jobs.example.com/applications",
            has_challenge=True,
            text="Please verify you are human",
        )
    )

    assert inspection.state is LoginState.HUMAN_CHALLENGE
    assert inspection.reason == "human_challenge"


@pytest.mark.asyncio
async def test_login_detector_accepts_an_ordinary_status_page_without_llm() -> None:
    inspection = await LoginDetector().inspect(
        FakePage(
            "https://jobs.example.com/applications/42",
            text="Current application status: Assessment",
        )
    )

    assert inspection.state is LoginState.AUTHENTICATED
    assert inspection.reason == "no_login_signal"
