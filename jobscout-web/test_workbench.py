"""Synthetic browser workflow using the production notes repository; no network."""

import json
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright
from test_ui import ROOT, STAGES, comparison_fixture, fixtures, page_payload, settle

from app.application_tracker.store import ApplicationTrackerStore
from app.application_tracker.workbench import (
    FeedbackInput,
    PlanInput,
    WorkbenchStore,
    WriteConflict,
)


def main():
    with tempfile.TemporaryDirectory() as temp, sync_playwright() as playwright:
        store = WorkbenchStore(ApplicationTrackerStore(Path(temp) / "tracker.db"))
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000}, service_workers="block"
        )
        page = context.new_page()
        state = {
            "fail_save": False,
            "fail_feedback": False,
            "hold": False,
            "authenticated": True,
        }
        unexpected, errors, feedback_requests = [], [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        report = comparison_fixture()

        def route(r):
            request = r.request
            url = urlsplit(request.url)
            path = url.path
            headers = {
                "Access-Control-Allow-Origin": "http://localhost:5500",
                "Access-Control-Allow-Credentials": "true",
                "Access-Control-Allow-Headers": "content-type,x-csrf-token",
                "Access-Control-Allow-Methods": "GET,POST,PUT,OPTIONS",
            }

            def fulfill(data, status=200):
                r.fulfill(
                    status=status,
                    content_type="application/json",
                    body=json.dumps(data),
                    headers=headers,
                )

            if url.hostname != "localhost" or url.port not in (5500, 8001):
                unexpected.append(request.url)
                r.abort()
            elif request.method == "OPTIONS":
                r.fulfill(status=204, headers=headers)
            elif path in (
                "/",
                "/index.html",
                "/app.js",
                "/core.js",
                "/tracker-state.js",
                "/workbench.js",
                "/api-client.js",
                "/runtime-config.js",
                "/style.css",
            ):
                name = "index.html" if path == "/" else path[1:]
                mime = (
                    "text/html"
                    if name.endswith("html")
                    else "text/css"
                    if name.endswith("css")
                    else "text/javascript"
                )
                r.fulfill(path=str(ROOT / name), content_type=mime)
            elif path == "/api/v1/auth/me":
                fulfill(
                    {"email": "synthetic@example.com"},
                    200 if state["authenticated"] else 401,
                )
            elif path == "/api/v1/auth/setup-status":
                fulfill({"needs_setup": False, "registration_enabled": True})
            elif path == "/api/v1/auth/logout":
                state["authenticated"] = False
                fulfill({})
            elif path == "/api/threads/search":
                fulfill(
                    [
                        {
                            "thread_id": "synthetic-report",
                            "values": {"title": "合成匹配报告"},
                            "status": "idle",
                        }
                    ]
                )
            elif path in (
                "/api/jobscout/opportunities",
                "/api/jobscout/tracker/notifications",
                "/api/jobscout/match-candidates/synthetic-report",
            ):
                fulfill([])
            elif path == "/api/integrations/lark/status":
                fulfill({"installed": False})
            elif path == "/api/jobscout/tracker/stages":
                fulfill(STAGES)
            elif path == "/api/jobscout/tracker/applications/page":
                fulfill(page_payload(fixtures(), url.query))
            elif path == "/api/jobscout/tracker/applications/6":
                fulfill(fixtures()[5])
            elif path == "/api/jobscout/workbench":
                fulfill(
                    {
                        "items": [
                            {
                                "kind": "login",
                                "title": "恢复招聘账号登录",
                                "hint": "需要登录后核对来源",
                                "application_id": 6,
                                "company": "合成公司",
                                "role": "产品经理",
                            }
                        ],
                        "total": 1,
                        "has_more": False,
                    }
                )
            elif path == "/api/threads/synthetic-report/state":
                fulfill(
                    {
                        "values": {
                            "messages": [
                                {
                                    "type": "human",
                                    "content": "合成匹配请求",
                                    "additional_kwargs": {"jobscout_mode": "match"},
                                },
                                {
                                    "type": "ai",
                                    "content": report,
                                    "additional_kwargs": {
                                        "jobscout_evidence": {
                                            "version": 2,
                                            "mode": "base_match",
                                            "match_contract_version": 2,
                                            "run_id": "synthetic",
                                            "accepted_count": 1,
                                            "removed_count": 0,
                                        }
                                    },
                                },
                            ]
                        }
                    }
                )
            elif path == "/api/jobscout/preparation/synthetic-report":
                key = parse_qs(url.query)["report_key"][0]
                state["key"] = key
                if request.method == "PUT":
                    if state["hold"]:
                        state["pending"] = r
                    elif state["fail_save"]:
                        fulfill({"detail": "合成保存故障"}, 503)
                    else:
                        try:
                            fulfill(
                                store.save_plan(
                                    "synthetic",
                                    "synthetic-report",
                                    key,
                                    PlanInput(**request.post_data_json),
                                )
                            )
                        except WriteConflict as exc:
                            fulfill({"detail": str(exc)}, 409)
                else:
                    fulfill(store.plan("synthetic", "synthetic-report", key))
            elif path == "/api/jobscout/feedback":
                if request.method == "POST":
                    body = FeedbackInput(**request.post_data_json)
                    feedback_requests.append(body.request_id)
                    saved = store.add_feedback("synthetic", body)
                    # Simulate commit succeeded but response was lost.
                    fulfill(
                        {"detail": "合成响应丢失"} if state["fail_feedback"] else saved,
                        503 if state["fail_feedback"] else 200,
                    )
                else:
                    fulfill(store.feedback("synthetic"))
            elif path == "/favicon.ico":
                r.fulfill(status=204)
            else:
                unexpected.append(path)
                r.abort()

        context.route("**/*", route)
        page.goto("http://localhost:5500/", wait_until="networkidle")
        page.locator("#workbenchPending summary").click()
        expect(page.locator("#workbenchCount")).to_have_text(" · 1 项")
        page.get_by_role("button", name="查看并处理", exact=True).click()
        expect(page.locator("#trackerRecovery")).to_contain_text("登录")
        page.get_by_role("button", name="记录识别纠错", exact=True).click()
        dialog = page.get_by_role("dialog", name="反馈与试用记录", exact=True)
        expect(dialog.get_by_label("记录类型", exact=True)).to_have_value("correction")
        dialog.get_by_role("button", name="关闭记录").click()
        page.keyboard.press("Escape")
        page.locator(".thread-item").first.click()
        plan = page.get_by_role("region", name="准备进度与个人笔记", exact=True)
        expect(plan).to_contain_text("尚未保存")
        plan.get_by_label("已整理可讲述的真实经历", exact=True).check()
        plan.get_by_label("准备使用的个人经历（用户笔记）", exact=True).fill(
            "<img src=x onerror=alert(1)> 合成项目：使用 Python 分析数据"
        )
        plan.get_by_label("缺失材料 / 待确认问题（用户笔记）", exact=True).fill(
            "待补个人职责与结果"
        )
        state["fail_save"] = True
        plan.get_by_role("button", name="保存准备进度", exact=True).click()
        expect(plan).to_contain_text("内容已保留")
        state["fail_save"] = False
        plan.get_by_role("button", name="保存准备进度", exact=True).click()
        expect(plan).to_contain_text("已保存 · 1 / 3")
        page.reload(wait_until="networkidle")
        page.locator(".thread-item").first.click()
        expect(plan).to_contain_text("已恢复保存进度 · 1 / 3")
        expect(plan.get_by_label("已整理可讲述的真实经历", exact=True)).to_be_checked()
        expect(
            plan.get_by_label("缺失材料 / 待确认问题（用户笔记）", exact=True)
        ).to_have_value("待补个人职责与结果")
        store.save_plan(
            "synthetic",
            "synthetic-report",
            state["key"],
            PlanInput(revision=1, materials="其他标签页保存的笔记"),
        )
        plan.get_by_label("缺失材料 / 待确认问题（用户笔记）", exact=True).fill(
            "本页未保存的补充"
        )
        plan.get_by_role("button", name="保存准备进度", exact=True).click()
        expect(plan).to_contain_text("其他页面更新")
        expect(
            plan.get_by_label("缺失材料 / 待确认问题（用户笔记）", exact=True)
        ).to_have_value("本页未保存的补充")
        plan.get_by_role(
            "button", name="重新读取（替换未保存内容）", exact=True
        ).click()
        expect(
            plan.get_by_label("缺失材料 / 待确认问题（用户笔记）", exact=True)
        ).to_have_value("其他标签页保存的笔记")
        plan.get_by_role("button", name="记录报告反馈 / 推荐决策", exact=True).click()
        dialog.get_by_label("原因 / 阻塞点（必填）", exact=True).fill(
            "合成反馈：缺少城市信息，暂不接受推荐"
        )
        dialog.get_by_label("处理结果", exact=True).select_option("rejected")
        dialog.get_by_label("问题类型", exact=True).select_option("matching")
        dialog.get_by_label("下次复验应检查什么", exact=True).fill("确认岗位地点原文")
        state["fail_feedback"] = True
        dialog.get_by_role("button", name="保存记录", exact=True).click()
        expect(dialog).to_contain_text("内容已保留")
        state["fail_feedback"] = False
        dialog.get_by_role("button", name="保存记录", exact=True).click()
        expect(dialog).to_contain_text("已保存。原始投递状态")
        assert feedback_requests[0] == feedback_requests[1]
        assert len(store.feedback("synthetic")) == 1
        dialog.locator("summary").click()
        expect(dialog).to_contain_text("确认岗位地点原文")
        with page.expect_download() as download:
            dialog.get_by_role("button", name="导出已读取记录 JSON").click()
        assert (
            len(json.loads(Path(download.value.path()).read_text(encoding="utf-8")))
            == 1
        )
        dialog.get_by_role("button", name="关闭记录").click()
        output = ROOT.parent / "local_eval" / "ui"
        output.mkdir(parents=True, exist_ok=True)
        settle(page)
        plan.screenshot(
            path=str(output / "phase3-preparation.png"), animations="disabled"
        )
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        plan.screenshot(
            path=str(output / "phase3-preparation-mobile.png"), animations="disabled"
        )
        page.set_viewport_size({"width": 1440, "height": 1000})
        state["hold"] = True
        plan.get_by_role("button", name="保存准备进度", exact=True).click()
        page.wait_for_timeout(100)
        assert "pending" in state
        page.get_by_role("button", name="退出登录", exact=True).click()
        state["pending"].fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "revision": 2,
                    "done": [],
                    "experience": "其他会话不应看到",
                    "materials": "",
                }
            ),
            headers={
                "Access-Control-Allow-Origin": "http://localhost:5500",
                "Access-Control-Allow-Credentials": "true",
            },
        )
        expect(page.locator("#authView")).to_be_visible()
        expect(page.locator(".preparation-actions")).to_have_count(0)
        assert not unexpected, unexpected
        assert not errors, errors
        browser.close()
    print(
        "PASS: pending actions, saved preparation, feedback retries/export, mobile and logout fencing; synthetic data only"
    )


if __name__ == "__main__":
    main()
