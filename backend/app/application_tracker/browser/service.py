"""Persistent browser lifecycle with explicit human-login handoff."""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol

from app.application_tracker.browser.detector import LoginDetector, PageLike
from app.application_tracker.browser.models import (
    BrowserAccessConfig,
    BrowserAccessResult,
    BrowserEvent,
    BrowserEventType,
    LoginState,
)
from app.application_tracker.browser.network import guard_route, validate_navigation_url
from app.application_tracker.browser.playwright_adapter import (
    BrowserDependencyError,
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
        self._profile_locks: dict[Path, asyncio.Lock] = {}

    async def fetch(
        self,
        url: str,
        *,
        user_id: str,
        config: BrowserAccessConfig | None = None,
        on_event: EventHandler | None = None,
    ) -> BrowserAccessResult:
        settings = config or BrowserAccessConfig.from_env()
        url_error = await validate_navigation_url(url, allow_private_addresses=settings.allow_private_addresses)
        if url_error:
            return self._failed("unsafe_url")

        profile_dir = profile_directory(settings.profile_root, user_id=user_id, url=url)
        await asyncio.to_thread(profile_dir.mkdir, parents=True, exist_ok=True)
        lock = self._profile_locks.setdefault(profile_dir, asyncio.Lock())
        async with lock:
            return await self._fetch_locked(
                url,
                profile_dir=profile_dir,
                config=settings,
                on_event=on_event,
            )

    async def aclose(self) -> None:
        close = getattr(self._launcher, "aclose", None)
        if close is not None:
            await close()

    async def _fetch_locked(
        self,
        url: str,
        *,
        profile_dir: Path,
        config: BrowserAccessConfig,
        on_event: EventHandler | None,
    ) -> BrowserAccessResult:
        first = await self._open_and_read(
            url,
            profile_dir=profile_dir,
            headless=config.headless,
            config=config,
            login_attempted=False,
        )
        if first.login_state is LoginState.AUTHENTICATED:
            return first
        if first.check_result is CheckResult.FETCH_FAILED:
            return first

        logged_in, login_error = await self._wait_for_human_login(
            url,
            profile_dir=profile_dir,
            config=config,
            on_event=on_event,
        )
        if not logged_in:
            if login_error != "login_timeout":
                return self._failed(
                    login_error or "human_login_failed",
                    login_attempted=True,
                )
            return BrowserAccessResult(
                page_text="",
                check_result=CheckResult.LOGIN_REQUIRED,
                login_state=first.login_state,
                login_attempted=True,
                error_code="login_timeout",
            )

        return await self._open_and_read(
            url,
            profile_dir=profile_dir,
            headless=config.headless,
            config=config,
            login_attempted=True,
        )

    async def _open_and_read(
        self,
        url: str,
        *,
        profile_dir: Path,
        headless: bool,
        config: BrowserAccessConfig,
        login_attempted: bool,
    ) -> BrowserAccessResult:
        context: BrowserContextLike | None = None
        try:
            context = await self._launcher.launch(
                profile_dir,
                headless=headless,
                timeout_ms=config.navigation_timeout_ms,
            )
            await self._install_network_guard(context, config)
            page = await self._active_page(context)
            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=config.navigation_timeout_ms,
            )
            inspection = await self._detector.inspect(page)
            if inspection.state is not LoginState.AUTHENTICATED:
                return BrowserAccessResult(
                    page_text="",
                    check_result=CheckResult.LOGIN_REQUIRED,
                    login_state=inspection.state,
                    login_attempted=login_attempted,
                )
            page_text = await page.locator("body").inner_text(timeout=config.page_text_timeout_ms)
            return BrowserAccessResult(
                page_text=page_text,
                check_result=CheckResult.SUCCESS,
                login_state=LoginState.AUTHENTICATED,
                login_attempted=login_attempted,
            )
        except BrowserDependencyError:
            return self._failed("browser_unavailable", login_attempted=login_attempted)
        except Exception:
            return self._failed("navigation_failed", login_attempted=login_attempted)
        finally:
            await self._close_context(context)

    async def _wait_for_human_login(
        self,
        url: str,
        *,
        profile_dir: Path,
        config: BrowserAccessConfig,
        on_event: EventHandler | None,
    ) -> tuple[bool, str | None]:
        context: BrowserContextLike | None = None
        challenge_announced = False
        try:
            context = await self._launcher.launch(
                profile_dir,
                headless=False,
                timeout_ms=config.navigation_timeout_ms,
            )
            await self._install_network_guard(context, config)
            page = await self._active_page(context)
            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=config.navigation_timeout_ms,
            )
            await self._emit(
                on_event,
                BrowserEvent(
                    BrowserEventType.HUMAN_LOGIN_REQUIRED,
                    "请在弹出的浏览器中完成登录；验证码和风控需要人工处理。",
                ),
            )
            deadline = self._monotonic() + config.login_timeout_seconds
            initial_settle = min(
                config.login_poll_interval_seconds,
                config.login_timeout_seconds,
            )
            if initial_settle > 0:
                await self._sleep(initial_settle)
            while True:
                page = await self._active_page(context)
                inspection = await self._detector.inspect(page)
                if inspection.state is LoginState.AUTHENTICATED:
                    await self._emit(
                        on_event,
                        BrowserEvent(
                            BrowserEventType.LOGIN_COMPLETED,
                            "登录完成，正在切回无头浏览器。",
                        ),
                    )
                    return True, None
                if inspection.state is LoginState.HUMAN_CHALLENGE and not challenge_announced:
                    challenge_announced = True
                    await self._emit(
                        on_event,
                        BrowserEvent(
                            BrowserEventType.HUMAN_CHALLENGE,
                            "检测到验证码或风控，请在浏览器中人工完成；系统不会尝试绕过。",
                        ),
                    )
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    await self._emit(
                        on_event,
                        BrowserEvent(
                            BrowserEventType.LOGIN_TIMED_OUT,
                            "登录等待超时，本条记录标记为需登录。",
                        ),
                    )
                    return False, "login_timeout"
                await self._sleep(min(config.login_poll_interval_seconds, remaining))
        except Exception:
            return False, "human_login_failed"
        finally:
            await self._close_context(context)

    async def _install_network_guard(self, context: BrowserContextLike, config: BrowserAccessConfig) -> None:
        async def handler(route: Any) -> None:
            await guard_route(route, allow_private_addresses=config.allow_private_addresses)

        await context.route("**/*", handler)

    async def _active_page(self, context: BrowserContextLike) -> PageLike:
        if context.pages:
            return context.pages[-1]
        return await context.new_page()

    async def _emit(self, handler: EventHandler | None, event: BrowserEvent) -> None:
        if handler is None:
            return
        maybe_awaitable = handler(event)
        if inspect.isawaitable(maybe_awaitable):
            await maybe_awaitable

    async def _close_context(self, context: BrowserContextLike | None) -> None:
        if context is None:
            return
        try:
            await context.close()
        except Exception:
            pass

    def _failed(self, error_code: str, *, login_attempted: bool = False) -> BrowserAccessResult:
        return BrowserAccessResult(
            page_text="",
            check_result=CheckResult.FETCH_FAILED,
            login_state=LoginState.UNKNOWN,
            login_attempted=login_attempted,
            error_code=error_code,
        )
