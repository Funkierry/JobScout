"""Concurrent tracker/chat browser checks: synthetic streams, zero external I/O."""

import json
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright
from test_ui import ROOT, STAGES, fixtures, page_payload


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            service_workers="block", viewport={"width": 1440, "height": 1000}
        )
        page = context.new_page()
        unexpected, errors, cancellations = [], [], []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def route(r):
            request = r.request
            url = urlsplit(request.url)
            path = url.path
            headers = {
                "Access-Control-Allow-Origin": "http://localhost:5500",
                "Access-Control-Allow-Credentials": "true",
                "Access-Control-Allow-Headers": "content-type,x-csrf-token",
                "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            }
            payloads = {
                "/api/v1/auth/me": {"email": "demo@example.com"},
                "/api/v1/auth/setup-status": {
                    "needs_setup": False,
                    "registration_enabled": True,
                },
                "/api/threads/search": [],
                "/api/jobscout/opportunities": [],
                "/api/jobscout/tracker/stages": STAGES,
                "/api/jobscout/tracker/applications/page": page_payload(
                    fixtures(), url.query
                ),
                "/api/jobscout/tracker/notifications": [],
                "/api/integrations/lark/status": {"installed": False},
                "/api/threads": {"thread_id": "fixture-chat"},
                "/api/v1/auth/logout": {},
            }
            if url.hostname != "localhost" or url.port not in (5500, 8001):
                unexpected.append(request.url)
                r.abort()
            elif request.method == "OPTIONS":
                r.fulfill(status=204, headers=headers)
            elif path in (
                "/",
                "/app.js",
                "/core.js",
                "/tracker-state.js",
                "/api-client.js",
                "/runtime-config.js",
                "/style.css",
            ):
                name = "index.html" if path == "/" else path[1:]
                r.fulfill(
                    path=str(ROOT / name),
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
            elif path.endswith("/cancel"):
                cancellations.append(path)
                r.fulfill(status=202, headers=headers)
            elif path in payloads:
                r.fulfill(
                    body=json.dumps(payloads[path]),
                    content_type="application/json",
                    headers=headers,
                )
            elif path == "/favicon.ico":
                r.fulfill(status=204)
            else:
                unexpected.append(path)
                r.abort()

        context.route("**/*", route)
        page.goto("http://localhost:5500/", wait_until="networkidle")
        page.clock.install()
        expect(page.locator(".tracker-row")).to_have_count(6)
        page.evaluate("""() => {
          const realFetch=window.fetch.bind(window);
          window.fixture={trackerAborts:0, chatAborts:0, starts:0};
          window.fetch=(url,opts={})=> {
            if(String(url).endsWith('/refresh')) return new Promise((resolve,reject)=> {
              fixture.finishTracker=()=>resolve(new Response(JSON.stringify({application:{...trackerRows[0],check_result:'成功'},skipped:false}), {headers:{'Content-Type':'application/json'}}));
              opts.signal?.addEventListener('abort',()=>{fixture.trackerAborts++;reject(opts.signal.reason);},{once:true});
            });
            if(String(url).endsWith('/runs/stream')) {
              fixture.starts++;
              fixture.request=JSON.parse(opts.body);
              if(fixture.holdHeaders) return new Promise((resolve,reject)=>{
                opts.signal.addEventListener('abort',()=>{fixture.chatAborts++;reject(opts.signal.reason);},{once:true});
              });
              const stream=new ReadableStream({start(controller){
                fixture.finishChat=()=>{
                  const ai={type:'ai',content:fixture.report || '合成回复已完成',additional_kwargs:fixture.report
                    ? {jobscout_evidence:{version:2,run_id:'fixture-run',mode:'base_match',accepted_count:0,removed_count:0}}
                    : {jobscout_route:{version:3,run_id:'fixture-run',in_scope:false,missing_fields:[]}}};
                  controller.enqueue(new TextEncoder().encode('event: values\\ndata: '+JSON.stringify({messages:[ai]})+'\\n\\n'));
                  controller.close();
                };
                opts.signal?.addEventListener('abort',()=>{fixture.chatAborts++;controller.error(opts.signal.reason);},{once:true});
              }});
              return Promise.resolve(new Response(stream,{headers:{'Content-Type':'text/event-stream','Content-Location':'/api/threads/fixture-chat/runs/fixture-run'}}));
            }
            if(String(url).includes('/match-candidates/')) return new Promise((resolve,reject)=>{
              fixture.rejectSave=()=>reject(new Error('Synthetic delayed save failure'));
            });
            if(String(url).endsWith('/refresh-all')) {
              const stream=new ReadableStream({start(controller){
                fixture.batchEvent=event=>controller.enqueue(new TextEncoder().encode('data: '+JSON.stringify(event)+'\\n\\n'));
                fixture.closeBatchEarly=()=>controller.close();
                fixture.finishBatch=()=>{fixture.batchEvent({type:'batch_completed',completed:6,total:6});controller.close();};
                opts.signal.addEventListener('abort',()=>controller.error(opts.signal.reason),{once:true});
              }});
              return Promise.resolve(new Response(stream,{headers:{'Content-Type':'text/event-stream'}}));
            }
            return realFetch(url,opts);
          };
        }""")
        page.evaluate("void refreshTrackerRow(1)")
        page.locator("#sidebarPrepBtn").click()
        page.locator("#composerInput").fill("合成公司，后端开发，校招")
        page.locator("#composerSend").click()
        page.wait_for_function("fixture.starts===1")
        page.locator("#sidebarTrackerBtn").click(timeout=1500)
        page.locator("#sidebarPrepBtn").click()
        expect(page.locator("#chatMessages")).to_contain_text("合成公司")
        assert page.evaluate("activeThreadId") == "fixture-chat"
        page.evaluate("fixture.finishTracker()")
        page.wait_for_function("!trackerBusy")
        expect(page.locator("dialog:modal")).to_have_count(0)
        expect(page.locator("#trackerTaskText")).to_contain_text("刷新完成")
        page.evaluate("fixture.finishChat()")
        expect(page.locator("#composerInput")).to_be_enabled()
        expect(page.locator("#chatMessages")).to_contain_text("合成回复已完成")
        page.locator("#composerInput").click()
        expect(page.locator("#composerInput")).to_be_focused()
        page.locator("#trackerTaskDetails").click()
        expect(page.locator("#trackerResultDialog")).to_be_visible()
        page.keyboard.press("Escape")

        page.evaluate("void refreshTrackerRow(1)")
        page.locator("#trackerCancelBtn").click()
        page.wait_for_function("!trackerBusy")
        assert page.evaluate("fixture.trackerAborts") == 1
        expect(page.locator("#trackerTaskText")).to_contain_text("停止")
        page.locator("#composerInput").fill("继续合成请求")
        page.locator("#composerSend").click()
        page.wait_for_function("fixture.starts===2")
        page.locator("#chatCancelBtn").click()
        expect(page.locator("#composerInput")).to_be_enabled()
        assert len(cancellations) == 1
        assert page.evaluate("fixture.chatAborts") >= 1
        assert page.evaluate("fixture.request.on_disconnect") == "cancel"

        # Chat may finish while its panel is hidden; returning restores its result.
        page.locator("#composerInput").fill("合成并行批量请求")
        page.locator("#composerSend").click()
        page.wait_for_function("fixture.starts===3")
        page.locator("#sidebarTrackerBtn").click()
        page.evaluate("void refreshAllTrackerRows()")
        page.evaluate("fixture.batchEvent({type:'batch_started',total:6})")
        page.evaluate("fixture.finishChat()")
        page.wait_for_function("!composerBusy")
        expect(page.locator("#chatStatus")).to_contain_text("投递记录")
        assert page.evaluate("trackerBusy") is True
        page.evaluate("fixture.finishBatch()")
        page.wait_for_function("!trackerBusy")
        expect(page.locator("dialog:modal")).to_have_count(0)
        page.locator("#sidebarPrepBtn").click()
        expect(page.locator("#chatMessages")).to_contain_text("合成并行批量请求")
        expect(page.locator("#chatMessages")).to_contain_text("合成回复已完成")

        # The clock advances only mock timers; this performs no real waiting/I/O.
        page.locator("#composerInput").fill("合成无响应请求")
        page.locator("#composerSend").click()
        page.wait_for_function("fixture.starts===4")
        page.clock.fast_forward(120001)
        expect(page.locator("#composerInput")).to_be_enabled()
        expect(page.locator("#chatMessages")).to_contain_text("连接长时间没有响应")
        assert len(cancellations) == 2

        # Stop before response headers: native cancel-on-disconnect must be sent.
        page.evaluate("fixture.holdHeaders=true")
        page.locator("#composerInput").fill("合成请求尚未收到响应头")
        page.locator("#composerSend").click()
        page.wait_for_function("fixture.starts===5")
        page.set_viewport_size({"width": 390, "height": 844})
        page.evaluate("setMode('tracker')")
        page.locator("#chatTaskReturn").click()
        expect(page.locator("#chatMessages")).to_contain_text("尚未收到响应头")
        page.locator("#chatCancelBtn").click()
        expect(page.locator("#composerInput")).to_be_enabled()
        assert len(cancellations) == 2
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.evaluate("fixture.holdHeaders=false")

        page.evaluate("void refreshAllTrackerRows()")
        page.evaluate("fixture.closeBatchEarly()")
        page.wait_for_function("!trackerBusy")
        expect(page.locator("#trackerTaskText")).to_contain_text("提前中断")
        page.evaluate("void refreshAllTrackerRows()")
        page.locator("#trackerCancelBtn").click()
        page.wait_for_function("!trackerBusy")
        expect(page.locator("#trackerTaskText")).to_contain_text("停止")

        # Logout while a finished matching report is still saving its actions.
        page.evaluate("""() => {
          setMode('match'); activeThreadId='fixture-chat';
          lastBaseContext={thread_id:activeThreadId,records:[],source_url:'https://example.feishu.cn/base/fixture'};
          fixture.report='# 简历 × 飞书岗位匹配报告\\n## 候选人画像\\n合成用户\\n## 推荐岗位\\n无\\n## 匹配依据\\n无';
          const job=beginJob('chat'); setComposerBusy(true);
          void runTurn('合成匹配请求',[],job).finally(()=>{finishJob(job);fixture.matchDone=true;});
        }""")
        page.wait_for_function("fixture.starts===6")
        page.evaluate("fixture.finishChat()")
        page.wait_for_function("typeof fixture.rejectSave==='function'")
        # Late completion from a logged-out session must not open a modal/write data.
        page.evaluate("void refreshTrackerRow(1)")
        page.locator("#logoutBtn").click()
        expect(page.locator("#authView")).to_be_visible()
        page.evaluate("fixture.rejectSave()")
        page.wait_for_function("fixture.matchDone")
        expect(page.locator(".report-row")).to_have_count(0)
        assert page.evaluate("fixture.trackerAborts") == 2
        expect(page.locator("dialog:modal")).to_have_count(0)
        assert page.evaluate("trackerRows.length") == 0
        assert not unexpected and not errors, (unexpected, errors)
        browser.close()
        print(
            "PASS: concurrent chat/refresh, view switching, background results, cancellation and logout; all requests synthetic"
        )


if __name__ == "__main__":
    main()
