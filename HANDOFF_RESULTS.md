# HANDOFF_RESULTS — HIGH-severity fixes (H1–H8 + Codex)

Run: 2026-09-22, autonomous, inline (no subagent-driven implementation). Every item
was done test-first (failing test seen first), `uv run pytest` green on each branch
tip, DOX pass done, and a `code-review` pass run on each diff with real findings
fixed in follow-up commits.

## Publishing

All branches are pushed to `origin`, PRs #27–#35 are open (none merged), and each fixes a tracking issue #36–#44 (labels `bug`, `severity: high`; PRs into `main` auto-close theirs, #44 must be closed by hand when #35 lands). The
first run had no GitHub credentials; branches were pushed and PRs opened once the
maintainer logged in. Stacked PRs (#29 → #30 → #31 → #34) target `main` and begin
with "Depends on #n"; #35 targets `feat/codex-adapter`.

Commits are authored as `KrishBhimani <126689784+KrishBhimani@users.noreply.github.com>`
(set in this clone's **repo-local** git config; no identity was configured) with **no**
AI co-author trailer, per the maintainer's rule.

## Results

| Item | Issue | Branch | Base | PR | Status | Summary |
|---|---|---|---|---|---|---|
| H1 incremental ingest | [#36](https://github.com/KrishBhimani/argus-code/issues/36) | `fix/incremental-ingest` | main | [#27](https://github.com/KrishBhimani/argus-code/pull/27) | opened | Chunked ingest == one-pass: byte-offset sequences, split-message merge (idempotent), errors applied by id, stored project/start kept; repairs `repair_session_duration_v1` + `backfill_tool_errors_v1` |
| H3 pricing | [#37](https://github.com/KrishBhimani/argus-code/issues/37) | `fix/pricing-current-models` | main | [#28](https://github.com/KrishBhimani/argus-code/pull/28) | opened | Bundled `2026-09-22.json` adds opus-5 / opus-5-5 / sonnet-5 / fable-5-1 (Anthropic prices); refresh writes `<data-dir>/pricing`, newest table wins; unknown model logged once |
| H2 fork double count | [#38](https://github.com/KrishBhimani/argus-code/issues/38) | `fix/fork-double-count` | H1 | [#29](https://github.com/KrishBhimani/argus-code/pull/29) | opened | Copied parent turns dropped when the origin stores them; order-independent reclaim; `MIGRATION_007` index; `repair_fork_duplicates_v1` (targeted delete of verified duplicate derived rows) |
| H4 transactions | [#39](https://github.com/KrishBhimani/argus-code/issues/39) | `fix/store-transactions` | H2 | [#30](https://github.com/KrishBhimani/argus-code/pull/30) | opened | `Repository.transaction()` (BEGIN IMMEDIATE/SAVEPOINT, per-connection RLock); every write locked; ingest rows + offset atomic; 3,000 calls 2.18 s → 0.012 s |
| H5 shutdown | [#40](https://github.com/KrishBhimani/argus-code/issues/40) | `fix/shutdown-join-writers` | H4 | [#31](https://github.com/KrishBhimani/argus-code/pull/31) | opened | Stop events + name-based joins for first-run and search backfill; skip `close()` if a writer is still alive; one-worker backfill claim; fixed a latent lock deadlock |
| H6 pidfile identity | [#41](https://github.com/KrishBhimani/argus-code/issues/41) | `fix/pidfile-identity` | main | [#32](https://github.com/KrishBhimani/argus-code/pull/32) | opened | PID + start time; VERIFIED/STALE/UNVERIFIED; only verified argusd is ever killed; legacy bare-PID files handled |
| H7 template secrets | [#42](https://github.com/KrishBhimani/argus-code/issues/42) | `fix/template-secrets` | main | [#33](https://github.com/KrishBhimani/argus-code/pull/33) | opened | One link-safe walk for copy + "left out" report; nested secrets/`*.local.json`/symlinks/junctions skipped; more credential names |
| H8 day buckets | [#43](https://github.com/KrishBhimani/argus-code/issues/43) | `fix/dashboard-day-buckets` | H5 | [#34](https://github.com/KrishBhimani/argus-code/pull/34) | opened | Server buckets days in the viewer's `tz`; `/api/overview.prior_window`; dashboard uses both; Tile "new"; rebuilt `dashboard-dist` |
| Codex H1 | [#44](https://github.com/KrishBhimani/argus-code/issues/44) | `fix/codex-incremental-ingest` | `feat/codex-adapter` | [#35](https://github.com/KrishBhimani/argus-code/pull/35) | opened | Byte-offset sequences, errors by id across holdback, child links survive a second refresh |

No item was skipped or draft-blocked.

## Test results (each branch tip, Linux, Python 3.12)

| Branch | `uv run pytest` |
|---|---|
| main (baseline) | 501 passed, 5 skipped |
| fix/incremental-ingest | 515 passed, 5 skipped |
| fix/pricing-current-models | 509 passed, 5 skipped |
| fix/fork-double-count | 522 passed, 5 skipped |
| fix/store-transactions | 527 passed, 5 skipped |
| fix/shutdown-join-writers | 533 passed, 5 skipped |
| fix/pidfile-identity | 516 passed, 5 skipped |
| fix/template-secrets | 527 passed, 5 skipped |
| fix/dashboard-day-buckets | 540 passed, 5 skipped; dashboard `npm ci && npm test` 60 passed, `build` OK, `size` 218.4 KB gz / 350 |
| feat/codex-adapter (baseline) | 568 passed, 7 skipped |
| fix/codex-incremental-ingest | 573 passed, 7 skipped |
| trial merge of H8-chain + H3 + H6 + H7 onto main (discarded) | 589 passed, 5 skipped, **no conflicts** |

**Pre-existing failures:** none. Skips are the documented platform/opt-in ones
(real `~/.claude` tests, Windows-only junction/terminate paths). No flakes seen.

**Stale test expectations changed (each explained in its PR):** `test_db.py`
hardcoded `schema_version == 6` → `SCHEMA_VERSION` (H2); daemon stop/collision tests
faked only `is_running` → fake the identity check (H6); `Tile.test.tsx` asserted
the raw `+3` delta the handoff calls a bug → "new" (H8); Codex
`test_extract_tool_calls` pinned positional `turn_index` → `== turn.sequence`.

## Left unverified

- **Windows code paths never ran on Windows:** H6 `GetProcessTimes` /
  `QueryFullProcessImageNameW` / `ctypes.wintypes` (unit-tested with a fake
  `kernel32`); H7 NTFS junctions (reparse-point branch simulated); H5's original
  access-violation crash (Linux tests assert ordering/joins, not the crash).
- **Pricing (H3):** input/output for all four models and cache reads for opus-5-5
  ($0.20) and fable-5-1 ($0.25) are Anthropic's published rates (via the claude-api
  reference); opus-5-5 cache writes ($5/$8) are documented as "derived — confirm at
  launch"; sonnet-5 cache values are the standard 1.25×/2×/0.1× multipliers. The
  non-0.1× cache reads are intentional — a code reviewer flagged them as typos; they
  are not.
- **Real data:** no access to `~/.argus` / `~/.claude` / `~/.codex`. Fork detection
  (H2) was built from the handoff's description of forked transcripts, not a real
  one. The **Codex write order** (function_call → token_count → output) is inferred;
  the fix is order-agnostic.
- `npm run e2e` (Playwright) not run (pre-release step; needs a Chromium download).
- First start after upgrading re-reads every on-disk session once (bounded 200/run,
  `backfill_tool_errors_v1`) — expect a few slower starts on big archives.

## Suggested merge order & dependencies

1. **H1** `fix/incremental-ingest` → main.
2. **H3** `fix/pricing-current-models` → main (independent; any time).
3. **H2** `fix/fork-double-count` (stacked on H1) → main after H1.
4. **H4** `fix/store-transactions` (on H2) → main after H2.
5. **H5** `fix/shutdown-join-writers` (on H4) → main after H4.
6. **H6** `fix/pidfile-identity` → main (independent).
7. **H7** `fix/template-secrets` → main (independent).
8. **H8** `fix/dashboard-day-buckets` (on H5) → main after H5.
9. **Codex** `fix/codex-incremental-ingest` → `feat/codex-adapter` (#25).

With squash-merges, each stacked branch must be rebased onto the new `main` after
its parent lands (`git rebase --onto main <old-parent-tip> <branch>`); the stacked
commits themselves apply cleanly.

**PR #25 rebase warning — migration number clash:** H2 adds `MIGRATION_007`
(`idx_turns_message`) and #25 also adds a `MIGRATION_007`
(`prompts.session_id` + `idx_prompts_session`). Whichever lands second must be
renumbered to **8** (both are idempotent; #25's artifact-verification self-heal must
cover both artifacts). Other expected #25 conflicts: `collector/pipeline.py`,
`collector/first_run.py`, `collector/search_backfill.py`, `store/repository.py`,
`store/db.py`, `store/AGENTS.md`, `tests/store/*`, and all of `dashboard-dist/`
(rebuild it after resolving). The Codex fix's `tool_error_ids` / `mark_tool_calls_errored`
/ `is_error = MAX(...)` code is textually identical to H1's to ease that merge.

## Known medium/low issues touched along the way

- M5 (flags set before work) — fixed for the re-read sweeps (H1) and the
  agent-type backfill (H5); `repair_fork_duplicates_v1` sets its flag after its pass.
- New: the search-backfill "already running" path deadlocked on a non-reentrant lock
  (fixed in H5).
- Not addressed (still open): M1–M4, M6–M10 and the low list, as instructed.

---

# Appendix — PR bodies


<!-- ============================ h1 ============================ -->

## fix(ingest): make incremental ingest equal to a one-pass read (H1)

### Root cause
The watcher ingests a live transcript in many small ticks (100 ms debounce,
often one line at a time). The Claude Code adapter computed every *file-wide*
value from the *current tick's* lines:

| Sub-issue | Symptom |
|---|---|
| H1a start/duration | `started_at` came from the tick's first assistant line → `duration_sec`/`started_at_ms` overwritten ("3 s" for a 29-day session); `started_at` itself is insert-only, so rows were internally inconsistent |
| H1b `is_error` | a `tool_result` in a later tick had no matching `tool_use` in that tick → flag discarded, call stored as success forever |
| H1c order | `sequence` / `turn_index` restarted at 0 per tick → scrambled timeline |
| H1d split message | a message's lines split across ticks → tail overwrote head (`tool_calls_count` 1 vs 2, later `timestamp`) |
| H1e project | `project_path` followed the latest `cwd` (a mid-session `cd` moved the session) |

Failing invariant test before the fix (one-pass vs line-by-line ingest of the same file):
```
session duration_sec one-pass= 86399 line-by-line= 0
session started_at_ms one-pass= 1777629601000 line-by-line= 1777716000000
session project_path one-pass= /proj line-by-line= /proj/sub
turn m1 {'timestamp': ('2026-05-01T10:00:01.000Z', '2026-05-01T10:00:03.000Z'), 'tool_calls_count': (2, 1)}
turn m2 {'sequence': (1, 0)}
turn m3 {'sequence': (2, 0)}
call t2 {'is_error': (1, 0)}
call t3 {'is_error': (1, 0), 'turn_index': (1, 0)}
FAILED test_line_by_line_equals_one_pass / test_byte_chunks_equal_one_pass[7|64|333]
```

### Fix
- **Sequence = byte offset of the message's first line** (file-wide monotonic);
  a call's `turn_index` = its message's sequence. The dashboard already renumbers
  turns 1..n by timestamp, so large values never reach the UI.
- **Split-message merge** in `pipeline._apply_result`: a stored turn with the same
  id and a smaller sequence, on a tick that didn't start at offset 0, is the head
  of a message whose tail this tick holds → keep head sequence/timestamp/input/
  cache, `max` output, add only tail calls not already stored (idempotent if the
  same tail is re-read after a lost offset write — found by code review). An
  offset-0 read overwrites, which is how pre-fix rows get rewritten.
- **Errors by id**: the adapter returns `tool_error_ids`; the pipeline applies
  them after the call upsert; `is_error` is monotonic (`MAX` on conflict).
- **Header**: `_header_for_recompute(..., authoritative=from_offset == 0)` keeps
  stored `project_path`/`started_at` on later ticks; `build_session` widens
  start/end with every stored turn. Project = first parsed line's `cwd`.

### Data repair
- `repair_session_duration_v1` (app_meta): in-DB recompute of `duration_sec`,
  `started_at_ms`, `ended_at_ms` from the stored ISO strings. Writer path only
  (`run_first_pass_ingest`); idempotent (second run changes 0 rows).
- `backfill_tool_errors_v1` (app_meta): re-read every top-level session still on
  disk once (deep_reset), same shape as the streamed-output sweep. The re-read
  also rewrites sequences and projects. **Sweep flags now flip only after the
  work ran and nothing un-attempted remains** (fixes the "flag set before work"
  part of M5 for these sweeps; the 200 cap is now `BACKFILL_CAP`).
- Sessions whose transcript Claude Code already deleted keep their old values.

### Tests added
- `tests/collector/test_incremental_invariant.py`: one-pass vs line-by-line vs
  7/64/333-byte chunks; offset-0 re-read stable; pre-fix rows rewritten; zero-turn
  tick keeps project/start; sub-agent error in a later tick; tail re-read after a
  lost offset write.
- `tests/collector/test_incremental_repairs.py`: duration repair (idempotent),
  tool-error sweep, flag waits for a capped sweep to finish.
- `tests/store/test_repository.py`: `is_error` never cleared by re-upsert.

`uv run pytest`: 515 passed, 5 skipped (platform skips only).

### Not verified
- Against the maintainer's real archive (no access to `~/.argus` here). The
  first start after upgrading re-reads every on-disk session once (bounded to
  200/run) — expect a slower first few starts on large archives.
- Codex adapter equivalents are on `feat/codex-adapter` (separate PR).

### Conflicts
Expect rebase conflicts with PR #25 (`feat/codex-adapter`) in
`collector/pipeline.py`, `collector/first_run.py`, `store/repository.py`.

### Concept: idempotency + "partial view vs whole truth"
Each tick sees a *slice*; anything that describes the *whole file* must either be
derived from something slice-independent (byte offsets) or merged with what's
already stored. The merge had to be **idempotent** — re-applying the same slice
must not change the result — because the offset write and the row writes aren't
atomic yet (H4). Trade-off: merging in Python costs one extra lookup per turn on
non-zero-offset ticks, in exchange for correctness without a schema change.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

<!-- ============================ h3 ============================ -->

## fix(pricing): price current Claude models; refresh into the data dir (H3)

### Root cause
1. The bundled tables (`pricing/2026-05-02.json`, `2026-06-12.json`) lack
   `claude-opus-5`, `claude-opus-5-5`, `claude-sonnet-5`, `claude-fable-5-1`;
   `compute_turn_cost` silently returns `0.0` for unknown models (645 `claude-opus-5`
   turns at $0 on the maintainer's archive).
2. `argus pricing refresh` wrote to `resources.files("argus")/"pricing"` — always the
   Python subpackage dir, so the "repo-root fallback" was dead code and refreshes
   landed in site-packages (wiped by upgrades; `PermissionError` on read-only installs).

Failing tests before the fix:
```
FAILED tests/pricing/test_load.py::test_bundled_table_prices_current_claude_models
FAILED tests/pricing/test_load.py::test_user_dir_table_wins_when_newer - TypeError
FAILED tests/pricing/test_compute.py::test_unknown_model_is_logged_once
FAILED tests/cli/test_pricing_refresh_cli.py::test_refresh_writes_to_data_dir_and_takes_effect
FAILED tests/cli/test_pricing_refresh_cli.py::test_refresh_reports_unwritable_data_dir
... 8 failed
```

### Fix
- New bundled table `pricing/2026-09-22.json` = 2026-06-12 + the four models.
- `pricing refresh --data-dir` writes to `<data-dir>/pricing/` (clean error on OSError).
- `load_pricing_table(user_dir=...)` picks the newest version across user + bundled
  dirs (an old refresh can't shadow a newer bundled table). `CoreRuntime` passes
  `<data_dir>/pricing`; `user_dir` is opt-in so tests never read `~/.argus`.
- `compute_turn_cost` logs an unpriced model once per process.
- Existing zero-cost backfill (`sessions_with_unpriced_turns`) re-prices old
  `claude-opus-5` turns — covered by a new test that ingests under the 2026-06-12
  table and backfills under the new one.

### Prices (per MTok) — source: Anthropic's published model pricing (claude-api reference, cached 2026-06-24)
| model | input | output | cw 5m | cw 1h | cache read |
|---|---|---|---|---|---|
| claude-opus-5 | 5 | 25 | 6.25 | 10 | 0.50 |
| claude-opus-5-5 | 4 | 20 | 5 | 8 | 0.20 |
| claude-sonnet-5 | 2 | 10 | 2.5 | 4 | 0.20 |
| claude-fable-5-1 | 10 | 50 | 12.5 | 20 | 0.25 |

**Not a typo:** fable-5-1's cache read is documented as $0.25 (0.025× input, a
quarter of fable-5's) and opus-5-5's as $0.20 (0.05× input). A reviewer flagged
both as off-ratio; they are intentionally not 0.1×.
**Derived, please confirm:** opus-5-5 cache writes ($5/$8) are documented as
"derived from the standard 1.25x / 2x multipliers — confirm at launch"; sonnet-5's
cache write/read values are the standard multipliers (1.25×/2×/0.1×) applied to
its documented $2 input. These match the LiteLLM refresh the maintainer ran.

### Tests added
`tests/pricing/test_load.py` (current models, newest-across-dirs, fallback),
`tests/pricing/test_compute.py` (log once), `tests/cli/test_pricing_refresh_cli.py`
(writes to data dir, nothing into the package, clean error),
`tests/collector/test_first_run.py` (opus-5 re-price after table upgrade).
`uv run pytest`: 509 passed, 5 skipped.

### Not verified
- The dashboard/API still shows $0 for an unknown model; only the log signals it
  (handoff minimum). A per-model "unpriced" badge would need an API field.
- Existing refreshed tables left inside an installed package by the old code are
  still read as the "bundled" dir until the package is upgraded (harmless: newest wins).

### Concept: separating code from mutable data
Package directories are owned by the installer — replaced on upgrade, sometimes
read-only. Mutable user state belongs in a user data dir. Trade-off: two places to
look, resolved by a deterministic rule (newest ISO-date version wins).

🤖 Generated with [Claude Code](https://claude.com/claude-code)

<!-- ============================ h2 ============================ -->

**Depends on the H1 PR (`fix/incremental-ingest`) — merge that first.** This branch is stacked on it (it relies on byte-offset turn sequences).

## fix(ingest): stop forked sessions double-counting copied parent turns (H2)

### Root cause
A forked transcript (`sessionKind: "bg"`) begins with verbatim copies of its
parent's lines — same line `uuid`, same `message.id`, `sessionId` rewritten, the
parent id kept in `session_id`. Turns are keyed `{session_id}:{message.id}`
(`aggregate.build_turn`), so every copy became a new turn of the fork (real case:
~24.8M cache-read tokens, 110k output tokens, 110 tool calls re-counted; the fork
inherited the parent's `started_at`).

No real forked transcript was available in this environment; the fixture is built
from the handoff's description (copied lines with `sessionId=<fork>`,
`session_id=<parent>`, `sessionKind: "bg"`).

Failing before the fix:
```
AssertionError: assert (['f1', 'm1', 'm2'], ['t1', 't9']) == (['f1'], ['t9'])
FAILED test_fork_ingested_after_parent_skips_copied_turns
FAILED test_fork_ingested_before_parent_is_reclaimed
FAILED test_repair_removes_existing_fork_duplicates_once
```

### Fix (detection = "same message id already stored under a different session")
- Adapter: `copied_from(line)` records the line's claimed origin in
  `turn.metadata.origin_session_id` (only when `session_id` ≠ `sessionId`).
- Pipeline `_drop_fork_copies`: a tagged turn is dropped with its tool calls only
  if the claimed origin **already stores the same message**. The claim alone
  never drops a turn (real non-copied lines can differ on `session_id`); the fork's
  start is recomputed from kept turns.
- Order independence, `_reclaim_fork_copies`: if the fork was read first, the
  origin reclaims the tagged copies when ingested (calls matched by turn sequence,
  so copies whose tool_use lines the origin read in earlier ticks go too), and the
  fork is recomputed via `recompute_stored_session` (rewrites `started_at`).
- `MIGRATION_007`: expression index `idx_turns_message` on the message-id suffix
  of `turns.id` (idempotent `CREATE INDEX IF NOT EXISTS`).

### Data repair — deletes derived rows (explicitly)
`repair_fork_duplicates_v1` (background phase, app_meta flag): for each top-level
session sharing a message id with another, parse (not ingest) its transcript;
turns whose line claims an origin that stores the same message are verified
copies → delete **only those turns and their tool calls** from the fork, then
recompute the fork. The origin keeps its rows; the fork's session row is kept.
These are duplicated *derived* rows, re-countable from the origin — not user data
loss. Documented as the one sanctioned delete in `store/AGENTS.md`. Forks whose
file Claude Code already deleted are left as-is (nothing to verify against).
Idempotent (second run finds no verified copies).

### Tests
`tests/collector/test_fork_dedup.py` (6): parent→fork, fork→parent, claim-only line
kept, multi-tick reclaim, copies-only fork start, repair + idempotency.
`tests/store/test_db.py`: MIGRATION_007 index exists and is used by the query plan.
**Changed stale expectations:** two `test_db.py` asserts hardcoded
`schema_version == 6`; the contract is "latest version", so they now compare to
`SCHEMA_VERSION` (7).
`uv run pytest`: 522 passed, 5 skipped.

### Not verified / not done
- Against a real forked transcript (none available here).
- Search segments of copied lines are not de-duplicated (search may show a copied
  message under both sessions).
- Sub-agent files are excluded from fork handling.

### Conflicts
PR #25 (`feat/codex-adapter`) touches `collector/pipeline.py`, `first_run.py`,
`store/repository.py` and `store/db.py` (migration numbering: if #25 adds a
migration 7, renumber one of them).

### Concept: identity vs. key
A row's *key* (`{session}:{message}`) isn't the message's *identity* — the same
message can appear in two files. De-duplication needs an ownership rule; here
"the origin the line itself names, verified by the origin actually holding it".
Trade-off: an extra indexed lookup on ticks that carry origin claims, plus a
reclaim step to make the result independent of ingest order.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

<!-- ============================ h4 ============================ -->

**Depends on the H2 PR (`fix/fork-double-count`, itself on H1) — merge those first.** Stacked because it wraps the same pipeline/repository code.

## fix(store): make write batches and each file ingest atomic (H4)

### Why after H1–H3 (order choice)
The handoff left the order open. I did transactions after the correctness fixes
so each earlier PR stays reviewable on its own and this one can wrap the final
shape of `pipeline.ingest_file`. H1's split-message merge was already made
idempotent (it counts only tail calls not yet stored) so it doesn't *depend* on
this PR; this PR closes the remaining crash window (rows without their offset).

### Root cause
`store/db.py` opens SQLite with `isolation_level=None` (autocommit). In that mode
Python's `Connection.__enter__` never issues `BEGIN`, so `with self.db:
executemany(...)` committed **every row**, and `__exit__`'s commit ended any
transaction a caller had opened. A stray `self.db.commit()` in `set_app_meta` did
the same.

Failing before the fix:
```
FAILED test_failing_batch_leaves_no_rows            assert 2 == 0   (2 of 4 rows persisted)
FAILED test_batch_inside_caller_transaction_is_not_committed_early
FAILED test_nested_failure_rolls_back_only_the_inner_block
FAILED test_another_threads_write_is_not_swept_into_a_rollback
FAILED test_ingest_rows_and_offset_are_all_or_nothing
upsert_tool_calls x3000: 2.183s
```
After: `upsert_tool_calls x3000: 0.012s` (~180x).

### Fix
- `Repository.transaction()`: outermost `BEGIN IMMEDIATE` / `COMMIT` / `ROLLBACK`;
  nested calls join via `SAVEPOINT` (inner failure undoes only the inner block).
- Per-connection `threading.RLock` + depth, carried by an `ArgusConnection`
  (`sqlite3.Connection` subclass passed as `factory=` in `open_db`; plain
  connections get a per-Repository fallback — `sqlite3.Connection` can't hold
  attributes or weakrefs).
- **Every write method** is decorated `@_writes` (not just the four batches), so a
  single-statement write from another thread waits for the lock instead of
  silently joining — and being rolled back with — another thread's transaction.
- `pipeline.ingest_file` runs in one transaction: a tick's rows (session, turns,
  calls, segments, sub-agents) and its file offset commit together.

### Locking decision (documented in `store/AGENTS.md`)
Writes take the RLock inside `transaction()`; **reads don't**. Readers on the shared
connection proceed during an ingest and can see its uncommitted rows for its
duration (one file tick). Another *process* writing the same DB (e.g. an `argus` CLI
command while argusd ingests) waits up to `busy_timeout` (5 s) per transaction.

### Tests
`tests/store/test_transactions.py` (5, listed above). Full suite: 527 passed, 5 skipped.
Code review of the diff: no findings.

### Not verified
- Behaviour under a long first-run chunk (up to 64 MiB per tick) with a concurrent
  CLI writer from another process: that writer could hit `database is locked`
  after 5 s. No such concurrent writer exists in normal use (the daemon owns writes).

### Concept: atomicity (the A in ACID)
A unit of work either happens completely or not at all. In autocommit mode each
statement is its own unit, so a "batch" was N units. `BEGIN … COMMIT` makes the
batch — and the pair (rows, offset) — one unit; as a bonus SQLite syncs the WAL once
instead of N times, hence the speed-up. Trade-off: writers serialise on one lock.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

<!-- ============================ h5 ============================ -->

**Depends on the H4 PR (`fix/store-transactions`, stacked on H2 → H1) — merge those first.** Stacked because it edits `collector/first_run.py`, which H1/H2 also change.

## fix(runtime): never close SQLite under a live writer thread on shutdown (H5)

### Root cause
`core/runtime.py::CoreRuntime.stop()` joined first-run with `join(timeout=10)`,
ignored the result, never joined the `argus-search-backfill` thread, then called
`self._db.close()`. Closing a sqlite3 connection another thread is using is
undefined behaviour — reproduced on the maintainer's machine as an access violation
(exit 139) in `upsert_turn ← ingest_file ← search_backfill`. Trigger: enable indexing
from the dashboard (re-ingest of up to 1,000 sessions) and press Ctrl+C.

Before the fix (old runtime, a backfill thread mid-write for 0.3 s):
```
['db.close', 'backfill done']      # connection closed under the writer
```

### Fix
- `search_backfill`: module stop event + `request_search_backfill_stop()`, checked
  between sessions; `SEARCH_BACKFILL_THREAD_NAME` + `join_search_backfill_threads()`
  (name-based, like `join_first_run_threads`).
- `first_run`: `FirstRunHandle.request_stop()`; the background loop, the fork
  repair and `_backfill_missing_derived_data` / `_reread` (`should_stop=`) stop after
  the current file.
- `CoreRuntime.stop()`: signal both, join both (`writer_join_timeout`, 10 s each);
  if a writer is still alive, log and **skip `close()`** (process exit reclaims it).
- `db` test fixture joins the search backfill too.
- Related (low): `run_segment_backfill` checked `in_progress` under the lock but set
  it after discovery (two concurrent enable requests → two workers). Now check-and-
  set in one critical section. Also fixed: the "already running" early return
  called `get_search_backfill_status()` while holding the same non-reentrant lock —
  a second enable request during a backfill would **deadlock**.
- Review follow-up: `backfill_agent_subagent_type_v1` was marked done *before* its
  re-reads; with shutdown able to interrupt them, it is now set only after an
  uninterrupted run. (The re-read sweeps from H1 already flip after the work.)

### Tests
`tests/core/test_runtime_shutdown.py` (6): stop waits for the search backfill before
close; stop skips close while a writer is stuck; search backfill stops when asked;
concurrent enable requests start one worker; first-run background stops when asked;
interrupted backfill leaves one-shot flags unset.
`uv run pytest`: 533 passed, 5 skipped.

### Not verified
- Not reproduced on Windows (the original crash was a Windows access violation);
  the Linux tests assert ordering/joins rather than the crash itself.

### Concept: resource lifetime / ownership
Whoever closes a shared resource must first make sure nobody else is using it —
"join before close". When that can't be guaranteed in bounded time, leaking the
handle to process exit is safe (the OS reclaims it, SQLite's WAL stays consistent);
freeing it under a user is not. Trade-off: a stuck writer means an unclean-looking
exit (logged) instead of a crash.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

<!-- ============================ h6 ============================ -->

## fix(daemon): verify argusd's identity before trusting or killing a PID (H6)

### Root cause
`daemon/pidfile.py::is_running(pid)` only checked that *some* process had the PID.
Used by `stop_daemon` (SIGTERM / `TerminateProcess`), the `run_foreground` collision
guard, `argus start`'s read-only decision and `daemon status`. After an unclean argusd
exit (Windows reboot; the Windows stop itself is a hard `TerminateProcess`, so
`finally: pidfile.remove` never runs) the OS can reuse the PID → `argus daemon stop`
killed an unrelated process.

Failing test before the fix (legacy/plain-PID file pointing at an unrelated live process):
```
FAILED tests/daemon/test_pidfile_identity.py::test_legacy_plain_pid_of_unrelated_process_is_not_trusted
  assert unrelated_process.poll() is None   # the process had been killed
```

### Fix
- Pidfile is JSON `{"pid", "start"}`; `start` = process start time
  (`/proc/<pid>/stat` field 22 on Linux, `ps -o lstart=` elsewhere on POSIX,
  `GetProcessTimes` creation time on Windows via ctypes). No psutil.
- `pidfile.check(rec)` → **VERIFIED** (start time matches; or, when there's no start
  time to compare, the command line is `argus … daemon run` / `-m argus.cli daemon run`),
  **STALE** (dead, different start time, or visibly another program — incl. a
  non-python/argus image on Windows), **UNVERIFIED** (alive, identity unreadable).
- Only VERIFIED is ever signalled (`stop_daemon`). "Don't start a second writer"
  decisions (collision guard, `daemon start`, `argus start` read-only mode,
  `wait_for_pidfile`) use `running_pid`, which also counts UNVERIFIED and logs how
  to clear it; `daemon status` says "possibly running".
- Old bare-PID files are still read (backward compatible) and handled by the
  command-line fallback; never killed on PID alone.

Code review found two issues in the first version (an unreadable start token made
the daemon's own file look stale; on Windows an old daemon's bare-PID file was
treated as stale so `daemon start` could launch a second writer) — fixed by the
three-state check in the second commit.

### Tests
`tests/daemon/test_pidfile_identity.py` (15): start token recorded + self verified;
reused PID not ours; stop never kills a reused PID; legacy unrelated PID not trusted;
legacy real argusd (cmdline) still recognised; status/collision guard ignore reused
PIDs; token-less own file recognised; console-script cmdline; unverifiable PID blocks a
second daemon but is never killed; Windows: creation time, OpenProcess failure,
dispatch, image name — via a fake `kernel32`.
**Changed stale expectations:** `test_service.py::test_run_foreground_refuses_when_live_daemon_exists`
and the two `test_process.py` stop tests faked only `is_running`; under the new
contract (verify identity before acting) they fake `pidfile.check` instead.
`uv run pytest`: 516 passed, 5 skipped.

### Not verified
- **The Windows branch was not run on Windows** (`GetProcessTimes`,
  `QueryFullProcessImageNameW`, `ctypes.wintypes` usage) — only unit-tested with a
  fake `kernel32`. Please run `uv run pytest tests/daemon` on Windows before merging.
- macOS `ps -o lstart=` path not exercised (Linux uses `/proc`).
- Trade-off: an old argus's bare-PID file on Windows whose PID was reused by some
  python.exe stays UNVERIFIED (blocks `daemon start` with a message) until the user
  deletes it — preferred over risking two writers or killing the wrong process.

### Concept: PID reuse / identity vs. address
A PID is an *address* the OS recycles, not an *identity*. (PID, start time) is
unique for the machine's uptime, so it identifies the process. The same idea as a
TOCTOU guard: re-verify what you're about to act on, don't trust an old handle.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

<!-- ============================ h7 ============================ -->

## fix(scaffold): keep secrets and out-of-tree links out of template snapshots (H7)

### Root cause
`scaffold/snapshot.py`: the secret filter (`secret_files_in`, `_safe_top_files`)
applied only to **top-level** `.claude/` files. Chosen subfolders were copied whole
with `shutil.copytree(src, dest, ignore=_COPY_IGNORE)`, which follows symlinks /
junctions (and the subfolder itself may be a link). Reproduced on the maintainer's
machine: the snapshot contained `agents/credentials.json`, `hooks/.env`,
`hooks/settings.local.json`, `skills/id_rsa` (via a junction outside the project)
while the CLI said "secrets are never snapshotted". Templates are copied into every
scaffolded project.

Failing before the fix: 21 tests, e.g.
```
FAILED test_nested_secrets_are_not_snapshotted
FAILED test_nested_symlink_out_of_the_tree_is_not_followed
FAILED test_symlinked_subfolder_is_not_followed
FAILED test_cli_reports_nested_withheld_files
FAILED test_more_credential_files_are_recognised[id_rsa] ... [.pgpass]  (16)
```

### Fix
- `plan_snapshot(claude, include_subdirs)`: one walk (top-level files + chosen
  subfolders, every depth) that never follows a symlink, junction or reparse point
  (`Path.is_symlink`, `Path.is_junction` on 3.12+, `st_file_attributes &
  FILE_ATTRIBUTE_REPARSE_POINT` on 3.11/Windows) — including the chosen subfolder
  itself — and skips `is_secret_file` names, `*.local.json`, caches.
- `snapshot_template` copies file-by-file from that plan (`follow_symlinks=False`);
  no `copytree`.
- The CLI's "left out" message is built from the same walk
  (`secret_files_in(claude, included)`), so it's truthful.
- `is_secret_file` now also matches `id_rsa`/`id_dsa`/`id_ecdsa`/`id_ed25519`,
  `*.pem *.key *.p12 *.pfx *.jks *.keystore`, `token.json`, `auth.json`,
  `oauth_creds.json`, `service-account.json`, `.netrc`, `.npmrc`, `.pypirc`,
  `.git-credentials`, `.pgpass` (public keys like `id_rsa.pub` are not flagged).
- Review follow-up: a linked chosen subfolder was reported twice.

### Tests
`tests/scaffold/test_snapshot_secrets.py`: extended names (+ non-false-positives),
nested secrets, withheld report from the same walk, nested symlink (dir + file) out
of the tree, symlinked subfolder (reported once), CLI output, and a simulated
Windows reparse-point directory.
`uv run pytest`: 527 passed, 5 skipped.

### Not verified
- Real NTFS junctions were not exercised (Linux here); the reparse-point branch is
  covered by a test that simulates `st_file_attributes`. The existing Windows-only
  junction tests (`test_scaffold_containment.py`) still apply on Windows CI.

### Concept: fail-closed filtering + a single source of truth
Security filters should decide "copy only what's known safe" (allow-list per file)
rather than "copy a tree, then subtract" — and the report shown to the user must
come from the *same* decision, or it drifts into a lie. Trade-off: a user who
genuinely wants a `*.key` or a linked folder in a template must copy it by hand;
the CLI tells them exactly what was left out.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

<!-- ============================ h8 ============================ -->

**Depends on the H5 PR (`fix/shutdown-join-writers`, stacked on H4 → H2 → H1) — merge those first.** Stacked because it changes `store/repository.py`, which the earlier PRs also change.

## fix: day buckets in the viewer's timezone; server-side prior-window deltas (H8)

### Root cause
- The server bucketed days as `substr(timestamp,1,10)` — a **UTC** date
  (`repository.aggregate_turns_by_day`) — while the dashboard built **local** date
  keys (`windows.ts::dayKeys`, `OverviewPage`, `CalendarHeatmap`, `ModelsPage`) to index
  those maps. In IST between 00:00 and 05:30 "today" never matched: the chart plotted
  0 next to a Tokens tile of 91.6M.
- "vs prior window" deltas: the current value was a rolling window (`now - n days`,
  server); the prior value summed whole calendar days from a wider response, which
  overlapped the current window's first day. The sessions delta compared "sessions
  with turns in window" against "sessions started in window".
- `Tile` printed a raw difference ("+55.413754999999995") when the prior value was 0.

Failing before the fix: all 6 new server tests (e.g. `tokens_by_day == {"2026-09-22": 100}`
for a 20:40Z turn requested with `tz=330`; no `prior_window`), and the vitest cases
(`tzOffsetMin` missing, `tz` not sent, tile showing the raw float, model not using
`prior_window`).

### Fix (the handoff's pre-made decision: server-side `tz` + server-side `prior_window`)
- `/api/overview` and `/api/trends` take `tz` (minutes east of UTC, ±840; invalid → 400
  per the app's validation handler) and bucket with `date(timestamp, '+N minutes')`.
  `tz` is part of the `_ReadCache` key. Window cutoffs remain rolling UTC instants.
- `/api/overview.prior_window = {tokens, cost_usd, sessions}` over `[now - 2n, now - n)`
  with the same definitions as the window's own totals (`turn_totals_between`);
  `null` for `all`.
- Dashboard: sends `tz=${-getTimezoneOffset()}`, keys those queries on it, uses
  `prior_window` for Tokens/Cost/Sessions deltas (drops the wider-overview fetch and
  the overlapping `priorWindowTotal` / `priorWindowSessions` helpers), and `Tile` shows
  "new" when there's no prior value. Rebuilt `dashboard-dist` per `dashboard/AGENTS.md`.
- The tool error-rate delta keeps its existing `(wider − current)` prior (already
  non-overlapping by subtraction).

### Tests
- `tests/server/test_day_buckets.py` (6): overview/trends buckets per `tz`, `tz` in the
  cache key, out-of-range `tz` rejected, prior window = the equal range just before
  (no overlap; sessions defined like `session_count`), null for `all`.
- Vitest `src/lib/api/timezone.test.ts` pins `TZ=Asia/Kolkata` at 02:10 local
  (20:40Z): offset = 330, `tz=330` sent on overview + trends, local "today" key.
  `useOverviewModel.test.ts` rewritten for `prior_window`; `Tile.test.tsx`.
- **Changed stale expectation:** `Tile.test.tsx` asserted the raw `+3` for a null-pct
  delta — the behaviour this PR fixes; it now expects "new".
- `uv run pytest`: 540 passed, 5 skipped. `npm ci && npm test` (60 passed) `&& npm run
  build && npm run size` (218.4 KB gz / 350 KB).

### Not verified
- `npm run e2e` (Playwright) was not run — needs a Chromium download; it's a
  pre-release step per root AGENTS.md.
- Not exercised in a real browser in a non-UTC zone (unit tests pin TZ).

### Concept: one definition, one place
Two components disagreed on what a "day" is (UTC vs local) and on what the "prior
window" is (rolling vs calendar). The fix makes the server the single place that
defines both, parameterised by the viewer's offset. Trade-off: the cache now varies by
timezone (more keys), which is bounded (a viewer has one offset).

🤖 Generated with [Claude Code](https://claude.com/claude-code)

<!-- ============================ codex ============================ -->

**PR into `feat/codex-adapter` (#25), not `main`.** Branch cut from `origin/feat/codex-adapter`; its history is untouched.

## fix(codex): make incremental ingest chunk-invariant; keep child links (H1, Codex)

### Root cause
The Codex reviewer's chunked-vs-whole harness found turn ids, tokens, offsets and
segment ids stable; only these differed:
1. `extract_turns.py` kept `seq = 0` per call and `extract_tool_calls.py` used
   `turn_index=idx` within the tick → timeline order depended on chunking.
2. A `function_call_output` / `patch_apply_end` / `mcp_tool_call_end` landing
   **after** the tick's last `token_count` is held back into the next tick
   (`ingest_file._cut`), which no longer contains the call → error flag dropped.
3. `discover.py::ThreadIndex.refresh()` cleared `_children`, and a cached `_peek`
   returned before re-registering the parent→child link → after a second refresh
   `sub_session_files_for(parent)` returned `[]` for unchanged children, so child
   segments never backfilled.

Failing before the fix (one-pass vs line-by-line through the real pipeline):
```
turn :tc@1451 {'sequence': (1, 0)}
turn :tc@2176 {'sequence': (2, 0)}
call c1 {'is_error': (1, 0)}
call c2 {'turn_index': (1, 0)}
FAILED tests/collector/test_codex_incremental_invariant.py::test_line_by_line_equals_one_pass
FAILED ... test_byte_chunks_equal_one_pass[9] / [101]
FAILED tests/adapters/codex/test_discover.py::test_children_survive_a_second_refresh
```

### Fix
1. `sequence` = byte offset of the closing `token_count` (legacy: the assistant
   message) line — the same offset as the turn's `tc@`/`msg@` id; `turn_index` =
   the owning turn's `sequence`. I chose byte offsets over a running counter in
   `TickState` (the handoff's suggestion) to match the Claude Code fix on `main`
   and because it needs no state rebuild.
2. `errored_call_ids()` → `AdapterIngestResult.tool_error_ids`; the pipeline applies
   them by id after the call upsert (parent and sub-sessions); `upsert_tool_calls`
   keeps `is_error = MAX(old, new)`. **This is the same mechanism, with textually
   identical code in `adapters/base.py` and `store/repository.py`, as the Claude
   Code fix on `main` (`fix/incremental-ingest`)** so the eventual merge resolves.
3. `ThreadIndex._peek` registers the parent→child link on every call (cache hit or miss).

### Tests
- `tests/collector/test_codex_incremental_invariant.py`: one-pass == line-by-line ==
  9/101-byte chunks through the real pipeline (session, turns, tool calls).
- `tests/adapters/codex/test_discover.py::test_children_survive_a_second_refresh`.
- **Changed stale expectation:** `test_extract_tool_calls.py::test_calls_are_attributed_to_the_turn_that_follows`
  pinned positional `turn_index` `[0, 1, 1, 1]`; it now asserts `turn_index == turn.sequence`.
- `uv run pytest` on this branch: 573 passed, 7 skipped (baseline 568 passed, 7 skipped).
- Code review of the diff: no findings.

### Data repair
None added here: the Codex adapter is unreleased (#25 unmerged), so no user archive
holds Codex rows. If the maintainer has local Codex rows from testing #25, the
one-shot `backfill_tool_errors_v1` re-read from the `main` fix (once both land)
rewrites them; otherwise `argus wipe` of a test data dir is fine.

### Not verified
- **The Codex write order (function_call → token_count → output) is inferred, not
  observed** — no real rollouts with model responses were available. The fix is
  order-agnostic (errors are applied by id whenever they're read).
- Integration with the `main` fixes (H1 split-message merge, H2, H4 transactions)
  — expect conflicts in `collector/pipeline.py`, `collector/first_run.py`,
  `store/repository.py` when #25 is rebased onto `main`.

### Concept: stable identity across partial reads
A tick is a *window* onto a growing file. Anything derived must be a function of
the file, not of the window: byte offsets are; "position within this tick" isn't.
Cross-window facts (an error whose call is in an earlier window) must be joined by
a stable key (the call id) rather than by co-location.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
