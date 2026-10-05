"""Offline browser check of same-origin deployment and first-admin setup."""

import json
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright
from test_ui import ROOT


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(service_workers="block")
        page = context.new_page()
        errors, unexpected, writes = [], [], []
        state = {"initialized": False, "authenticated": False, "available": True}
        page.on("pageerror", lambda error: errors.append(str(error)))

        def route(r):
            request = r.request
            url = urlsplit(request.url)
            path = url.path

            def reply(body, status=200):
                r.fulfill(
                    status=status,
                    content_type="application/json",
                    body=json.dumps(body),
                )

            if url.netloc != "jobs.example.test":
                unexpected.append(request.url)
                r.abort()
            elif path in ("/", "/app.js", "/runtime-config.js", "/style.css"):
                name = "index.html" if path == "/" else path[1:]
                r.fulfill(
                    path=str(ROOT / name),
                    content_type={
                        "index.html": "text/html",
                        "app.js": "text/javascript",
                        "runtime-config.js": "text/javascript",
                        "style.css": "text/css",
                    }[name],
                )
            elif path == "/api/v1/auth/me":
                reply(
                    {"email": "owner@example.test"},
                    200 if state["authenticated"] else 401,
                )
            elif path == "/api/v1/auth/setup-status":
                reply(
                    {
                        "needs_setup": not state["initialized"],
                        "registration_enabled": False,
                    },
                    200 if state["available"] else 503,
                )
            elif path in ("/api/v1/auth/initialize", "/api/v1/auth/login/local"):
                writes.append(path)
                state.update(initialized=True, authenticated=True)
                reply({"email": "owner@example.test"})
            elif path == "/api/v1/auth/logout":
                state["authenticated"] = False
                reply({})
            elif path == "/api/integrations/lark/status":
                reply({"installed": False})
            elif path in (
                "/api/threads/search",
                "/api/jobscout/opportunities",
                "/api/jobscout/tracker/stages",
                "/api/jobscout/tracker/applications",
                "/api/jobscout/tracker/notifications",
            ):
                reply([])
            elif path == "/favicon.ico":
                r.fulfill(status=204)
            else:
                unexpected.append(path)
                r.abort()

        context.route("**/*", route)
        page.goto("https://jobs.example.test/", wait_until="networkidle")
        expect(page.locator("#authTitle")).to_have_text("创建你的管理员账号")
        expect(page.locator("#authToggle")).to_be_hidden()
        page.locator("#authEmail").fill("owner@example.test")
        page.locator("#authPassword").fill("Synthetic-Fixture-Password-42!")
        page.locator("#authSubmit").click()
        expect(page.locator("#chatView")).to_be_visible()
        assert writes == ["/api/v1/auth/initialize"]
        assert (
            page.locator("#capabilityCenterLink").get_attribute("href")
            == "/workspace/capabilities"
        )
        page.locator("#logoutBtn").click()
        expect(page.locator("#authTitle")).to_have_text("登录你的求职工作台")
        expect(page.locator("#authToggle")).to_be_hidden()
        page.locator("#authSubmit").click()
        expect(page.locator("#chatView")).to_be_visible()
        assert writes[-1] == "/api/v1/auth/login/local"

        # A setup-status outage must neither expose registration nor send a
        # password to the wrong endpoint. Retry works with empty required fields.
        state.update(authenticated=False, available=False)
        page.reload(wait_until="networkidle")
        expect(page.locator("#authSubmit")).to_have_text("重试连接")
        assert page.locator("#authEmail").input_value() == ""
        state["available"] = True
        page.locator("#authSubmit").click()
        expect(page.locator("#authSubmit")).to_have_text("登录")
        assert len(writes) == 2
        assert not unexpected and not errors, (unexpected, errors)
        browser.close()
        print(
            "PASS: HTTPS same-origin, administrator setup, closed registration, login/logout and connection retry"
        )


if __name__ == "__main__":
    main()
