"""Persistent browser lifecycle with explicit human-login handoff."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol

from app.application_tracker.browser.detector import LoginDetector, PageLike
from app.application_tracker.browser.models import (
    BrowserAccessConfig,
    BrowserAccessResult,
    BrowserEvent,
)
from app.application_tracker.browser.playwright_adapter import (
    PlaywrightPersistentContextLauncher,
)
from app.application_tracker.browser.profiles import profile_directory
from app.application_tracker.models import CheckResult

EventHandler = Callable[[BrowserEvent], Awaitable[None] | None]
Sleep = Callable[[float], Awaitable[None]]


class BrowserContextLike(Protocol):
    pages: list[PageLike]

    async def new_page(self) -> PageLike: ...

    async def route(self, pattern: str, handler: Callable[..., Any]) -> None: ...

    async def close(self) -> None: ...


class ContextLauncher(Protocol):
    async def launch(
        self,
        profile_dir: Path,
        *,
        headless: bool,
        timeout_ms: int,
    ) -> BrowserContextLike: ...


class PersistentBrowserService:
    """Fetch pages with a reusable local profile and bounded human login flow."""

    def __init__(
        self,
        launcher: ContextLauncher | None = None,
        *,
        detector: LoginDetector | None = None,
        sleep: Sleep = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._launcher = launcher or PlaywrightPersistentContextLauncher()
        self._detector = detector or LoginDetector()
        self._sleep = sleep
        self._monotonic = monotonic

    async def fetch(
        self,
        url: str,
        *,
        user_id: str,
        config: BrowserAccessConfig | None = None,
        on_event: EventHandler | None = None,
    ) -> BrowserAccessResult:
        from app.application_tracker.browser.live_session import PersistentAgentBrowser
        from app.application_tracker.browser.profiles import shared_profile_lock

        settings = config or BrowserAccessConfig.from_env()
        path = profile_directory(settings.profile_root, user_id=user_id, url=url)
        browser = PersistentAgentBrowser(url=url, profile_dir=path, profile_lock=shared_profile_lock(path), launcher=self._launcher, config=settings, detector=self._detector, on_event=on_event, sleep=self._sleep, monotonic=self._monotonic)
        try:
            result = await browser.open_page()
            if result.check_result is CheckResult.LOGIN_REQUIRED and settings.interactive_login:
                result = await browser.request_human_login()
            return result
        finally:
            await browser.close()

    async def aclose(self) -> None:
        close = getattr(self._launcher, "aclose", None)
        if close is not None:
            await close()
