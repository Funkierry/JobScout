// Same-origin credentials, CSRF and cancellation share one transport boundary.
(function (root, factory) {
  if (typeof module !== "undefined") module.exports = factory();
  else root.JobScoutApi = factory();
})(globalThis, function () {
  function createApiClient({ base = "", fetcher, cookie = () => "" }) {
    return async function request(
      path,
      {
        method = "GET",
        json,
        form,
        headers = {},
        signal,
        timeoutMs = 30000,
      } = {},
    ) {
      const opts = { method, credentials: "include", headers: { ...headers } };
      const signals = [
        signal,
        timeoutMs > 0 ? AbortSignal.timeout(timeoutMs) : null,
      ].filter(Boolean);
      if (signals.length)
        opts.signal =
          signals.length === 1 ? signals[0] : AbortSignal.any(signals);
      if (json !== undefined) {
        opts.headers["Content-Type"] = "application/json";
        opts.body = JSON.stringify(json);
      } else if (form !== undefined) opts.body = form;
      if (method !== "GET" && method !== "HEAD") {
        const match = cookie().match(/(?:^|; )csrf_token=([^;]*)/);
        if (match) opts.headers["X-CSRF-Token"] = decodeURIComponent(match[1]);
      }
      return fetcher(base + path, opts);
    };
  }
  async function decodeResponse(res) {
    let body = null;
    try {
      body = await res.json();
    } catch (_) {
      /* no body */
    }
    if (!res.ok) {
      const msg = body?.detail?.message || body?.detail || res.statusText;
      const error = new Error(
        typeof msg === "string" ? msg : JSON.stringify(msg),
      );
      error.status = res.status;
      error.code = body?.detail?.code;
      throw error;
    }
    return body;
  }
  return { createApiClient, decodeResponse };
});
