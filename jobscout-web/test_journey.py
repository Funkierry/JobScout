"""Offline UI -> production FastAPI routes -> temporary SQLite journey.

Run via backend/scripts/jobscout_offline_tests.py --test ../jobscout-web/test_journey.py.
Authentication identity and report history are fixtures; no model/portal/mail calls.
"""

import hashlib
import json
import platform
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright
from test_ui import ROOT, comparison_fixture

sys.path.insert(0, str(ROOT.parent / "backend" / "tests"))
from _router_auth_helpers import make_authed_test_app  # noqa: E402

from app.application_tracker.models import ApplicationInput  # noqa: E402
from app.application_tracker.store import ApplicationTrackerStore  # noqa: E402
from app.application_tracker.workbench import FeedbackInput, WorkbenchStore  # noqa: E402
from app.gateway.routers import jobscout  # noqa: E402


def test_feedback_preparation_journey(tmp_path, monkeypatch):
    started = time.perf_counter()
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    dirty = bool(
        subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=ROOT,
            text=True,
        ).strip()
    )
    tracker = ApplicationTrackerStore(tmp_path / "tracker.db")
    row = tracker.add_application(
        "owner",
        ApplicationInput(
            company="合成公司",
            role="AI 产品经理",
            url="https://example.com/application",
        ),
    )
    with tracker._connect() as connection:
        connection.execute(
            "UPDATE applications SET status='一面',stage='一面',confidence=0.4,check_result='成功',checked_at='2026-10-22T00:00:00+00:00' WHERE id=?",
            (row.id,),
        )
    before = tracker.list_applications("owner")
    store = WorkbenchStore(tracker)
    for i in range(51):
        store.add_feedback(
            "owner",
            FeedbackInput(
                request_id=f"fixture-{i:03}", category="trial", reason=f"合成历史 {i}"
            ),
        )
    identity = {"owner": "owner"}
    monkeypatch.setattr(jobscout, "_tracker_store", lambda: tracker)
    monkeypatch.setattr(jobscout, "get_effective_user_id", lambda: identity["owner"])
    monkeypatch.setattr(
        jobscout,
        "get_thread_store",
        lambda _: SimpleNamespace(
            get=AsyncMock(
                side_effect=lambda thread_id, user_id: (
                    {"thread_id": thread_id}
                    if user_id == "owner" and thread_id == "synthetic-report"
                    else None
                )
            )
        ),
    )
    app = make_authed_test_app()
    app.include_router(jobscout.router)
    client = TestClient(app)
    report = comparison_fixture()
    calls, unexpected, errors = [], [], []
    failure = {"older_page": True}
    output = ROOT.parent / "local_eval" / "ui"
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        browser_version = browser.version
        context = browser.new_context(
            viewport={"width": 1360, "height": 960}, service_workers="block"
        )
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))

        def route(r):
            req, url = r.request, urlsplit(r.request.url)
            path = url.path
            headers = {
                "Access-Control-Allow-Origin": "http://localhost:5500",
                "Access-Control-Allow-Credentials": "true",
                "Access-Control-Allow-Headers": "content-type,x-csrf-token",
                "Access-Control-Allow-Methods": "GET,POST,PUT,OPTIONS",
            }

            def reply(data, status=200):
                r.fulfill(
                    status=status,
                    body=json.dumps(data),
                    content_type="application/json",
                    headers=headers,
                )

            if url.hostname != "localhost" or url.port not in (5500, 8001):
                unexpected.append(req.url)
                r.abort()
            elif req.method == "OPTIONS":
                r.fulfill(status=204, headers=headers)
            elif path in (
                "/",
                "/index.html",
                "/app.js",
                "/core.js",
                "/api-client.js",
                "/tracker-state.js",
                "/workbench.js",
                "/runtime-config.js",
                "/style.css",
            ):
                name = "index.html" if path == "/" else path[1:]
                r.fulfill(
                    path=str(ROOT / name),
                    content_type="text/html"
                    if name.endswith("html")
                    else "text/css"
                    if name.endswith("css")
                    else "text/javascript",
                )
            elif path.startswith("/api/jobscout/"):
                calls.append((req.method, path))
                if (
                    path == "/api/jobscout/feedback"
                    and "before=" in url.query
                    and failure["older_page"]
                ):
                    reply({"detail": "合成历史读取故障"}, 503)
                else:
                    response = client.request(
                        req.method,
                        path + ("?" + url.query if url.query else ""),
                        content=req.post_data_buffer,
                        headers={"Content-Type": "application/json"},
                    )
                    reply(response.json(), response.status_code)
            elif path == "/api/v1/auth/me":
                reply({"email": "synthetic@example.com"})
            elif path == "/api/threads/search":
                reply(
                    [
                        {
                            "thread_id": "synthetic-report",
                            "values": {"title": "合成匹配报告"},
                            "status": "idle",
                        }
                    ]
                )
            elif path == "/api/threads/synthetic-report/state":
                reply(
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
            elif path == "/api/integrations/lark/status":
                reply({"installed": False})
            elif path == "/favicon.ico":
                r.fulfill(status=204)
            else:
                unexpected.append(path)
                r.abort()

        context.route("**/*", route)
        page.goto("http://localhost:5500/", wait_until="networkidle")
        page.locator("#workbenchPending summary").click()
        expect(page.locator("#workbenchItems")).to_contain_text("补充或核对状态依据")
        page.get_by_role("button", name="查看并处理", exact=True).click()
        page.get_by_role("button", name="记录识别纠错", exact=True).click()
        dialog = page.get_by_role("dialog", name="反馈与试用记录", exact=True)
        dialog.get_by_label("原因 / 阻塞点（必填）", exact=True).fill(
            "合成复现：低置信度面试需要核对"
        )
        dialog.get_by_label("下次复验应检查什么", exact=True).fill("应显示核对依据入口")
        dialog.get_by_label("问题类型", exact=True).select_option("status")
        dialog.get_by_role("button", name="保存记录", exact=True).click()
        expect(dialog).to_contain_text("已保存。原始投递状态")
        root_id = store.feedback("owner")[0]["id"]
        dialog.locator("summary").click()
        expect(dialog).to_contain_text("问题类型：状态表达")
        dialog.get_by_role("button", name=f"记录复验 #{root_id}", exact=True).click()
        expect(dialog).to_contain_text(f"正在复验记录 #{root_id}")
        dialog.get_by_label("原因 / 阻塞点（必填）", exact=True).fill(
            "按照原记录复验条件逐项检查"
        )
        dialog.get_by_label("复验结论", exact=True).select_option("passed")
        dialog.get_by_role("button", name="保存记录", exact=True).click()
        assert not dialog.get_by_label("更正内容 / 实际结果", exact=True).evaluate(
            "el => el.checkValidity()"
        )
        assert store.feedback("owner")[0]["id"] == root_id
        dialog.get_by_label("更正内容 / 实际结果", exact=True).fill(
            "合成检查：入口已提示核对依据"
        )
        dialog.get_by_role("button", name="保存记录", exact=True).click()
        expect(dialog).to_contain_text("已保存。原始投递状态")
        dialog.locator("summary").click()
        expect(dialog).to_contain_text(f"复验原记录 #{root_id} · 复验通过（用户确认）")
        dialog.get_by_role("button", name="读取更早记录", exact=True).click()
        expect(dialog).to_contain_text("合成历史读取故障")
        expect(dialog).to_contain_text("已读取 50 条")
        failure["older_page"] = False
        dialog.get_by_role("button", name="重试读取记录", exact=True).click()
        expect(dialog).to_contain_text("已读取 53 条")
        with page.expect_download() as download:
            dialog.get_by_role("button", name="导出已读取记录 JSON", exact=True).click()
        records = json.loads(Path(download.value.path()).read_text(encoding="utf-8"))
        assert len(records) == 53 and len({r["id"] for r in records}) == 53
        assert (
            records[0]["followup_of"] == root_id
            and records[0]["verification"] == "passed"
        )
        dialog.locator("article").first.screenshot(
            path=str(output / "phase4-feedback.png")
        )
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert dialog.evaluate("el => el.scrollWidth <= el.clientWidth")
        dialog.locator("article").first.screenshot(
            path=str(output / "phase4-feedback-mobile.png")
        )
        dialog.get_by_role("button", name="关闭记录", exact=True).click()
        page.keyboard.press("Escape")
        page.set_viewport_size({"width": 1360, "height": 960})
        page.locator(".thread-item").first.click()
        plan = page.get_by_role("region", name="准备进度与个人笔记", exact=True)
        expect(plan).to_contain_text("尚未保存")
        plan.get_by_label("已整理可讲述的真实经历", exact=True).check()
        plan.get_by_label("准备使用的个人经历（用户笔记）", exact=True).fill(
            "合成项目笔记"
        )
        plan.get_by_role("button", name="保存准备进度", exact=True).click()
        expect(plan).to_contain_text("已保存 · 1 / 3")
        # Recreate repository to exercise file persistence, then re-open report.
        monkeypatch.setattr(
            jobscout, "_tracker_store", lambda: ApplicationTrackerStore(tracker.path)
        )
        page.reload(wait_until="networkidle")
        page.locator(".thread-item").first.click()
        expect(plan).to_contain_text("已恢复保存进度 · 1 / 3")
        expect(
            plan.get_by_label("准备使用的个人经历（用户笔记）", exact=True)
        ).to_have_value("合成项目笔记")
        assert tracker.list_applications("owner") == before
        identity["owner"] = "other"
        assert client.get("/api/jobscout/feedback").json() == []
        assert (
            client.get(f"/api/jobscout/tracker/applications/{row.id}").status_code
            == 404
        )
        assert (
            client.get(
                "/api/jobscout/preparation/synthetic-report?report_key=" + "a" * 64
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/api/jobscout/feedback",
                json={
                    "request_id": "forged-reference",
                    "category": "trial",
                    "reason": "合成",
                    "followup_of": root_id,
                },
            ).status_code
            == 404
        )
        for query in ("limit=0", "limit=51", "before=-1", "before=9223372036854775808"):
            assert client.get("/api/jobscout/feedback?" + query).status_code == 422
        assert not errors, errors
        assert not unexpected, unexpected
        assert ("PUT", "/api/jobscout/preparation/synthetic-report") in calls
        browser.close()
    (output / "phase4-journey.json").write_text(
        json.dumps(
            {
                "synthetic": True,
                "case_id": "feedback-preparation-v1",
                "git_revision": revision,
                "working_tree_modified": dirty,
                "fixture_sha256": hashlib.sha256(
                    Path(__file__).read_bytes()
                ).hexdigest(),
                "python": platform.python_version(),
                "browser": browser_version,
                "result": "passed",
                "elapsed_seconds": round(time.perf_counter() - started, 2),
                "production_api_requests": len(calls),
                "exported_records": len(records),
                "unexpected_browser_requests": len(unexpected),
                "application_facts_unchanged": True,
                "real_model_portal_mail_calls": 0,
                "excluded": [
                    "real authentication",
                    "model quality",
                    "real recruitment data",
                    "user outcomes",
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
