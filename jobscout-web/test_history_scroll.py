"""Offline history deletion and 100-row scrolling checks with synthetic data.

--baseline-ref HEAD measures the committed UI with the same fixtures/viewport.
No running Gateway, credentials, portals or model calls are used.
"""

import argparse
import json
import subprocess
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright
from test_ui import ROOT, STAGES, fixtures, page_payload


def measure(page):
    return page.evaluate("""() => {
      const wrap = document.querySelector('#trackerTableWrap');
      const bounds = wrap.getBoundingClientRect();
      const header = wrap.querySelector('thead').getBoundingClientRect();
      const visible = [...wrap.querySelectorAll('tbody tr')].filter(row => {
        const rect = row.getBoundingClientRect();
        return rect.top >= Math.max(bounds.top, header.bottom) && rect.bottom <= Math.min(bounds.bottom, innerHeight);
      });
      return { viewport: `${innerWidth}x${innerHeight}`, visible_rows: visible.length,
        table_height_px: Math.round(bounds.height), row_height_px: Math.round(wrap.querySelector('tbody tr').getBoundingClientRect().height) };
    }""")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-ref")
    args = parser.parse_args()
    output = ROOT.parent / "local_eval" / "jobscout_history_scroll"
    output.mkdir(parents=True, exist_ok=True)

    def read_asset(name):
        if args.baseline_ref:
            return subprocess.check_output(
                ["git", "show", f"{args.baseline_ref}:jobscout-web/{name}"],
                cwd=ROOT.parent,
            ).decode("utf-8")
        return (ROOT / name).read_text(encoding="utf-8")

    assets = {
        name: read_asset(name)
        for name in ("index.html", "app.js", "runtime-config.js", "style.css")
    }
    paginated = 'id="trackerPagination"' in assets["index.html"]
    for name in ("core.js", "tracker-state.js", "api-client.js"):
        if f'src="{name}"' in assets["index.html"]:
            assets[name] = read_asset(name)
    rows = [
        {**fixtures()[i % 6], "id": i + 1, "company": f"示例公司 {i + 1:03d}"}
        for i in range(100)
    ]
    title = '面试准备 <img src=x onerror="alert(1)">'
    history = [
        {"thread_id": "fixture-one", "status": "idle", "values": {"title": title}},
        {"thread_id": "fixture-two", "status": "idle", "values": {"title": "岗位匹配"}},
        {
            "thread_id": "fixture-busy",
            "status": "busy",
            "values": {"title": "正在研究"},
        },
    ]
    targets = [
        dict(
            id=1,
            company="示例公司 001",
            role="后端开发工程师",
            recruitment_type="校招",
            prep_thread_id="fixture-one",
            match_thread_id=None,
            application_ids=[1],
        )
    ]
    state = {
        "delete_status": 200,
        "delete_calls": 0,
        "patches": 0,
        "target_failure": False,
    }
    errors, unexpected, measurements = [], [], []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000},
            locale="zh-CN",
            service_workers="block",
        )
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))

        def route(request_route):
            request = request_route.request
            url = urlsplit(request.url)
            path = url.path

            def fulfill(data, status=200):
                request_route.fulfill(
                    status=status,
                    content_type="application/json",
                    body=json.dumps(data),
                    headers={
                        "Access-Control-Allow-Origin": "http://localhost:5500",
                        "Access-Control-Allow-Credentials": "true",
                        "Access-Control-Allow-Headers": "content-type,x-csrf-token",
                        "Access-Control-Allow-Methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
                    },
                )

            if url.hostname != "localhost" or url.port not in (5500, 8001):
                unexpected.append(request.url)
                request_route.abort()
            elif request.method == "OPTIONS":
                fulfill({})
            elif path in (
                "/",
                "/index.html",
                "/app.js",
                "/core.js",
                "/tracker-state.js",
                "/api-client.js",
                "/runtime-config.js",
                "/style.css",
            ):
                name = "index.html" if path == "/" else path[1:]
                request_route.fulfill(
                    body=assets[name],
                    content_type={
                        "index.html": "text/html",
                        "app.js": "text/javascript",
                        "core.js": "text/javascript",
                        "tracker-state.js": "text/javascript",
                        "api-client.js": "text/javascript",
                        "runtime-config.js": "text/javascript",
                        "style.css": "text/css",
                    }[name],
                )
            elif path == "/favicon.ico":
                request_route.fulfill(status=204)
            elif path == "/api/v1/auth/me":
                fulfill({"email": "demo@example.com"})
            elif path == "/api/threads/search":
                fulfill(history)
            elif path == "/api/jobscout/opportunities":
                fulfill(
                    {"detail": "离线测试：岗位列表暂时不可用"}
                    if state["target_failure"]
                    else targets,
                    503 if state["target_failure"] else 200,
                )
            elif path == "/api/jobscout/tracker/stages":
                fulfill(STAGES)
            elif path == "/api/jobscout/tracker/applications":
                fulfill(rows)
            elif path == "/api/jobscout/tracker/applications/page":
                fulfill(
                    {"detail": "分页读取失败"}
                    if state.get("page_failure")
                    else page_payload(rows, url.query),
                    503 if state.get("page_failure") else 200,
                )
            elif path == "/api/jobscout/tracker/notifications":
                fulfill([])
            elif path == "/api/integrations/lark/status":
                fulfill({"installed": False})
            elif path.startswith("/api/threads/") and path.endswith("/state"):
                fulfill(
                    {
                        "values": {
                            "messages": [
                                {"type": "human", "content": "示例面试准备请求"}
                            ]
                        }
                    }
                )
            elif (
                path == "/api/jobscout/threads/fixture-one"
                and request.method == "DELETE"
            ):
                state["delete_calls"] += 1
                if state["delete_status"] != 200:
                    fulfill(
                        {"detail": "离线测试：删除暂时不可用"}, state["delete_status"]
                    )
                else:
                    history[:] = [
                        thread
                        for thread in history
                        if thread["thread_id"] != "fixture-one"
                    ]
                    targets[0]["prep_thread_id"] = None
                    fulfill({"deleted": True})
            elif request.method == "PATCH":
                state["patches"] += 1
                fulfill({})
            else:
                unexpected.append(path)
                request_route.abort()

        context.route("**/*", route)
        page.goto("http://localhost:5500/", wait_until="networkidle")
        expect(page.locator(".tracker-row")).to_have_count(50 if paginated else 100)
        for width, height in [(1440, 1000), (1366, 768)]:
            page.set_viewport_size({"width": width, "height": height})
            measurements.append(measure(page))
        label = "before" if args.baseline_ref else "after"
        (output / f"{label}-layout.json").write_text(
            json.dumps(measurements, indent=2), encoding="utf-8"
        )
        if args.baseline_ref:
            print(
                json.dumps(
                    {
                        "baseline": measurements,
                        "unexpected_requests": unexpected,
                        "errors": errors,
                    }
                )
            )
            assert not unexpected and not errors
            browser.close()
            return

        expect(page.locator("#trackerPageLabel")).to_contain_text("第 1 页")
        first_ids = page.locator('.tracker-row input[aria-label="公司"]').evaluate_all(
            "nodes => nodes.map(node => node.value)"
        )
        page.locator("#trackerNextPage").click()
        expect(page.locator("#trackerPageLabel")).to_contain_text("第 2 页")
        expect(page.locator("#trackerNextPage")).to_be_disabled()
        second_ids = page.locator('.tracker-row input[aria-label="公司"]').evaluate_all(
            "nodes => nodes.map(node => node.value)"
        )
        assert len(first_ids) == len(second_ids) == 50 and set(first_ids).isdisjoint(
            second_ids
        )
        expect(page.locator("#trackerTotalCount")).to_have_text("100")
        page.locator("#trackerPreviousPage").click()
        expect(page.locator("#trackerPageLabel")).to_contain_text("第 1 页")
        state["page_failure"] = True
        page.locator("#trackerNextPage").click()
        expect(page.locator("#trackerError")).to_contain_text("分页读取失败")
        expect(page.locator("#trackerPageLabel")).to_contain_text("第 1 页")
        state["page_failure"] = False

        page.set_viewport_size({"width": 1440, "height": 1000})
        page.screenshot(path=str(output / "after-desktop.png"), animations="disabled")
        wrap = page.locator("#trackerTableWrap")
        heading_top = page.locator(".tracker-table th").first.bounding_box()["y"]
        wrap.hover(position={"x": 400, "y": 200})
        page.mouse.wheel(0, 700)
        page.wait_for_function(
            "document.querySelector('#trackerTableWrap').scrollTop > 300"
        )
        assert (
            abs(
                page.locator(".tracker-table th").first.bounding_box()["y"]
                - heading_top
            )
            < 2
        )
        wrap.evaluate("node => node.scrollTop = 0")
        page.locator(".tracker-stage-select").first.hover()
        page.mouse.wheel(0, 500)
        page.wait_for_function(
            "document.querySelector('#trackerTableWrap').scrollTop > 100"
        )
        assert state["patches"] == 0, (
            "Wheel scrolling must not modify application stages"
        )
        wrap.evaluate("node => node.scrollTop = node.scrollHeight")
        expect(page.locator(".tracker-row").last).to_be_in_viewport()
        wrap.evaluate("node => node.scrollTop = 0")
        wrap.focus()
        page.keyboard.press("PageDown")
        page.wait_for_function(
            "document.querySelector('#trackerTableWrap').scrollTop > 100"
        )
        wrap.evaluate("node => node.scrollTop = 0")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        expect(page.locator("#trackerOverview")).to_be_hidden()
        page.locator("#trackerOverviewToggle").click()
        expect(page.locator("#trackerOverview")).to_be_visible()
        expect(page.locator("#trackerTotalCount")).to_have_text("100")
        assert measure(page)["visible_rows"] < measurements[0]["visible_rows"]
        page.locator("#trackerOverviewToggle").click()
        page.reload(wait_until="networkidle")
        expect(page.locator("#trackerOverview")).to_be_hidden()
        page.locator("#trackerDensityBtn").click()
        wrap.hover(position={"x": 400, "y": 100})
        page.mouse.wheel(0, 700)
        page.wait_for_function(
            "document.querySelector('#trackerTableWrap').scrollTop > 100"
        )
        page.locator("#trackerDensityBtn").click()

        delete = page.locator(".thread-delete-btn").first
        expect(page.locator(".thread-delete-btn").last).to_be_disabled()
        delete.click()
        expect(page.locator("#threadDeleteDialog")).to_be_visible()
        expect(page.locator("#threadDeleteName")).to_have_text(title)
        expect(page.locator("#threadDeleteName img")).to_have_count(0)
        expect(page.locator("#threadDeleteCancel")).to_be_focused()
        page.screenshot(path=str(output / "delete-dialog.png"), animations="disabled")
        page.locator("#threadDeleteCancel").click()
        assert state["delete_calls"] == 0
        page.locator(".thread-item").first.click()
        expect(page.locator("#chatMessages")).to_contain_text("示例面试准备请求")
        delete.click()
        for status, message in [
            (409, "任务运行"),
            (500, "暂时不可用"),
            (404, "重启 Gateway"),
        ]:
            state["delete_status"] = status
            page.locator("#threadDeleteConfirm").click()
            expect(page.locator("#threadDeleteError")).to_contain_text(message)
            expect(page.locator(".thread-item")).to_have_count(3)
            expect(page.locator("#threadDeleteConfirm")).to_be_enabled()
        state["delete_status"] = 200
        state["target_failure"] = True
        page.locator("#threadDeleteConfirm").click()
        expect(page.locator("#threadDeleteDialog")).to_be_hidden()
        expect(page.locator(".thread-item")).to_have_count(2)
        expect(page.locator("#threadActionStatus")).to_contain_text(
            "对话已删除，关联岗位暂未刷新"
        )
        assert page.evaluate("activeThreadId") is None
        assert page.evaluate("opportunities[0].prep_thread_id") is None
        assert page.evaluate("opportunities[0].application_ids") == [1]
        assert page.evaluate("trackerRows.length") == 50
        assert state["delete_calls"] == 4
        state["target_failure"] = False

        page.locator("#sidebarTrackerBtn").click()
        page.set_viewport_size({"width": 390, "height": 844})
        expect(page.locator(".tracker-row")).to_have_count(50)
        page.screenshot(path=str(output / "after-mobile.png"), animations="disabled")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert wrap.evaluate("node => getComputedStyle(node).overflowY") == "visible"
        page.locator(".tracker-row").last.scroll_into_view_if_needed()
        expect(page.locator(".tracker-row").last).to_be_in_viewport()
        page.locator("#sidebarToggle").click()
        page.locator(".thread-delete-btn").first.click()
        expect(page.locator("#threadDeleteDialog")).to_be_visible()
        expect(page.locator("#threadDeleteConfirm")).to_be_in_viewport()
        page.keyboard.press("Escape")
        expect(page.locator("#threadDeleteDialog")).to_be_hidden()
        assert state["delete_calls"] == 4, "Mobile cancellation must not delete"
        assert state["patches"] == 0 and not unexpected and not errors, (
            unexpected,
            errors,
        )
        browser.close()
        print(
            json.dumps(
                {
                    "result": "passed",
                    "layout": measurements,
                    "rows": 100,
                    "delete_requests": state["delete_calls"],
                    "unexpected_requests": 0,
                    "page_errors": 0,
                }
            )
        )


if __name__ == "__main__":
    main()
