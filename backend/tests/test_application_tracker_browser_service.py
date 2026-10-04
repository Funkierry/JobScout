from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from app.application_tracker.browser.live_session import PersistentAgentBrowserFactory
from app.application_tracker.browser.models import (
    BrowserAccessConfig,
    BrowserEvent,
    BrowserEventType,
)
from app.application_tracker.browser.network import guard_route
from app.application_tracker.browser.service import PersistentBrowserService
from app.application_tracker.models import CheckResult


class FakeLocator:
    def __init__(self, page: FakePage, selector: str) -> None:
        self.page = page
        self.selector = selector

    async def count(self) -> int:
        if self.selector == 'input[type="password"]':
            return int(self.page.has_password)
        if self.selector == "body":
            return 1
        return int(self.page.has_challenge)

    async def inner_text(self, *, timeout: float | None = None) -> str:
        del timeout
        return self.page.text


class FakePage:
    def __init__(
        self,
        url: str,
        *,
        text: str = "",
        has_password: bool = False,
        has_challenge: bool = False,
    ) -> None:
        self.url = url
        self.text = text
        self.has_password = has_password
        self.has_challenge = has_challenge
        self.visited: list[str] = []
        self.load_state_waits: list[tuple[str, int]] = []
        self.timeout_waits: list[int] = []

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    async def goto(self, url: str, **kwargs: Any) -> None:
        del kwargs
        self.visited.append(url)

    async def wait_for_load_state(self, state: str, *, timeout: int) -> None:
        self.load_state_waits.append((state, timeout))

    async def wait_for_timeout(self, timeout: int) -> None:
        self.timeout_waits.append(timeout)


class FakeContext:
    def __init__(self, page: FakePage) -> None:
        self.pages = [page]
        self.closed = False
        self.routes: list[tuple[str, Callable[..., Awaitable[None]]]] = []

    async def new_page(self) -> FakePage:
        return self.pages[-1]

    async def route(
        self,
        pattern: str,
        handler: Callable[..., Awaitable[None]],
    ) -> None:
        self.routes.append((pattern, handler))

    async def close(self) -> None:
        self.closed = True


class FakeLauncher:
    def __init__(self, contexts: list[FakeContext]) -> None:
        self.contexts = contexts
        self.calls: list[tuple[Path, bool, int]] = []

    async def launch(
        self,
        profile_dir: Path,
        *,
        headless: bool,
        timeout_ms: int,
    ) -> FakeContext:
        self.calls.append((profile_dir, headless, timeout_ms))
        return self.contexts[len(self.calls) - 1]


class HeadedLaunchFailure(FakeLauncher):
    async def launch(
        self,
        profile_dir: Path,
        *,
        headless: bool,
        timeout_ms: int,
    ) -> FakeContext:
        if not headless:
            raise RuntimeError("headed browser unavailable")
        return await super().launch(
            profile_dir,
            headless=headless,
            timeout_ms=timeout_ms,
        )


class FakeRequest:
    def __init__(self, url: str) -> None:
        self.url = url


class FakeRoute:
    def __init__(self, url: str) -> None:
        self.request = FakeRequest(url)
        self.aborted = False
        self.continued = False

    async def abort(self, error_code: str = "failed") -> None:
        del error_code
        self.aborted = True

    async def continue_(self) -> None:
        self.continued = True


def _config(tmp_path: Path, **overrides: Any) -> BrowserAccessConfig:
    values = {"allow_private_addresses": True, **overrides}
    return BrowserAccessConfig(profile_root=tmp_path, **values)


@pytest.mark.asyncio
async def test_public_page_uses_one_headless_context_and_no_llm(tmp_path: Path) -> None:
    page = FakePage(
        "https://jobs.example.com/applications/42",
        text="Current application status: Assessment",
    )
    context = FakeContext(page)
    launcher = FakeLauncher([context])
    service = PersistentBrowserService(launcher)

    result = await service.fetch(
        "https://jobs.example.com/applications/42",
        user_id="local-user",
        config=_config(tmp_path, interactive_login=False),
    )

    assert result.check_result is CheckResult.SUCCESS
    assert result.page_text == "Current application status: Assessment"
    assert [headless for _, headless, _ in launcher.calls] == [True]
    assert context.closed
    assert context.routes[0][0] == "**/*"


@pytest.mark.asyncio
async def test_login_reuses_one_headed_context_for_reading(tmp_path: Path) -> None:
    headed = FakePage(
        "https://accounts.example.com/login",
        has_password=True,
        text="Sign in",
    )
    contexts = [FakeContext(headed)]
    launcher = FakeLauncher(contexts)
    events: list[BrowserEvent] = []

    async def complete_login(_: float) -> None:
        headed.url = "https://jobs.example.com/applications/42"
        headed.has_password = False
        headed.text = "Current application status: First interview"

    service = PersistentBrowserService(launcher, sleep=complete_login)
    result = await service.fetch(
        "https://jobs.example.com/applications/42",
        user_id="local-user",
        config=_config(
            tmp_path,
            login_poll_interval_seconds=0.01,
            login_timeout_seconds=1,
        ),
        on_event=events.append,
    )

    assert result.check_result is CheckResult.SUCCESS
    assert result.login_attempted
    assert result.page_text == "Current application status: First interview"
    assert [headless for _, headless, _ in launcher.calls] == [False]
    assert len({profile for profile, _, _ in launcher.calls}) == 1
    assert all(context.closed for context in contexts)
    assert [event.type for event in events] == [
        BrowserEventType.HUMAN_LOGIN_REQUIRED,
        BrowserEventType.LOGIN_COMPLETED,
    ]


@pytest.mark.asyncio
async def test_blank_headed_shell_waits_for_login_page_before_deciding(tmp_path: Path) -> None:
    headed = FakePage(
        "https://accounts.example.com/login",
        text="",
    )
    contexts = [FakeContext(headed)]
    launcher = FakeLauncher(contexts)
    poll_count = 0

    async def advance_login(_: float) -> None:
        nonlocal poll_count
        poll_count += 1
        if poll_count == 1:
            headed.url = "https://accounts.example.com/login"
            headed.has_password = True
            headed.text = "Sign in"
        else:
            headed.url = "https://jobs.example.com/applications/42"
            headed.has_password = False
            headed.text = "Current application status: First interview"

    service = PersistentBrowserService(launcher, sleep=advance_login)
    result = await service.fetch(
        "https://jobs.example.com/applications/42",
        user_id="local-user",
        config=_config(
            tmp_path,
            login_poll_interval_seconds=0.01,
            login_timeout_seconds=1,
        ),
    )

    assert result.check_result is CheckResult.SUCCESS
    assert poll_count == 2
    assert [headless for _, headless, _ in launcher.calls] == [False]


@pytest.mark.asyncio
async def test_agent_browser_keeps_the_authenticated_headed_context(tmp_path: Path) -> None:
    headed = FakePage(
        "https://accounts.example.com/login",
        has_password=True,
        text="Sign in",
    )
    launcher = FakeLauncher([FakeContext(headed)])

    async def complete_login(_: float) -> None:
        headed.url = "https://jobs.example.com/applications/42"
        headed.has_password = False
        headed.text = "Current application status: First interview"

    factory = PersistentAgentBrowserFactory(launcher)
    browser = factory.create(
        url="https://jobs.example.com/applications/42",
        user_id="local-user",
        config=_config(
            tmp_path,
            login_poll_interval_seconds=0.01,
            login_timeout_seconds=1,
        ),
    )
    browser._sleep = complete_login
    try:
        opened = await browser.open_page()
        result = await browser.request_human_login()
    finally:
        await browser.close()

    assert opened.check_result is CheckResult.LOGIN_REQUIRED
    assert result.check_result is CheckResult.SUCCESS
    assert result.page_text == "Current application status: First interview"
    assert [headless for _, headless, _ in launcher.calls] == [False]
    assert headed.load_state_waits == [("networkidle", 3000), ("networkidle", 3000)]
    assert headed.timeout_waits == []


@pytest.mark.asyncio
async def test_agent_browser_opens_visible_login_from_portal_homepage(tmp_path: Path) -> None:
    headed = FakePage("https://campus.example.com/", text="校园招聘\n登录 / 注册")
    launcher = FakeLauncher([FakeContext(headed)])

    async def complete_login(_: float) -> None:
        headed.text = "我的投递\n当前进度：笔试"

    factory = PersistentAgentBrowserFactory(launcher)
    browser = factory.create(
        url="https://campus.example.com/",
        user_id="local-user",
        config=_config(tmp_path, login_poll_interval_seconds=0.01, login_timeout_seconds=1),
    )
    browser._sleep = complete_login
    try:
        opened = await browser.open_page()
        result = await browser.request_human_login()
    finally:
        await browser.close()

    assert opened.check_result is CheckResult.LOGIN_REQUIRED
    assert result.check_result is CheckResult.SUCCESS
    assert [headless for _, headless, _ in launcher.calls] == [False]


@pytest.mark.asyncio
async def test_login_timeout_returns_login_required_and_skips_reopen(tmp_path: Path) -> None:
    headed = FakePage("https://accounts.example.com/login", has_password=True)
    contexts = [FakeContext(headed)]
    launcher = FakeLauncher(contexts)
    service = PersistentBrowserService(launcher)

    result = await service.fetch(
        "https://jobs.example.com/applications/42",
        user_id="local-user",
        config=_config(tmp_path, login_timeout_seconds=0),
    )

    assert result.check_result is CheckResult.LOGIN_REQUIRED
    assert result.login_attempted
    assert [headless for _, headless, _ in launcher.calls] == [False]
    assert all(context.closed for context in contexts)


@pytest.mark.asyncio
async def test_headed_browser_failure_is_not_misreported_as_login_timeout(
    tmp_path: Path,
) -> None:
    initial = FakePage("https://accounts.example.com/login", has_password=True)
    launcher = HeadedLaunchFailure([FakeContext(initial)])
    service = PersistentBrowserService(launcher)

    result = await service.fetch(
        "https://jobs.example.com/applications/42",
        user_id="local-user",
        config=_config(tmp_path),
    )

    assert result.check_result is CheckResult.FETCH_FAILED
    assert result.error_code == "navigation_failed"
    assert not result.login_attempted


@pytest.mark.asyncio
async def test_private_initial_url_is_rejected_before_browser_launch(tmp_path: Path) -> None:
    launcher = FakeLauncher([])
    service = PersistentBrowserService(launcher)

    result = await service.fetch(
        "http://127.0.0.1/internal",
        user_id="local-user",
        config=_config(tmp_path, allow_private_addresses=False),
    )

    assert result.check_result is CheckResult.FETCH_FAILED
    assert result.error_code == "unsafe_url"
    assert launcher.calls == []


@pytest.mark.asyncio
async def test_headless_batch_login_does_not_open_a_second_window(tmp_path):
    page = FakePage("https://accounts.example.com/login", has_password=True)
    launcher = FakeLauncher([FakeContext(page)])
    factory = PersistentAgentBrowserFactory(launcher)
    browser = factory.create(url="https://jobs.example.com/applications", user_id="u", config=_config(tmp_path, interactive_login=False))
    try:
        first = await browser.open_page()
        again = await browser.request_human_login()
        assert first.check_result is again.check_result is CheckResult.LOGIN_REQUIRED
        assert again.error_code == "interactive_login_required"
        assert [headless for _, headless, _ in launcher.calls] == [True]
    finally:
        await browser.close()


@pytest.mark.asyncio
async def test_profile_lock_spans_request_factories_and_releases_on_cancel(tmp_path):
    import asyncio

    first_launcher = FakeLauncher([FakeContext(FakePage("https://jobs.example.com/applications", text="Current status: Assessment"))])
    second_launcher = FakeLauncher([FakeContext(FakePage("https://jobs.example.com/applications", text="Current status: Assessment"))])
    args = {"url": "https://jobs.example.com/applications", "user_id": "u", "config": _config(tmp_path, interactive_login=False)}
    first = PersistentAgentBrowserFactory(first_launcher).create(**args)
    second = PersistentAgentBrowserFactory(second_launcher).create(**args)
    assert first._profile_lock is second._profile_lock
    await first.open_page()
    assert first._profile_lock.locked()
    task = asyncio.create_task(second.open_page())
    # Wait until the coroutine is actually blocked on the owned lock.
    await asyncio.sleep(0)
    assert not second_launcher.calls
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await second.close()
    assert first._profile_lock.locked()
    await first.close()
    assert not first._profile_lock.locked()
    try:
        assert (await second.open_page()).check_result is CheckResult.SUCCESS
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_request_guard_blocks_private_redirects_and_subresources() -> None:
    private_route = FakeRoute("http://127.0.0.1/internal")
    public_route = FakeRoute("https://1.1.1.1/status")

    await guard_route(private_route, allow_private_addresses=False)
    await guard_route(public_route, allow_private_addresses=False)

    assert private_route.aborted
    assert not private_route.continued
    assert public_route.continued
    assert not public_route.aborted
