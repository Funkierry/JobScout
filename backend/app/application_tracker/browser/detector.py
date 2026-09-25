"""Deterministic login and anti-bot challenge detection."""

from __future__ import annotations

import re
from typing import Protocol

from app.application_tracker.browser.models import LoginInspection, LoginState

_LOGIN_URL_PATTERN = re.compile(
    r"(?:^|[/?.&=_-])(login|log-in|signin|sign-in|passport|sso|auth|oauth|authenticate|authorize)(?:$|[/?.&#=_-])",
    re.IGNORECASE,
)
_CHALLENGE_SELECTOR = ", ".join(
    (
        'iframe[src*="captcha" i]',
        '[class*="captcha" i]',
        '[id*="captcha" i]',
        '[data-testid*="captcha" i]',
        '[class*="challenge" i]',
    )
)
_CHALLENGE_TEXT_MARKERS = (
    "captcha",
    "verify you are human",
    "security check",
    "unusual traffic",
    "验证码",
    "安全验证",
    "人机验证",
    "风控",
)


class LocatorLike(Protocol):
    async def count(self) -> int: ...

    async def inner_text(self, *, timeout: float | None = None) -> str: ...


class PageLike(Protocol):
    url: str

    def locator(self, selector: str) -> LocatorLike: ...

    async def goto(self, url: str, **kwargs: object) -> object: ...


class LoginDetector:
    """Apply cheap rules before any later LLM-based fallback is considered."""

    async def inspect(self, page: PageLike) -> LoginInspection:
        body_text = await self._body_text(page)
        has_challenge_element = await page.locator(_CHALLENGE_SELECTOR).count() > 0
        lowered_text = body_text.casefold()
        if has_challenge_element or any(marker.casefold() in lowered_text for marker in _CHALLENGE_TEXT_MARKERS):
            return LoginInspection(LoginState.HUMAN_CHALLENGE, "human_challenge")

        if _LOGIN_URL_PATTERN.search(page.url):
            return LoginInspection(LoginState.LOGIN_REQUIRED, "login_url")
        if await page.locator('input[type="password"]').count() > 0:
            return LoginInspection(LoginState.LOGIN_REQUIRED, "password_input")
        return LoginInspection(LoginState.AUTHENTICATED, "no_login_signal")

    async def _body_text(self, page: PageLike) -> str:
        try:
            return await page.locator("body").inner_text(timeout=2_000)
        except Exception:
            return ""
