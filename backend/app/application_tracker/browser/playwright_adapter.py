"""Lazy adapter around Playwright's persistent Chromium context."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any


class BrowserDependencyError(RuntimeError):
    """Raised when the optional Playwright runtime is unavailable."""


class PlaywrightPersistentContextLauncher:
    def __init__(self) -> None:
        self._playwright: Any | None = None
        self._start_lock = asyncio.Lock()

    async def _get_playwright(self) -> Any:
        if self._playwright is not None:
            return self._playwright
        async with self._start_lock:
            if self._playwright is not None:
                return self._playwright
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:
                raise BrowserDependencyError("Playwright is not installed; sync the backend browser extra") from exc
            self._playwright = await async_playwright().start()
            return self._playwright

    async def launch(
        self,
        profile_dir: Path,
        *,
        headless: bool,
        timeout_ms: int,
    ) -> Any:
        playwright = await self._get_playwright()
        try:
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=headless,
            )
        except Exception as exc:
            raise BrowserDependencyError("Chromium could not be started") from exc
        context.set_default_timeout(timeout_ms)
        context.set_default_navigation_timeout(timeout_ms)
        return context

    async def aclose(self) -> None:
        if self._playwright is None:
            return
        playwright, self._playwright = self._playwright, None
        await playwright.stop()
