// Deferred fixtures exercise the real loaders without network or timers.
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

function fixture() {
  const context = vm.createContext({ console, TextDecoder, URL, URLSearchParams, require, module: { exports: {} } });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "app.js"), "utf8"), context);
  const nodes = new Map();
  context.document = { getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, { textContent: "", classList: { add() {}, remove() {}, toggle() {} }, setAttribute() {}, close() {} });
    return nodes.get(id);
  } };
  vm.runInContext("showTrackerError=()=>{}; renderTrackerRows=()=>{}; renderThreadList=()=>{}; renderOpportunityBar=()=>{}; setTrackerAddFormOpen=()=>{}; loadTrackerNotifications=async()=>[];", context);
  const pending = [];
  context.apiJson = () => new Promise(resolve => pending.push(resolve));
  return { context, pending };
}

async function main() {
  for (const [loader, variable, older, newer] of [
    ["loadTrackerApplications", "trackerRows", [{ id: 1, status: "old" }], [{ id: 1, status: "new" }]],
    ["loadThreadList", "threadListCache", [{ thread_id: "deleted" }], [{ thread_id: "retained" }]],
    ["loadOpportunities", "opportunities", [{ id: 1, prep_thread_id: "deleted" }], [{ id: 1, prep_thread_id: null }]],
  ]) {
    const { context, pending } = fixture();
    const payload = rows => loader === "loadTrackerApplications" ? { items: rows, total: rows.length, summary: { total: rows.length }, stages: [], has_more: false } : rows;
    const first = vm.runInContext(`${loader}()`, context);
    const last = vm.runInContext(`${loader}()`, context);
    pending[1](payload(newer)); await last;
    pending[0](payload(older)); await first;
    assert.strictEqual(vm.runInContext(`JSON.stringify(${variable})`, context), JSON.stringify(newer), "late response must not replace newer state");
    const inFlight = vm.runInContext(`${loader}()`, context);
    vm.runInContext(`invalidateSessionViews(); ${variable}=[];`, context);
    pending[2](payload(older)); await inFlight;
    assert.strictEqual(vm.runInContext(`${variable}.length`, context), 0, "old session must not repopulate cleared state");
  }
  const { context, pending } = fixture();
  const request = vm.runInContext("loadThreadList()", context);
  vm.runInContext('deletedThreadIds.add("deleted");', context);
  pending[0]([{ thread_id: "deleted" }, { thread_id: "retained" }]); await request;
  assert.strictEqual(vm.runInContext('JSON.stringify(threadListCache.map(t => t.thread_id))', context), '["retained"]');
  {
    const { context, pending } = fixture();
    vm.runInContext('pendingThreadDelete={id:"delete-me", title:"Fixture"}; threadListCache=[{thread_id:"delete-me"}];', context);
    const deletion = vm.runInContext("confirmThreadDelete()", context);
    await vm.runInContext("confirmThreadDelete()", context);
    assert.strictEqual(pending.length, 1, "repeated confirmation must send only one DELETE");
    assert.strictEqual(context.document.getElementById("threadDeleteConfirm").disabled, true);
    vm.runInContext('invalidateSessionViews(); threadListCache=[{thread_id:"other-user"}];', context);
    pending[0]({ deleted: true }); await deletion;
    assert.strictEqual(vm.runInContext('threadListCache[0].thread_id', context), "other-user", "old deletion result must not alter a new session");
  }
  {
    const { context, pending } = fixture();
    context.rendered = [];
    vm.runInContext('closeSidebar=()=>{}; clearAttachment=()=>{}; setMode=()=>{}; renderHistoryMessages=messages=>rendered.push(messages);', context);
    const old = vm.runInContext('selectThread("old")', context);
    const latest = vm.runInContext('selectThread("latest")', context);
    pending[1]({ values: { messages: ["new"] } }); await latest;
    pending[0]({ values: { messages: ["old"] } }); await old;
    assert.strictEqual(JSON.stringify(context.rendered), '[["new"]]', "late history must not replace the selected thread");
  }
  console.log("PASS: out-of-order lists, session changes and deleted history stay isolated");
}

main().catch(error => { console.error(error); process.exitCode = 1; });
