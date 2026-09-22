# Argus testing guide

A catalog of every test file in the Argus codebase, what each test verifies,
and the conventions tests follow. This doubles as the parity checklist for
the in-flight TypeScript → Python backend migration: every TS test below
needs a Python equivalent with the same assertions before the TS source is
deleted.

## Tooling

| | Today (TS) | After migration (Python) |
|---|---|---|
| Runner | `vitest` | `pytest` |
| HTTP client | hono's built-in `app.request()` | `httpx` via Starlette's `TestClient` |
| Fakes / mocks | `vi.stubGlobal`, `vi.fn` | `monkeypatch`, `unittest.mock` |
| Time control | (manual `Date.now()` math) | `freezegun` |
| Run command | `npm test` | `uv run pytest` |
| Suite size | ~100 tests, ~3s | target: same count, same intent |

## Conventions

- **One tmpdir per test.** Every test that touches the filesystem creates a
  fresh `mkdtempSync(join(tmpdir(), 'argus-...'))` in `beforeEach` to avoid
  cross-test contamination. Python equivalent: pytest's `tmp_path` fixture.
- **DB fixtures use a real on-disk file**, not `:memory:`, because some
  paths (`dbSizeBytes`, `vacuum`, WAL checkpointing) need real files.
- **Shared synthetic-line factories** (`mkAssistant`, `mkUser`, `ASSISTANT`,
  `SESSION`, `TURN`) are duplicated across test files rather than centralized.
  Same convention will carry over — pytest fixtures in `conftest.py` per
  module folder.
- **Regression tests are labeled inline** with a comment naming the bug
  they protect against (search for `Regression:` in the test bodies).
- **Real-environment tests are gated by env var.** `integration.test.ts`
  runs only when `ARGUS_REAL_CLAUDE_ROOT` is set; otherwise it's skipped.
  This pattern is opt-in for anything that needs a real `~/.claude/`.

## Test catalog

### `src/adapters/claude_code/`

#### `discover.test.ts` — session-file discovery (3 tests)

- finds top-level `.jsonl` files under `projects/<encoded-dir>/` and
  excludes non-jsonl siblings like `sessions-index.json`
- `subAgentFilesFor(parentFile)` returns every JSONL inside the matching
  `<parent>/subagents/` folder
- returns empty array when no `subagents` directory exists for the parent

#### `schemas.test.ts` — zod line validation (4 tests)

- parses an assistant line with full `usage` block
- accepts assistant line with the newer `cache_creation.ephemeral_5m_input_tokens`
  / `ephemeral_1h_input_tokens` tier breakdown
- parses a user line with string `content`
- passthrough preserves unknown fields like `attribution_agent`, `isSidechain`,
  `agentId` (lines stay round-trippable even if Claude adds new keys)

#### `model.test.ts` — model-name canonicalization (3 tests)

- alias forms pass through unchanged: `claude-opus-4-7`, `claude-sonnet-4-6`,
  `claude-haiku-4-5`
- date suffix is stripped: `claude-opus-4-5-20251101` → `claude-opus-4-5`
- unknown model strings are returned as-is

#### `extract_turns.test.ts` — turn-event extraction (4 tests)

- dedupes by `message.id` when multiple lines share it (Claude Code can emit
  one line per content block in some versions)
- counts `tool_use` blocks across all deduped lines for the same turn
- maps `usage.cache_read_input_tokens` → `cache_read_tokens` and
  `usage.cache_creation_input_tokens` → `cache_write_tokens`
- extracts the `cache_creation.ephemeral_5m_input_tokens` /
  `ephemeral_1h_input_tokens` tier breakdown when present

#### `extract_tool_calls.test.ts` — tool-call extraction (7 tests)

- one row per `tool_use` block; `block_index` reflects content-array position
- `subagent_type` is populated only for the `Task` tool (others get null)
- `is_error` is attributed by matching `tool_result.tool_use_id` in the
  next user message
- defaults `is_error` to 0 when no matching tool_result exists
- MCP tool names like `mcp__context7__query-docs` pass through unchanged
- `input_size` equals `JSON.stringify(input).length`
- dedupes blocks across multiple lines that share `message.id`; both share
  the same `turn_index`

#### `extract_transcript.test.ts` — transcript segment extraction (8 tests)

- one segment per assistant `text` block; `tool_use` blocks are skipped
  but their position in `content[]` shifts the next block's `block_index`
- `thinking` blocks become segments with `role='thinking'`
- empty / whitespace-only text blocks are skipped
- user message with string `content` emits one `role='user'` segment
- user `tool_result` content (string form) → `role='tool_result'` segment
- user `tool_result` with array content is flattened to one segment
  joining all text children
- oversized text (>16 KB) is truncated with an ellipsis suffix
- chronological pairing: user and assistant segments preserve their own
  timestamps across both input arrays

#### `history_jsonl.test.ts` — `~/.claude/history.jsonl` ingest (13 tests)

`lineToPrompt(...)`:
- skips empty / whitespace-only `display`
- flags slash commands via `is_slash` (`/exit` → 1, `hello` → 0)
- normalizes Windows project paths: backslashes → forward slashes,
  drive letter lowercased
- `pasted_chars > 0` only when `pastedContents` is non-empty
- truncates `display` past 8 KB cap with ellipsis suffix
- leading-slash detection survives `trimStart` (`"   /clear"` → slash)

`ingestHistoryFile(...)`:
- byte-offset tail: subsequent calls read only new bytes
- holds back a partial last line until newline arrives
- skips malformed lines and continues (recorded in `parse_errors`)
- resets offset and re-ingests when the file shrinks below the recorded
  offset (history rotation case)
- FTS5 search returns matches with `<mark>` snippet markers
- empty query returns chronological recents (most recent first)
- slash commands excluded by default; `includeSlash: true` brings them back

#### `index.test.ts` — adapter façade (1 test)

- `ClaudeCodeAdapter` exposes `agent = 'claude_code'` and reports the
  constructor root via `rootPath()`

#### `ingest_file.test.ts` — single-file ingest (5 tests)

- parses a fresh file from offset 0; returns the new byte offset
- subsequent ingest with `from_offset` parses only the new tail
- a malformed line is recorded as a parse_error; surrounding lines still
  ingest successfully
- a partial trailing line is preserved (not parsed) until completed by
  a future append
- session header derives `project_path` from the `cwd` JSONL field, not
  from the encoded directory name on disk

#### `integration.test.ts` — real `~/.claude/` smoke (2 tests, gated)

Only runs when `ARGUS_REAL_CLAUDE_ROOT` env var points at a real Claude Code
log directory:

- `discoverSessionFiles` finds at least one file
- ingesting a real session keeps parse errors below 5% (loose
  conformance check against real-world JSONL drift)

### `src/collector/`

#### `aggregate.test.ts` — session aggregation (2 tests)

- rolls up turn-level totals into session: `total_fresh_input_tokens`,
  `total_output_tokens`, cost (verified against table-driven expected
  value), `turn_count`, and id format `{agent}:{native_session_id}`
- picks `primary_model` by sum of (input + output) tokens — not by
  first turn, not by last turn

#### `rollup_subagents.test.ts` — sub-agent → parent rollup (1 test)

- sums each sub-agent session's token + cost totals into the parent
- captures sub-agent ids in `metadata.sub_agent_session_ids` for traceability

#### `test_incremental_invariant.py` — chunked ingest == one-pass ingest (Python)

- **Regression (H1).** Ingest one fixture whole, line by line, and in 7/64/333
  byte chunks: session row, turns and tool_calls must be identical. Covers
  session start/duration/project, file-wide turn order, a streamed message
  split across ticks, and a tool error whose result lands in a later tick
  (parent and sub-agent).
- an offset-0 re-read rewrites pre-fix rows instead of merging into them

#### `test_incremental_repairs.py` — one-shot repairs for pre-fix rows (Python)

- `repair_session_duration_v1` recomputes duration/`*_at_ms` in-DB, idempotent
- `backfill_tool_errors_v1` re-reads every session on disk once; its flag only
  flips after the capped sweep has really finished

#### `test_fork_dedup.py` — forked sessions don't re-count copied turns (Python)

- **Regression (H2).** Parent-then-fork and fork-then-parent both leave the copied
  turns/tool calls only on the parent and the fork's start at its own first turn;
  a differing `session_id` alone never drops a turn; the one-shot repair removes
  pre-fix copies and is idempotent.

#### `pipeline.test.ts` — end-to-end ingest pipeline (4 tests)

- **Regression: turn merging.** Incremental re-ingest must NOT reduce
  session totals. Append a new turn → total goes up. Re-ingest with no
  new content → total stays the same. (Earlier broken version
  recomputed totals from only the newly-read turns, losing prior totals.)
- **Regression: sub-agent double-count.** `discoverSessionFiles` must NOT
  list sub-agent files; they're walked as part of the parent's ingest. A
  parent + one sub-agent must end up with `total_fresh = parent + sub`,
  not `parent + sub + sub`.
- ingests a synthetic Claude Code session end-to-end (session row +
  positive cost + file offset advanced)
- segment-write gating: with `enable_transcript_search` OFF, ingest
  records zero segments; flip ON and re-ingest from offset 0 → segments
  appear

#### `first_run.test.ts` — recent-vs-older two-phase ingest (1 test)

- recent files (within `recentDays`) complete in foreground; older
  files only after `backfillDone`. Status object (`processed`,
  `total`, `pending`) updates correctly across both phases.

#### `watcher.test.ts` — chokidar live-tail (1 test)

- start watcher → append a line to a session file → wait for debounce
  → DB has the new turn

### `src/pricing/`

#### `load.test.ts` — bundled pricing table (2 tests)

- loads the bundled `pricing/<version>.json`; version + known model
  prices match
- unknown model lookup returns `undefined`

#### `compute.test.ts` — per-turn cost (4 tests)

- Anthropic with explicit 5m + 1h cache write tiers: cost ≈
  `5 + 25 + 0.50 + (0.7 × 6.25) + (0.3 × 10)` for a 1M-tokens-per-bucket turn
- Anthropic without tier breakdown falls back to the 5m rate
- OpenAI with cached input only uses `cache_read` rate (no cache write tier)
- unknown model returns cost = 0 (not NaN, not error)

#### `refresh.test.ts` — LiteLLM pricing refresh (2 tests)

- `diffPricing(old, new)` returns added / removed / changed / unchanged
  arrays
- `fetchLiteLlmTable(url)` parses LiteLLM's per-token JSON into Argus'
  per-MTok shape; filters out the `sample_spec` placeholder; uses
  `vi.stubGlobal('fetch', ...)` to avoid real network

### `src/schema/`

#### `types.test.ts` — type-level shape (2 tests)

- `Session` has all required fields and `s.agent` type-equals `AgentName`
- `NormalizedCacheFields` enforces the Codex zero-cache-write rule
  (cache_write_tokens may be 0; tier fields may be null)

Note: these are compile-time `expectTypeOf` checks. Python equivalents
will be runtime pydantic-model construction tests, since static-type
assertions don't translate.

### `src/server/`

#### `api.test.ts` — HTTP API contract (13 tests)

- `GET /api/sessions` — list sorted by `started_at` desc
- `GET /api/sessions/:id` — returns session + its turns; missing → 404
  (not asserted here, see Repository test for the missing case)
- `GET /api/overview` — totals for window; agent split adds up
- **Regression: long-runner exclusion.** `GET /api/overview` includes
  turns from sessions whose `started_at` is OUTSIDE the window. Resumed
  sessions must appear in `total_cost_usd`, `cost_by_day`, `top_sessions`,
  `cost_by_model`.
- **Top Sessions window math.** Each `top_sessions` row reports window
  contribution (not lifetime). Sum of rows = hero total. Lifetime $99
  outside window does NOT leak in.
- **Multi-day distribution.** A session with turns on days 1 and 3
  must distribute cost across both days in `cost_by_day` (the old
  bug dumped all lifetime cost on `started_at`'s day).
- `GET /api/trends?granularity=day&groupBy=agent` — points array
  populated
- `GET /api/ingest/status` — proxies through the status callback
- `GET /api/pricing` — version string
- `GET /api/export.json` — list of all sessions
- `GET /api/export.csv` — content-type `text/csv`
- `GET /api/parse-errors` — array shape

#### `server.test.ts` — server bootstrap (1 test)

- one port serves both `/api/*` and static files from `dashboardDir`;
  index.html returns 200 with body content; API still returns 200

### `src/store/`

#### `db.test.ts` — schema + migrations (3 tests)

- fresh open creates all expected tables: `sessions`, `turns`,
  `file_offsets`, `parse_errors`, `app_meta` (+ migration-2 / -3 tables)
- WAL journal mode is enabled
- second open of an existing DB is idempotent (no migration re-runs
  that would error)

#### `repository.test.ts` — repository surface (14 tests)

- upsert + read a session round-trips (metadata JSON included)
- upsert is idempotent (re-upsert with changed fields wins)
- upsert + read turns round-trips
- `listSessions` sorts by `started_at` desc
- `setFileOffset` / `getFileOffset` round-trips; missing path returns 0
- `recordParseError` + `recentParseErrors` round-trips
- `upsertToolCalls` + `toolLeaderboard` aggregates correctly; re-upsert
  with the same ids doesn't duplicate
- `linkPromptToSession` finds a session by `(project, timestampMs)`;
  returns null outside the time window or for the wrong project
- `subagentCalls` returns only rows with `subagent_type` set; rolls up
  by type with count + error count
- `insertPrompts` + `searchPrompts` (FTS5) returns ranked matches with
  `<mark>` snippet markers
- `upsertTranscriptSegments` + `searchTranscripts` (FTS5) returns
  ranked matches with snippet markers
- `searchTranscripts` respects role filter (`roles: ['user']` excludes
  assistant hits)
- `searchTranscripts` is upsert-idempotent (same `uid` overwrites text,
  no duplicate row, FTS reflects the new text)
- search indexing flag defaults OFF on a fresh DB
- search indexing flag defaults ON when segments already exist on an
  upgraded install (migration default)
- `setSearchIndexingEnabled` persists across reads
- `clearAllSegments` wipes the table AND the FTS5 index (search after
  clear returns 0)

### `dashboard/` (Python-side guards)

| File | Covers |
|---|---|
| `tests/dashboard/test_no_raw_html.py` | Scans every `dashboard/src/**/*.ts(x)` for `dangerouslySetInnerHTML` / `innerHTML` / `insertAdjacentHTML`. React text nodes are the only sanctioned way to render JSONL-derived strings. Replaces the ECharts formatter guard from the Astro era. |
| `tests/server/test_spa_fallback.py` | Deep links (`/sessions/<id>`, `/alerts`, legacy `/session?id=`) serve `index.html`; `/api/*` and asset misses stay 404; no fallback without a dashboard dir. |

### `dashboard/` (JavaScript — run in `dashboard/`)

- `npm test` — Vitest + Testing Library + jsdom, files co-located as `*.test.ts(x)`:
  `lib/format` (formatter tiers), `lib/analysis` (windows, deltas, rollups,
  series, composition, weekday x hour), `lib/api` (Zod schemas against recorded
  fixtures, window-name mapping), `components/ui` (Tile delta rules, Pill
  icon+label invariant, Seg), `components/charts` (Bars scaling + error segment,
  Meter, CalendarHeatmap, AreaLine legend rule, Minimap error flag + click),
  `features/*` (overview deltas, session filters, timeline model, tool calls per
  day, snippet cleaning, Ctrl+K palette), `app/shell` (sidebar groups + badge).
- `npm run e2e` — Playwright smoke (`dashboard/e2e/smoke.spec.ts`): every route
  renders its crumb with no page errors against an empty data dir; deep link
  survives refresh; legacy URLs redirect; Ctrl+K opens. Serves the tracked
  `dashboard-dist/`, so refresh it first. One-time `npx playwright install chromium`.

### Top-level

#### `src/e2e.test.ts` — end-to-end smoke (1 test)

- writes a synthetic JSONL → runs first-pass ingest → starts the real
  HTTP server on port 0 → `fetch('/api/sessions')` returns the session
  with positive cost → `/api/overview` reflects it

## Regression tests by bug

Quick index of the tests that exist specifically to prevent a regression.
These are the highest-value ports during the Python rewrite — if any
break, the Python version has reintroduced a real bug that shipped in TS.

| Bug | Tests guarding it |
|---|---|
| Windowed views undercounted sessions started before the window | `api.test.ts` "includes turns from sessions that started before the window", "top_sessions rows show window-only totals", "distributes a multi-day session across actual turn dates" |
| Sub-agent files counted twice (as both their own session and as part of parent rollup) | `pipeline.test.ts` "sub-agent files are NOT counted twice" |
| Incremental re-ingest stomped on prior session totals instead of merging | `pipeline.test.ts` "incremental re-ingest does NOT reduce session totals" |
| Transcript segments written even when the user had opt-out flag set | `pipeline.test.ts` "skips writing transcript segments when search indexing flag is OFF" |
| FTS5 syntax errors on user query crashed the API | (covered indirectly — the two-stage retry pattern in `api.ts`; no dedicated unit test today) |
| Migration on an old install defaulted indexing OFF and broke existing users | `repository.test.ts` "search indexing flag defaults ON when segments already exist" |

## Fixtures & helpers worth porting once, not per-file

| TS pattern | Python equivalent |
|---|---|
| `mkAssistant(msgId, seq, content)` factory (4 files use a copy) | one `tests/_factories.py::assistant_line(...)` |
| `mkUserToolResult(...)` factory | `tests/_factories.py::user_tool_result(...)` |
| `SESSION(id, ts, agent?)` factory (api/repository tests) | `tests/_factories.py::session(...)` |
| `TURN(id, sessionId, ts, opts?)` factory | `tests/_factories.py::turn(...)` |
| `mkdtempSync('argus-')` + `openDb` + `Repository` boilerplate | `conftest.py::repo` fixture |

Centralize these once at the Python rewrite — the TS code's per-file
duplication is mild tech debt that's cheap to clean up at the rewrite
boundary.

## Notes for the Python port

- **Time-sensitive tests** (`api.test.ts` overview / trends) compute
  `Date.now() - 86_400_000` math inline. Replace with `freezegun.freeze_time`
  so the cutoffs are deterministic across machines. The Python tests
  should not introduce wall-clock flakiness.
- **FTS5 availability** — the Python build of `sqlite3` must include
  FTS5. Add one test that asserts FTS5 is present (`PRAGMA compile_options`
  contains `ENABLE_FTS5`) so a missing dependency fails loudly at the
  test boundary, not deep in `searchPrompts` with a confusing
  `sqlite3.OperationalError`.
- **Path normalization** (`normalizeProjectPath`) has a Windows-only
  branch that lowercases the path. Tests must run on Windows runners
  to exercise that branch — the CI matrix should include
  `windows-latest`.
- **Watcher debounce timing** — `watcher.test.ts` waits 200 ms then 400 ms
  for chokidar's `awaitWriteFinish` to fire. Python's `watchdog` doesn't
  have the same primitive; the equivalent test will exercise our own
  `defaultdict[Path, Timer]` debouncer. Same wait windows should still
  apply.
- **HTTP test client** — `app.request('/api/...')` in hono is a synchronous-
  feeling call against the in-process app. Use Starlette's `TestClient`
  for the same property in pytest; do NOT spin up a real port for unit
  tests (only the e2e test should bind a port).
