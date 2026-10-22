# Backend Tests

Backend tests must preserve the runtime invariants they exercise without changing production execution topology.

JobScout workbench tests use synthetic records and temporary SQLite files. Cover
owner/report isolation, optimistic write conflicts, lost-response idempotency,
thread cleanup, compact all-page pending items and unchanged recruitment facts.
Frontend persistence/retry/logout coverage lives in `jobscout-web/test_workbench.py`.
`jobscout-web/test_journey.py` runs separately under the offline runner with a
browser: intercepted requests dispatch to production FastAPI routes and temporary
SQLite. Identity/history are fixtures; do not claim live auth or model evaluation.
Deployment tests derive scripts from the real HTML entrypoint and check static
Nginx routes, image COPY sources and explicit build-context inclusion together.
The container smoke must request every shipped helper, including workbench.js.

## Executor starvation tests

`test_executor_starvation.py` covers the deterministic starvation semantics from RFC #4560:

- default-executor saturation and queueing;
- cancellation of an awaiter while an already-started synchronous worker continues;
- isolation between the asyncio default executor and DeerFlow's dedicated file-I/O executor.

Use explicit synchronization such as `threading.Event` rather than sleep-based timing thresholds for worker lifecycle assertions. Every test must release blocked workers and restore any process-global monkeypatches so teardown cannot leak threads or state into later tests.

Stress/soak testing, AnyIO worker instrumentation, Uvicorn multi-process behavior, and broad production executor redesign are separate concerns and should not be folded into these deterministic regressions.

Tracker role identity regressions cover optional preference badges, ambiguous
aliases, cross-owner isolation, and distinct application URLs. Matching regressions
replay cropped/negated skill quotes and direction-based proficiency bypasses; keep
user data and paid model calls out of offline tests.
