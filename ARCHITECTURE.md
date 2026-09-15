# Argus architecture

A guide to how the pieces fit together, aimed at anyone about to change
the code. Read this before adding a feature so you know where things
live and why. README.md is the user-facing intro; this is the
contributor's map.

## The 30-second mental model

Claude Code writes a `.jsonl` file every time you use it, one file per
session, at `~/.claude/projects/<project>/<session-id>.jsonl`. Each
line is one event: a user message, an assistant reply, a tool call, or
a tool result. Codex CLI does the same at
`~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<thread-id>.jsonl`, with a
different line shape (see `python/argus/adapters/AGENTS.md`).

Argus is two things:

1. A **watcher** that reads those files as they grow, validates each
   line, and stores it in a local SQLite database at
   `~/.argus/argus.db`. The database is also an **archive** —
   rows survive even after Claude Code's own cleanup deletes the
   source `.jsonl`.
2. A **local web server** that queries SQLite and serves a static
   dashboard at `http://localhost:4242`.

No network calls (except optional `argus pricing refresh`). No
telemetry. No LLM calls. Everything stays on your machine.

The backend is Python (≥3.11, FastAPI + uvicorn + pydantic + watchdog
+ stdlib sqlite3 + typer). The dashboard is a Vite + React SPA (TanStack
Router/Query/Table, uPlot + SVG charts, Tailwind tokens), statically built.
Both ship together in the `argus-code` wheel on PyPI.

## The data layer

SQLite in WAL mode. Two tables you'll touch most often:

### `sessions`
One row per session. **Pre-summed totals** for fast listing:

- `id` (e.g. `claude_code:abc123…`), `agent`, `project_path`
- `started_at`, `ended_at`, `duration_sec`
- `total_*_tokens` (fresh_input, output, cache_read, cache_write)
- `total_cost_usd`, `primary_model`, `turn_count`

Use for: the Sessions table, "Top sessions in window" lists, anything
that needs a whole-session view.

### `turns`
One row per back-and-forth with Claude — **the source of truth** for
everything else.

- `id`, `session_id`, `sequence`, `timestamp`
- `model`, `model_raw`
- Per-turn token counts (fresh_input, output, cache_read,
  cache_write_5m, cache_write_1h)
- `tool_calls_count`, `cost_usd`, `metadata`

Use for: anything that needs **per-day** granularity — Overview
heatmap, Trends line chart, "Last N days" totals.

> ⚠️ **Critical rule for windowed views.** Query `turns` by their
> `timestamp`, not `sessions` by `started_at`. A session that started
> 3 weeks ago but had turns this week must still count toward "last 7
> days". The canonical helper is
> `Repository.aggregateTurnsByDay(cutoffIso)`.

### Other tables
- `tool_calls` — one row per tool invocation; powers the Tools page
- `prompts`, `transcript_segments` (+ FTS5 siblings) — opt-in full-text
  search indexes
- `file_offsets` — per-file ingest cursor so we re-read only new bytes
- `parse_errors` — surfaced on Settings → Parse errors
- `alerts` — detector findings shown on the Overview "What needs
  attention" card; written **only** by the alert scheduler (see below)
- `app_meta` — schema version, search-indexing flag

## The write path (ingest)

```
~/.claude/projects/<proj>/<session-id>.jsonl      ~/.codex/sessions/.../rollout-*.jsonl
        │                                                   │
        │  watchdog Observer emits add/change events (one Observer per adapter root)
        ▼                                                   ▼
python/argus/adapters/claude_code/                python/argus/adapters/codex/
        │  parse one line at a time; each adapter turns its own line shape into
        │  the shared RawSessionHeader / RawTurnEvent / RawToolCall / RawSegment
        ▼
python/argus/collector/pipeline.py    convert to sessions/turns/tool_calls
        │
        ▼
python/argus/store/repository.py      upsert into SQLite (idempotent — re-ingest is safe)
        │
        ▼
~/.argus/argus.db
```

Two ingest paths share the same `ingest_file` function:

1. **First run** (`python/argus/collector/first_run.py`) — at startup,
   walks every file. Recent files first (foreground), older files in a
   background thread. Dashboard becomes useful within a few seconds.
2. **Watcher** (`python/argus/collector/watcher.py`) — keeps running
   after first-run, picks up new lines as Claude Code writes them. A
   per-path 100ms debouncer coalesces fs-event bursts before handing
   the path to a single ingest worker thread (SQLite WAL = one writer
   at a time).

## The read path (server + dashboard)

```
browser → http://localhost:4242
        │
        ▼
python/argus/server/app.py         FastAPI app + uvicorn, binds 127.0.0.1 by default
        │
        ▼
python/argus/server/api.py         /api/overview, /api/sessions, /api/tools, …
        │
        ▼
python/argus/store/repository.py   parameterized SQL queries
        │
        ▼
dashboard-dist/                    static SPA bundled from dashboard/src/ (Vite + React)
```

The dashboard is **statically built**. No server-side rendering, no
Node at runtime. Pages fetch `/api/...` through a typed, Zod-validated
client (`dashboard/src/lib/api/`) via TanStack Query and render with uPlot
(time series, scatter) and hand-rolled SVG (bars, meters, heatmaps, the
session minimap). Derived analyses (prior-window deltas, per-project
rollups, weekday x hour, tool calls per day) are pure functions in
`dashboard/src/lib/analysis/`, computed client-side from existing endpoints;
each carries a `// candidate-endpoint:` note where a server aggregate would
scale better. Deep links such as `/sessions/<id>` are served by the
`index.html` fallback in `server/app.py` and resolved by the client router.

## Detection (alerts)

Beyond passively charting history, Argus runs **detectors** that flag
behavior worth your attention. The flow reuses ingest's separation of
concerns — readers read, one writer writes:

```
python/argus/detectors/registry.py   @register + available_detectors()
        │  each detector is pure: detect(repo, now_iso) reads, returns Findings
        ▼
python/argus/collector/scheduler.py  the ONLY writer of alerts; runs every
        │                            detector on a fixed cadence in a thread
        ▼
~/.argus/argus.db  →  alerts table   upsert by (detector, dedup_key);
        │                            idempotent, with a resolved_at lifecycle
        ▼
python/argus/server/api.py           GET /api/alerts, GET /api/alerts/unseen,
        │                            POST /api/alerts/{id}/seen
        ▼
dashboard "What needs attention"     card (hidden when empty) + a browser
                                     Notification on unseen critical findings
```

Rules that matter:

- **Detectors are pure.** `detect(repo, now_iso)` reads the DB and returns
  `Finding` objects — it never writes. The scheduler owns every write. That
  boundary is what makes detectors trivially unit-testable.
- **Idempotent upserts.** `upsert_alert` keys on `(detector, dedup_key)` with
  `ON CONFLICT … DO UPDATE … RETURNING id`, so re-running a detector updates
  the existing row instead of duplicating.
- **`resolved_at` lifecycle.** Each tick the scheduler resolves alerts whose
  `dedup_key` is no longer in the detector's current findings
  (`resolve_stale_alerts`). If the issue recurs, the row un-resolves and its
  `seen_at` clears — so a tool that recovers then re-spikes notifies again
  rather than staying silent.
- **Self-registration.** Detectors `@register` in their module, imported for
  side-effect in `python/argus/detectors/__init__.py` — same pattern as
  adapters. Adding one is a new file + the import line; no scheduler edits.

The v1 detector (`tool_error_rate_spike`) compares each tool's last-7-day
error rate against its preceding 4-week baseline and fires when the rate
multiplies past a threshold — or breaks from a zero baseline. Cost was
deliberately *not* the v1 signal: it's meaningless for Pro/Max users who
don't pay per token.

## Scaffolding (`argus claude`)

Separate from ingest/serve: a file-copy tool that stamps a `.claude/` setup
(and a root `CLAUDE.md`) into a project — proactive config *before* the agent
runs, not just observation after. No DB, no daemon, no schema.

```
python/argus/scaffold/storage.py     resolve template names across two tiers
python/argus/scaffold/scaffolder.py  init: copy a template dir → a project
python/argus/scaffold/snapshot.py    create: snapshot a project's .claude/
        │
        ▼
templates/<name>/                    bundled (read-only, shipped in the wheel)
~/.argus/templates/<name>/           user templates (read-write)
```

- **Two tiers, user-first.** `resolve_template` checks `~/.argus/templates/`
  then the bundled `templates/`. The bundled `default` ships in the wheel via
  `force-include`, exactly like `pricing/` and `dashboard-dist/`.
- **Pure file copy.** No templating or substitution in v1. `init` copies each
  template file to its matching path in the project (`CLAUDE.md` → root,
  everything else → `.claude/`).
- **`CLAUDE.md` is never overwritten**, even with `--force` — it's the user's
  project brain. `create` is scoped to `.claude/` and excludes session/cache
  dirs from the snapshot.

## Conventions that matter

A few patterns to absorb before adding code:

- **Cutoff-string aggregation.** Windowed queries take an ISO
  timestamp string and use `WHERE timestamp >= ?`. Pass `''` for
  "all time" (empty string sorts below every real ISO string).
  Examples: `aggregateTurnsByDay`, `toolCallsTotal`, `mcpToolCalls`.

- **Top-level vs sub-agent ids.** Sub-agent rollup sessions contain
  `/` in their `id`. SQL excludes them with
  `session_id NOT LIKE '%/%'`; JS excludes them with the
  `isTopLevel(id)` helper. Sub-agent token usage is rolled into the
  parent's totals during ingest, so excluding them at read time
  prevents double-counting.

- **Migrations are append-only.** Never edit a published migration in
  `python/argus/store/migrations/inline.py`. Add a new `MIGRATION_N+1`
  and bump the schema version check in `db.py`. Production users have
  data that depends on the exact SQL that already ran.

- **Idempotent ingest.** `upsert_turn`, `upsert_session`, etc. all use
  `ON CONFLICT(id) DO UPDATE`. Re-ingesting a file from offset 0 is
  safe — useful for backfilling derived data after a schema change.

- **Never write raw HTML in the dashboard.** React renders every
  JSONL-derived string as a text node; `dangerouslySetInnerHTML` and
  `innerHTML` are forbidden (`tests/dashboard/test_no_raw_html.py`). FTS5
  `<mark>` snippets are split into text runs by
  `dashboard/src/features/search/cleanSnippet.ts`.

- **CSRF Origin check.** All non-GET `/api/*` routes verify the
  `Origin` header is loopback. Implemented as FastAPI middleware in
  `python/argus/server/app.py`. Defends against random tabs in your
  browser hitting argus while it's running.

- **Path safety.** `discover_session_files` calls `Path.resolve(strict=True)`
  on each candidate and rejects anything that doesn't canonicalise
  under `~/.claude/`. Defends against a hostile symlink planted in
  `projects/` pointing at e.g. `/etc/passwd`. On Windows the
  containment check lowercases both sides so case differences in the
  canonical path don't silently reject every candidate.

- **Adapter registry.** Adapter classes self-register via the
  `@register` decorator (`python/argus/adapters/registry.py`).
  `available_adapters()` returns every registered class whose
  `is_present()` is True. Two ship today: `claude_code` and `codex`.
  Adding another (OpenClaw, Hermes, …) is one new folder + one
  `@register` — zero edits to CLI, watcher, pipeline, or server.
  The one thing the collector asks of an adapter beyond parsing is
  `native_session_id(path)`: how a stored `<agent>:<id>` maps back to
  its file. Never assume the file stem.

## Where things live

```
python/argus/
  cli.py                    argus start / search / pricing / claude / wipe (typer)
  adapters/
    base.py                 Adapter protocol + ParseError
    registry.py             @register + available_adapters()
    claude_code/            JSONL parsers, discovery, history.jsonl
    codex/                  rollout parsers (envelope + legacy), thread index, history.jsonl
  collector/                pipeline, watcher, first-run, backfills, alert scheduler
  detectors/                alert detectors (pure reads) + @register registry
  scaffold/                 argus claude: template storage / init / snapshot
  pricing/                  LiteLLM-derived price table + cost compute
  store/                    SQLite repository + migrations
  server/                   FastAPI app (app.py) + routes (api.py)
  schema/                   pydantic data models

dashboard/
  src/
    app/                    router, query client, shell (sidebar, top bar, Ctrl+K)
    routes/                 TanStack file routes (one per page + legacy redirects)
    features/<page>/        page components and page-specific hooks
    components/ui/          Panel, Tile, Pill, Chip, Seg, Button, DataTable, ...
    components/charts/      uPlot wrappers + SVG charts (rules in README.md)
    lib/api/                Zod schemas, typed client, React Query hooks, fixtures
    lib/analysis/           pure derived analyses (unit-tested)
    lib/format/             number / date formatters
    styles.css              Tailwind v4 tokens (@theme) + base rules
dashboard-dist/             built dashboard, shipped in the wheel as data

pricing/                    bundled LiteLLM price tables (shipped in wheel as data)
templates/                  bundled .claude/ scaffolding templates (shipped in wheel as data)
tests/                      pytest suite, mirrors python/argus/ layout
~/.argus/argus.db           SQLite DB (created on first run)
~/.argus/templates/         user-saved scaffolding templates (argus claude template create)
~/.claude/                  Claude Code source data we read from
~/.codex/                   Codex CLI source data we read from ($CODEX_HOME)
```

## What's deliberately NOT here

- **No embeddings, no vector DB.** Search is SQLite FTS5 — inverted
  index, BM25 ranking, sub-millisecond, fully offline.
- **No external APIs** except the optional `argus pricing refresh`
  (single HTTP GET to LiteLLM's GitHub).
- **No message queues or external workers.** Concurrency is at most three
  in-process threads: the file watcher, the HTTP server, and a lightweight
  detector scheduler. No brokers, no cron, no separate processes.
- **No auth.** Loopback binding is the security model — see
  [SECURITY.md](./SECURITY.md).

## Common changes and where to start

| Want to… | Touch these |
|---|---|
| Add a new dashboard page | `dashboard/src/routes/<name>.tsx` + `dashboard/src/features/<name>/<Name>Page.tsx`, add it to `GROUPS` in `dashboard/src/app/shell/Sidebar.tsx` and `PAGES` in `CommandPalette.tsx` |
| Add a new API endpoint | `python/argus/server/api.py`, add a `repo.<method_name>()` in `python/argus/store/repository.py` |
| Add a new SQL table or column | New `MIGRATION_N+1` in `python/argus/store/migrations/inline.py`, bump schema check in `db.py`, add `repo` method, add pydantic model in `python/argus/schema/types.py` |
| Parse a new JSONL field | `python/argus/adapters/claude_code/schemas.py` (pydantic), wire into `pipeline.py` |
| Tweak cost computation | `python/argus/pricing/compute.py`, then re-ingest to recompute via a backfill |
| Add a new chart | `dashboard/src/components/charts/` (read its `README.md` first; series colours in `uplotTheme.ts`) |
| Add a new adapter (OpenClaw, Hermes, …) | New folder `python/argus/adapters/<agent>/` + `@register class` in `adapter.py` implementing `native_session_id`. No edits to CLI / watcher / pipeline / server. `adapters/codex/` is the second worked example. |
| Add a display name for a new agent | `dashboard/src/lib/agents.ts` (`AGENT_LABEL`, `resumeHint`). |
| Add an alert detector | New file in `python/argus/detectors/` with a `@register` class whose pure `detect()` returns `Finding`s, plus the side-effect import in `detectors/__init__.py`. The scheduler picks it up automatically. |
| Change the bundled scaffold template | Edit files under `templates/default/`; they're force-included into the wheel. New top-level dirs need a `force-include` entry in `pyproject.toml`. |
