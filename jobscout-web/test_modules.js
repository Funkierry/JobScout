const assert = require("node:assert/strict");
const { createApiClient, decodeResponse } = require("./api-client.js");
const { TrackerPager } = require("./tracker-state.js");

async function main() {
  const calls = [];
  const api = createApiClient({
    base: "https://fixture.invalid",
    cookie: () => "csrf_token=fixture%2Btoken",
    fetcher: async (...args) => {
      calls.push(args);
      return { ok: true };
    },
  });
  await api("/read");
  await api("/write", { method: "POST", json: { value: 1 } });
  assert.equal(calls[0][0], "https://fixture.invalid/read");
  assert.equal(calls[0][1].credentials, "include");
  assert.equal(calls[0][1].headers["X-CSRF-Token"], undefined);
  assert.equal(calls[1][1].headers["X-CSRF-Token"], "fixture+token");
  assert.equal(calls[1][1].body, '{"value":1}');
  const controller = new AbortController();
  await api("/cancel", { signal: controller.signal });
  controller.abort();
  assert.equal(calls[2][1].signal.aborted, true);
  await api("/timeout", { timeoutMs: 5 });
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(calls[3][1].signal.aborted, true);
  await assert.rejects(
    decodeResponse({
      ok: false,
      status: 409,
      json: async () => ({ detail: { code: "conflict", message: "Changed" } }),
    }),
    (error) =>
      error.status === 409 &&
      error.code === "conflict" &&
      error.message === "Changed",
  );

  const pager = new TrackerPager();
  const data = {
    items: [{ id: 100 }],
    summary: { total: 100 },
    stages: [],
    has_more: true,
    next_before_id: 51,
  };
  pager.accept(data);
  assert.equal(pager.next(), true);
  assert.ok(pager.url().includes("before_id=51"));
  pager.reject();
  assert.equal(pager.page, 1);
  assert.equal(pager.data, data);
  pager.filter("准备材料");
  assert.equal(
    new URL(pager.url(), "https://fixture.invalid").searchParams.get("stage"),
    "准备材料",
  );
  pager.reject();
  assert.equal(pager.stage, null);
  pager.reset();
  assert.equal(pager.data, null);
  assert.equal(pager.next(), false);
  console.log(
    "PASS: shared API cancellation/CSRF/errors and page/filter failure recovery",
  );
}
main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
