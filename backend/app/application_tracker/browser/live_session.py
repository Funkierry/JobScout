"""Live persistent browser session used by the tracker agent tools."""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.application_tracker.browser.detector import LoginDetector
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


@dataclass(frozen=True, slots=True)
class InteractiveElement:
    """A bounded, selector-free element reference exposed to the model."""

    ref: int
    role: str
    name: str


class AgentBrowser(Protocol):
    async def open_page(self) -> BrowserAccessResult: ...

    async def get_page_text(self) -> str: ...

    async def get_interactive_elements(self) -> list[InteractiveElement]: ...

    async def click(self, ref: int) -> None: ...

    async def screenshot(self) -> bytes: ...

    async def request_human_login(self) -> BrowserAccessResult: ...

    async def close(self) -> None: ...


class AgentBrowserFactory(Protocol):
    def create(
        self,
        *,
        url: str,
        user_id: str,
        config: BrowserAccessConfig,
        on_event: EventHandler | None = None,
    ) -> AgentBrowser: ...


class PersistentAgentBrowser:
    """One live page whose persistent profile survives human-login relaunches."""

    _ELEMENT_ATTRIBUTE = "data-application-tracker-ref"

    def __init__(
        self,
        *,
        url: str,
        profile_dir: Path,
        profile_lock: asyncio.Lock,
        launcher: Any,
        config: BrowserAccessConfig,
        detector: LoginDetector,
        on_event: EventHandler | None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = url
        self._profile_dir = profile_dir
        self._profile_lock = profile_lock
        self._launcher = launcher
        self._config = config
        self._detector = detector
        self._on_event = on_event
        self._sleep = sleep
        self._monotonic = monotonic
        self._context: Any | None = None
        self._page: Any | None = None
        self._lock_acquired = False

    async def open_page(self) -> BrowserAccessResult:
        url_error = await validate_navigation_url(
            self._url,
            allow_private_addresses=self._config.allow_private_addresses,
        )
        if url_error:
            return self._failed("unsafe_url")
        try:
            await self._ensure_profile_lock()
            await asyncio.to_thread(
                self._profile_dir.mkdir,
                parents=True,
                exist_ok=True,
            )
            if self._context is None:
                await self._open_context(headless=self._config.headless)
            await self._navigate_target()
            return await self._current_result(login_attempted=False)
        except BrowserDependencyError:
            return self._failed("browser_unavailable")
        except Exception:
            return self._failed("navigation_failed")

    async def get_page_text(self) -> str:
        page = self._require_page()
        return await page.locator("body").inner_text(timeout=self._config.page_text_timeout_ms)

    async def get_interactive_elements(self) -> list[InteractiveElement]:
        page = self._require_page()
        raw_elements = await page.evaluate(
            r"""attribute => {
                const selector = [
                    'a[href]', 'button', 'summary',
                    '[role="button"]', '[role="link"]',
                    'input[type="button"]', 'input[type="submit"]'
                ].join(',');
                const visible = element => {
                    const rect = element.getBoundingClientRect();
                    const style = window.getComputedStyle(element);
                    return rect.width > 0 && rect.height > 0 &&
                        style.visibility !== 'hidden' && style.display !== 'none';
                };
                return Array.from(document.querySelectorAll(selector))
                    .filter(visible)
                    .slice(0, 100)
                    .map((element, index) => {
                        element.setAttribute(attribute, String(index + 1));
                        const role = element.getAttribute('role') ||
                            element.tagName.toLowerCase();
                        const name = (
                            element.getAttribute('aria-label') ||
                            element.innerText ||
                            element.getAttribute('value') ||
                            element.getAttribute('title') || ''
                        ).trim().replace(/\s+/g, ' ').slice(0, 200);
                        return {ref: index + 1, role, name};
                    });
            }""",
            self._ELEMENT_ATTRIBUTE,
        )
        return [
            InteractiveElement(
                ref=int(item["ref"]),
                role=str(item["role"]),
                name=str(item["name"]),
            )
            for item in raw_elements
            if isinstance(item, dict)
        ]

    async def click(self, ref: int) -> None:
        if ref <= 0:
            raise ValueError("ref must be positive")
        page = self._require_page()
        locator = page.locator(f'[{self._ELEMENT_ATTRIBUTE}="{ref}"]')
        if await locator.count() != 1:
            raise ValueError("element ref is missing or stale; call get_page_text again")
        await locator.click(timeout=self._config.navigation_timeout_ms)
        try:
            await page.wait_for_load_state(
                "domcontentloaded",
                timeout=min(self._config.navigation_timeout_ms, 3_000),
            )
        except Exception:
            pass

    async def screenshot(self) -> bytes:
        page = self._require_page()
        return await page.screenshot(type="png", full_page=False)

    async def request_human_login(self) -> BrowserAccessResult:
        try:
            await self._ensure_profile_lock()
            await self._close_context()
            await self._open_context(headless=False)
            await self._navigate_target()
            await self._emit(
                BrowserEvent(
                    BrowserEventType.HUMAN_LOGIN_REQUIRED,
                    "请在弹出的浏览器中完成登录；验证码和风控需要人工处理。",
                )
            )
            deadline = self._monotonic() + self._config.login_timeout_seconds
            challenge_announced = False
            initial_settle = min(
                self._config.login_poll_interval_seconds,
                self._config.login_timeout_seconds,
            )
            if initial_settle > 0:
                await self._sleep(initial_settle)
            while True:
                self._page = await self._active_page()
                inspection = await self._detector.inspect(self._page)
                if inspection.state is LoginState.AUTHENTICATED:
                    await self._navigate_target()
                    result = await self._current_result(login_attempted=True)
                    if result.check_result is CheckResult.SUCCESS:
                        await self._emit(
                            BrowserEvent(
                                BrowserEventType.LOGIN_COMPLETED,
                                "登录完成，正在读取申请页面。",
                            )
                        )
                        return result
                    if result.check_result is CheckResult.FETCH_FAILED:
                        return result
                if inspection.state is LoginState.HUMAN_CHALLENGE and not challenge_announced:
                    challenge_announced = True
                    await self._emit(
                        BrowserEvent(
                            BrowserEventType.HUMAN_CHALLENGE,
                            "检测到验证码或风控，请人工处理；系统不会尝试绕过。",
                        )
                    )
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    await self._emit(
                        BrowserEvent(
                            BrowserEventType.LOGIN_TIMED_OUT,
                            "登录等待超时，本条记录标记为需登录。",
                        )
                    )
                    await self._close_context()
                    return BrowserAccessResult(
                        page_text="",
                        check_result=CheckResult.LOGIN_REQUIRED,
                        login_state=inspection.state,
                        login_attempted=True,
                        error_code="login_timeout",
                    )
                await self._sleep(min(self._config.login_poll_interval_seconds, remaining))

        except BrowserDependencyError:
            return self._failed("browser_unavailable", login_attempted=True)
        except Exception:
            return self._failed("human_login_failed", login_attempted=True)

    async def close(self) -> None:
        await self._close_context()
        if self._lock_acquired:
            self._profile_lock.release()
            self._lock_acquired = False

    async def _ensure_profile_lock(self) -> None:
        if self._lock_acquired:
            return
        await self._profile_lock.acquire()
        self._lock_acquired = True

    async def _open_context(self, *, headless: bool) -> None:
        self._context = await self._launcher.launch(
            self._profile_dir,
            headless=headless,
            timeout_ms=self._config.navigation_timeout_ms,
        )

        async def handler(route: Any) -> None:
            await guard_route(
                route,
                allow_private_addresses=self._config.allow_private_addresses,
            )

        await self._context.route("**/*", handler)
        self._page = await self._active_page()

    async def _navigate_target(self) -> None:
        page = self._require_page()
        await page.goto(
            self._url,
            wait_until="domcontentloaded",
            timeout=self._config.navigation_timeout_ms,
        )
        try:
            await page.wait_for_load_state(
                "networkidle",
                timeout=min(self._config.navigation_timeout_ms, 3_000),
            )
        except Exception:
            pass
        # Recruitment portals often render an authenticated-looking shell and
        # apply a client-side login redirect only after network-idle fires.
        await page.wait_for_timeout(min(self._config.page_text_timeout_ms, 2_000))

    async def _current_result(self, *, login_attempted: bool) -> BrowserAccessResult:
        page = self._require_page()
        inspection = await self._detector.inspect(page)
        if inspection.state is not LoginState.AUTHENTICATED:
            return BrowserAccessResult(
                page_text="",
                check_result=CheckResult.LOGIN_REQUIRED,
                login_state=inspection.state,
                login_attempted=login_attempted,
            )
        return BrowserAccessResult(
            page_text=await self.get_page_text(),
            check_result=CheckResult.SUCCESS,
            login_state=LoginState.AUTHENTICATED,
            login_attempted=login_attempted,
        )

    async def _active_page(self) -> Any:
        if self._context is None:
            raise RuntimeError("browser context is not open")
        if self._context.pages:
            return self._context.pages[-1]
        return await self._context.new_page()

    def _require_page(self) -> Any:
        if self._page is None:
            raise RuntimeError("open_page must be called first")
        return self._page

    async def _close_context(self) -> None:
        context, self._context, self._page = self._context, None, None
        if context is None:
            return
        try:
            await context.close()
        except Exception:
            pass

    async def _emit(self, event: BrowserEvent) -> None:
        if self._on_event is None:
            return
        maybe_awaitable = self._on_event(event)
        if inspect.isawaitable(maybe_awaitable):
            await maybe_awaitable

    @staticmethod
    def _failed(
        error_code: str,
        *,
        login_attempted: bool = False,
    ) -> BrowserAccessResult:
        return BrowserAccessResult(
            page_text="",
            check_result=CheckResult.FETCH_FAILED,
            login_state=LoginState.UNKNOWN,
            login_attempted=login_attempted,
            error_code=error_code,
        )


class PersistentAgentBrowserFactory:
    """Create live sessions while serializing access to each Chromium profile."""

    def __init__(self, launcher: Any | None = None) -> None:
        self._launcher = launcher or PlaywrightPersistentContextLauncher()
        self._detector = LoginDetector()
        self._profile_locks: dict[Path, asyncio.Lock] = {}

    def create(
        self,
        *,
        url: str,
        user_id: str,
        config: BrowserAccessConfig,
        on_event: EventHandler | None = None,
    ) -> PersistentAgentBrowser:
        path = profile_directory(config.profile_root, user_id=user_id, url=url)
        lock = self._profile_locks.setdefault(path, asyncio.Lock())
        return PersistentAgentBrowser(
            url=url,
            profile_dir=path,
            profile_lock=lock,
            launcher=self._launcher,
            config=config,
            detector=self._detector,
            on_event=on_event,
        )

    async def aclose(self) -> None:
        close = getattr(self._launcher, "aclose", None)
        if close is not None:
            await close()
