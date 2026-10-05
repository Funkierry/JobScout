"""Offline browser checks; every request is fulfilled locally or rejected.

Run with backend/.venv/Scripts/python.exe -B jobscout-web/test_ui.py.
Screenshots contain synthetic data only and stay under ignored local_eval/.
"""

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent
STAMP = "2026-10-01T08:00:00+00:00"
STAGES = ["未知", "已投递", "简历筛选", "笔试", "一面", "二面", "Offer", "未通过"]


def fixtures():
    return [
        dict(
            id=i,
            company=company,
            role=role,
            url=f"https://careers.example.com/{i}",
            status=status,
            stage=status,
            raw_status=status,
            confidence=0.95 if status != "未知" else 0.0,
            evidence=f"{role} · 当前状态：{status}",
            checked_at=STAMP,
            changed_at=STAMP,
            applied_at="2026-09-28",
            applied_at_evidence="投递时间：2026-09-28",
            check_result="成功" if status != "未知" else "需登录",
            terminal=status in ["Offer", "未通过"],
            changed=False,
            stage_manual=False,
            previous_status=None,
            source_summary=None,
        )
        for i, (company, role, status) in enumerate(
            [
                ("远山科技", "后端开发工程师", "二面"),
                ("青禾数据", "数据开发工程师", "笔试"),
                ("北辰实验室", "Java 开发工程师", "简历筛选"),
                ("原野网络", "平台研发工程师", "已投递"),
                ("晴空软件", "软件开发工程师", "Offer"),
                ("星海数科", "大数据开发工程师", "未知"),
            ],
            1,
        )
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-only", action="store_true")
    parser.add_argument("--label", default="after")
    args = parser.parse_args()
    if not args.label.isalnum():
        parser.error("label must be alphanumeric")
    output = ROOT.parent / "local_eval" / "ui"
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            service_workers="block",
        )
        page = context.new_page()
        errors, unexpected, mutations = [], [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        rows = fixtures()
        state = {"read": False, "failure": False, "authenticated": True}
        settings = dict(
            enabled=False,
            interval_minutes=360,
            daily_limit=30,
            timezone="Asia/Shanghai",
            allow_model_fallback=False,
            server_enabled=True,
            used_today=0,
            next_run_at=None,
        )

        def route(request_route):
            request = request_route.request
            url = urlsplit(request.url)
            path = url.path
            headers = {
                "Access-Control-Allow-Origin": "http://localhost:5500",
                "Access-Control-Allow-Credentials": "true",
                "Access-Control-Allow-Headers": "content-type,x-csrf-token",
                "Access-Control-Allow-Methods": "GET,POST,PUT,OPTIONS",
            }

            def fulfill(data, status=200):
                request_route.fulfill(
                    status=status,
                    content_type="application/json",
                    body=json.dumps(data),
                    headers=headers,
                )

            if url.hostname != "localhost":
                unexpected.append(request.url)
                request_route.abort()
            elif request.method == "OPTIONS":
                fulfill({})
            elif path in [
                "/",
                "/index.html",
                "/app.js",
                "/runtime-config.js",
                "/style.css",
            ]:
                name = "index.html" if path == "/" else path[1:]
                request_route.fulfill(
                    path=str(ROOT / name),
                    content_type={
                        "index.html": "text/html",
                        "app.js": "text/javascript",
                        "runtime-config.js": "text/javascript",
                        "style.css": "text/css",
                    }[name],
                )
            elif path == "/favicon.ico":
                request_route.fulfill(status=204)
            elif path == "/api/v1/auth/me":
                fulfill(
                    {"email": "demo@example.com"} if state["authenticated"] else {},
                    200 if state["authenticated"] else 401,
                )
            elif path == "/api/v1/auth/setup-status":
                fulfill({"needs_setup": False, "registration_enabled": True})
            elif path in [
                "/api/threads/search",
                "/api/jobscout/opportunities",
                "/api/jobscout/tracker/mail/events",
            ]:
                fulfill([])
            elif path == "/api/integrations/lark/status":
                fulfill({"installed": False})
            elif path == "/api/jobscout/tracker/stages":
                fulfill(STAGES)
            elif path == "/api/jobscout/tracker/applications":
                fulfill(
                    {"detail": "离线测试：暂时无法读取记录"}
                    if state["failure"]
                    else rows,
                    503 if state["failure"] else 200,
                )
            elif path == "/api/jobscout/tracker/import":
                assert request.method == "POST"
                assert "text/csv" in request.post_data
                state["imported"] = True
                fulfill({"inserted": 1, "updated": 0, "total": 1})
            elif path == "/api/jobscout/tracker/notifications":
                fulfill(
                    [
                        dict(
                            id=1,
                            company=rows[0]["company"] if rows else "远山科技",
                            role="后端开发工程师",
                            old_status="一面",
                            new_status="二面",
                            evidence="后端开发工程师 · 当前状态：二面",
                            source="portal",
                            created_at=STAMP,
                            read=state["read"],
                        )
                    ]
                )
            elif path == "/api/jobscout/tracker/notifications/1/read":
                state["read"] = True
                fulfill({"read": True})
            elif path == "/api/jobscout/tracker/schedule":
                if request.method == "PUT":
                    mutations.append(request.post_data_json)
                    settings.update(request.post_data_json)
                fulfill(settings)
            elif path == "/api/jobscout/tracker/mail/config":
                fulfill(
                    dict(
                        enabled=False,
                        provider="gmail",
                        sender_domain_count=0,
                        max_messages=30,
                    )
                )
            else:
                unexpected.append(path)
                request_route.abort()

        context.route("**/*", route)
        page.goto("http://localhost:5500/", wait_until="networkidle")
        page.locator("#sidebarTrackerBtn").click()
        expect(page.locator(".tracker-row")).to_have_count(6)
        page.screenshot(
            path=str(output / f"{args.label}-desktop.png"),
            full_page=True,
            animations="disabled",
        )
        if not args.capture_only:
            expect(page.locator("#trackerTotalCount")).to_have_text("6")
            expect(page.locator("#trackerActiveCount")).to_have_text("4")
            expect(page.locator("#trackerReviewCount")).to_have_text("1")
            with page.expect_file_chooser() as chooser:
                page.locator("#trackerImportBtn").click()
            chooser.value.set_files(
                {
                    "name": "fixture.csv",
                    "mimeType": "text/csv",
                    "buffer": b"company,role,url,applied_at,notes\nExample,Backend,https://example.test/jobs,,fixture\n",
                }
            )
            expect(page.locator("#trackerProgress")).to_contain_text("导入完成")
            assert state["imported"]
            expect(page.locator("#trackerImportBtn")).to_be_enabled()
            page.locator(".tracker-filter-tag").filter(has_text="笔试").click()
            expect(page.locator(".tracker-row")).to_have_count(1)
            page.locator(".tracker-filter-tag").filter(has_text="全部").click()
            page.locator(".tracker-status-detail").first.click()
            expect(page.locator("#trackerResultDialog")).to_be_visible()
            expect(page.locator("#trackerResultEvidence")).to_contain_text(
                "后端开发工程师"
            )
            page.keyboard.press("Escape")
            page.locator("#trackerScheduleBtn").click()
            expect(page.locator("#trackerScheduleSave")).to_be_enabled()
            expect(page.locator("#trackerScheduleModel")).not_to_be_checked()
            page.locator("#trackerScheduleInterval").fill("120")
            page.locator("#trackerScheduleSave").click()
            expect(page.locator("#trackerScheduleStatus")).to_have_text("设置已保存。")
            assert (
                mutations[-1]["interval_minutes"] == 120
                and not mutations[-1]["allow_model_fallback"]
            )
            page.keyboard.press("Escape")
            page.locator("#trackerNotificationsBtn").click()
            page.get_by_role("button", name="标为已读", exact=True).click()
            expect(page.locator("#trackerUnreadCount")).to_be_hidden()
            page.keyboard.press("Escape")
            page.locator("#trackerMailBtn").click()
            expect(page.locator("#trackerMailSync")).to_be_disabled()
            page.keyboard.press("Escape")
            page.locator("#newChatBtn").click()
            expect(page.locator("#workspaceTitle")).to_have_text("面试准备")
            expect(page.locator("#composerInput")).to_be_visible()
            expect(page.locator("#welcomeState")).not_to_contain_text("8+4")
            page.screenshot(
                path=str(output / "after-prep.png"),
                full_page=True,
                animations="disabled",
            )
            page.locator("#sidebarMatchBtn").click()
            expect(page.locator("#baseUrlInput")).to_be_visible()
            page.locator("#sidebarTrackerBtn").click()
            page.keyboard.press("Control+k")
            expect(page.locator("#workspaceTitle")).to_have_text("面试准备")
            page.locator("#sidebarTrackerBtn").click()
        page.set_viewport_size({"width": 390, "height": 844})
        page.locator("#trackerPanel").evaluate("node => node.scrollTop = 0")
        page.screenshot(
            path=str(output / f"{args.label}-mobile.png"),
            full_page=True,
            animations="disabled",
        )
        if not args.capture_only:
            assert page.evaluate(
                "document.documentElement.scrollWidth <= window.innerWidth"
            )
            page.locator("#sidebarToggle").click()
            expect(page.locator("#sidebarTrackerBtn")).to_be_visible()
            page.locator("#sidebarCloseBtn").click()
            page.locator("#trackerScheduleBtn").click()
            expect(page.locator("#trackerScheduleForm")).to_be_visible()
            page.keyboard.press("Escape")
            page.locator(".tracker-row").first.scroll_into_view_if_needed()
            page.screenshot(
                path=str(output / "after-mobile-records.png"),
                full_page=True,
                animations="disabled",
            )
            rows.clear()
            page.evaluate("loadTrackerApplications()")
            expect(page.locator("#trackerEmpty")).to_be_visible()
            expect(page.locator("#trackerTotalCount")).to_have_text("0")
            state["failure"] = True
            page.evaluate("loadTrackerApplications()")
            expect(page.locator("#trackerError")).to_contain_text("暂时无法读取记录")
            page.screenshot(
                path=str(output / "after-empty-error.png"),
                full_page=True,
                animations="disabled",
            )
        state["authenticated"] = False
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.reload(wait_until="networkidle")
        expect(page.locator("#authView")).to_be_visible()
        page.screenshot(
            path=str(output / f"{args.label}-login.png"),
            full_page=True,
            animations="disabled",
        )
        page.set_viewport_size({"width": 390, "height": 844})
        expect(page.locator("#authSubmit")).to_be_visible()
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        )
        page.screenshot(
            path=str(output / f"{args.label}-login-mobile.png"),
            full_page=True,
            animations="disabled",
        )
        assert not unexpected, unexpected
        assert not errors, errors
        browser.close()
    print(
        "PASS: fixture browser checks; all requests intercepted; no real account, website, mailbox or model used"
    )


if __name__ == "__main__":
    main()
