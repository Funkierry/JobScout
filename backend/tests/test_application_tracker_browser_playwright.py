from __future__ import annotations

import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.application_tracker.browser.live_session import PersistentAgentBrowserFactory
from app.application_tracker.browser.models import BrowserAccessConfig
from app.application_tracker.browser.service import PersistentBrowserService
from app.application_tracker.models import CheckResult

pytest.importorskip("playwright.async_api")


class QuietFixtureHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def do_GET(self) -> None:
        if self.path == "/seed-session":
            self._send_html(
                "Session seeded",
                cookie="application_tracker_session=ready; Max-Age=3600; Path=/; HttpOnly",
            )
            return
        if self.path == "/protected-status":
            if "application_tracker_session=ready" in self.headers.get("Cookie", ""):
                self._send_html("Current application status: First interview")
            else:
                self._send_html('<input type="password" aria-label="Password">')
            return
        super().do_GET()

    def _send_html(self, body: str, *, cookie: str | None = None) -> None:
        payload = f"<!doctype html><html><body>{body}</body></html>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(payload)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_real_playwright_reads_a_local_status_fixture(tmp_path: Path) -> None:
    fixture_dir = Path(__file__).parent / "fixtures" / "application_tracker" / "browser"
    handler = partial(QuietFixtureHandler, directory=str(fixture_dir))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    service = PersistentBrowserService()
    try:
        port = server.server_address[1]
        result = await service.fetch(
            f"http://127.0.0.1:{port}/status.html",
            user_id="integration-test-user",
            config=BrowserAccessConfig(
                profile_root=tmp_path / "browser_profile",
                allow_private_addresses=True,
            ),
        )
    finally:
        await service.aclose()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    if result.error_code == "browser_unavailable":
        pytest.skip("Playwright Chromium is not installed")
    assert result.check_result is CheckResult.SUCCESS
    assert result.page_text == "Current application status: Assessment"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_real_playwright_reuses_cookie_from_the_same_profile(
    tmp_path: Path,
) -> None:
    fixture_dir = Path(__file__).parent / "fixtures" / "application_tracker" / "browser"
    handler = partial(QuietFixtureHandler, directory=str(fixture_dir))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    service = PersistentBrowserService()
    config = BrowserAccessConfig(
        profile_root=tmp_path / "browser_profile",
        allow_private_addresses=True,
        login_timeout_seconds=0,
    )
    try:
        port = server.server_address[1]
        seeded = await service.fetch(
            f"http://127.0.0.1:{port}/seed-session",
            user_id="integration-test-user",
            config=config,
        )
        protected = await service.fetch(
            f"http://127.0.0.1:{port}/protected-status",
            user_id="integration-test-user",
            config=config,
        )
    finally:
        await service.aclose()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    if seeded.error_code == "browser_unavailable":
        pytest.skip("Playwright Chromium is not installed")
    assert seeded.check_result is CheckResult.SUCCESS
    assert protected.check_result is CheckResult.SUCCESS
    assert protected.page_text == "Current application status: First interview"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_real_agent_browser_can_observe_click_and_capture(
    tmp_path: Path,
) -> None:
    fixture_dir = Path(__file__).parent / "fixtures" / "application_tracker" / "browser"
    handler = partial(QuietFixtureHandler, directory=str(fixture_dir))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    factory = PersistentAgentBrowserFactory()
    port = server.server_address[1]
    browser = factory.create(
        url=f"http://127.0.0.1:{port}/agent_details.html",
        user_id="agent-integration-test-user",
        config=BrowserAccessConfig(
            profile_root=tmp_path / "browser_profile",
            allow_private_addresses=True,
        ),
    )
    try:
        opened = await browser.open_page()
        elements = await browser.get_interactive_elements()
        detail_button = next(item for item in elements if item.name == "View details")
        await browser.click(detail_button.ref)
        updated_text = await browser.get_page_text()
        screenshot = await browser.screenshot()
    finally:
        await browser.close()
        await factory.aclose()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    if opened.error_code == "browser_unavailable":
        pytest.skip("Playwright Chromium is not installed")
    assert opened.check_result is CheckResult.SUCCESS
    assert "Current status: Second interview" in updated_text
    assert screenshot.startswith(b"\x89PNG\r\n\x1a\n")
