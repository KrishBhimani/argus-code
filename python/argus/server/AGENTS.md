# AGENTS.md — server (FastAPI app + API)

Parent: `python/argus/AGENTS.md`. Serves the API and the static dashboard at
`http://localhost:4242`.

## Purpose

`app.py` builds the app, serves the dashboard, and runs uvicorn; `api.py` defines
the `/api/...` routes over the repository.

## Local Contracts

- **Route registration order is load-bearing.** Session ids use the greedy
  `{session_id:path}` converter (so sub-agent ids containing `/` match). The bare
  `GET /api/sessions/{session_id:path}` handler must be registered **last** — any
  suffix route (`/timeline`, `/tool-output/{tool_use_id}`, `/transcript`,
  `/subagents`) must be declared **before** it, or the bare route shadows them. Add
  new session subroutes above the bare handler.
- **Static serving tolerates Windows path quirks.** The dashboard mount uses
  `SafeStaticFiles`, whose `lookup_path` swallows `OSError` and returns
  `("", None)` — needed because a `:` in a URL segment makes Starlette raise on
  Windows. Keep that subclass; don't mount plain `StaticFiles`.
- **Aggregate reads are memoised** (`_ReadCache` in `api.py`): `/api/overview`,
  `/api/trends`, `/api/tools/overview` cache per argument set and are invalidated
  by a data fingerprint (row counts / max rowids of sessions, turns, tool_calls and
  sessions.max(computed_at)). Every query parameter that changes the answer
  (window, granularity, groupBy, `tz`) must be in the cache key. If you make one of those endpoints read a new table,
  add it to the fingerprint or the cache will serve stale answers.
- **Days are the viewer's days.** `/api/overview` and `/api/trends` take `tz`
  (minutes east of UTC, `-Date#getTimezoneOffset()`; ±840) and bucket with
  `date(timestamp, '+N minutes')` in `aggregate_turns_by_day`, so their day keys
  equal the dashboard's local `dayKeys()`. `tz` is part of the `_ReadCache` key.
  Window *cutoffs* stay rolling UTC instants (`now - n days`).
- **`/api/overview.prior_window`** = `{tokens, cost_usd, sessions}` over
  `[now - 2n, now - n)` (null for `all`), computed with the same definitions as
  the window's own totals (`turn_totals_between`). Deltas must use it — summing
  whole calendar days client-side overlapped the rolling window.
- **SPA fallback.** The dashboard is client-routed. A `GET` that is not
  `/api/*`, whose last path segment has no extension, and misses the static
  mount returns `dashboard-dist/index.html` (200) via the 404 exception handler
  registered next to the mount. Asset and API misses stay 404; no fallback when
  the dashboard dir is absent. Covered by `tests/server/test_spa_fallback.py`.
- **Clean shutdown.** `serve_blocking` pre-installs SIGINT/SIGTERM handlers that
  flip uvicorn's `should_exit` (then `force_exit`) before `server.run()`, and
  restores the previous handlers in a `finally`. This is what makes Ctrl+C exit
  without a traceback — don't remove it.
- **Read-only under the daemon.** When `argusd` is running, mutating endpoints
  return 409 pointing at the CLI (`argus indexing enable`) or `argus daemon stop`.
- **The Origin allowlist is exact, and port-aware.** `is_allowed_origin(origin,
  port)` matches argus's own origin only — never a prefix, never "any loopback
  port". Loopback is a neighbourhood of mutually untrusting web origins (a dev
  server on `:3000`, a notebook on `:8888`), not one principal. Anything
  constructing the app must pass the real `opts.port` or same-origin POSTs break.
- **A loopback `Host` header is required** (`is_loopback_host` + the
  `host_header_check` middleware). This is the DNS-rebinding guard: the Origin
  check exempts GET and every data-bearing route is a GET, so without this a page
  that rebinds its own domain to `127.0.0.1` becomes same-origin in the browser
  and can read the user's entire prompt history. Don't relax it to satisfy a
  test — `TestClient` defaults to `Host: testserver`, so tests must pass
  `base_url=LOOPBACK`. The check is skipped only when the user deliberately bound
  a non-loopback interface (`--host 0.0.0.0`), which is a warned opt-out.

## Verification

`uv run pytest tests/server` — covers route ordering, `SafeStaticFiles`, the SPA
fallback, and the sub-agent URL (no 500).
