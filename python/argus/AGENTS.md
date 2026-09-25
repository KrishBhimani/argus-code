# AGENTS.md — Python backend package (`argus`)

Parent: repo-root `AGENTS.md`. Read it first for the workflow and global rules.

## Purpose

The `argus` package: ingest Claude Code transcripts, store them in SQLite, serve
the dashboard, and provide the `argus` CLI. Runs directly from source
(`uv run argus start`) — no build step. Entry point: `argus = "argus.cli:app"`.

## Ownership

This doc owns backend-wide rules and the subsystems that have no child doc:
- `core/` — runtime wiring (`RuntimeOptions`, building the watcher + server).
  **`CoreRuntime.stop()` never closes the DB under a live thread.** It stops the
  scheduler and the watcher (their `stop(timeout)` returns whether the threads
  exited), asks first-run's background phase (`FirstRunHandle.request_stop`)
  and the search backfill (`request_search_backfill_stop`) to stop after their
  current file, joins both by thread name (`join_first_run_threads`,
  `join_search_backfill_threads`; `writer_join_timeout` each), and if **any**
  of the four is still alive it logs and **skips `close()`** — process exit
  reclaims the handle.
  A new background thread that writes to the connection must get the same
  stop-event + name-based join and be added here and to the `db` test fixture.
- `daemon/` — `argusd` background service: pidfile, process lifecycle, logging.
  **Never act on a bare PID.** The pidfile stores `{"pid", "start"}` (process
  start time: `/proc/<pid>/stat` field 22 / `ps -o lstart=` / Windows
  `GetProcessTimes`); every "is argusd running?" decision (stop, collision guard,
  `argus start` read-only mode, `daemon status`) goes through
  `pidfile.check()`: VERIFIED (same start time, or with no start time to
  compare, a command line that is `argus … daemon run`), STALE (dead, different
  start time, visibly another program) or UNVERIFIED (alive, identity
  unreadable — e.g. an old bare-PID file on Windows). **Only VERIFIED is ever
  signalled** (`live_pid`), and `stop_daemon` re-verifies before its
  after-timeout force-kill (the PID may have been reused while it waited).
  `argus daemon stop` on an UNVERIFIED PID says so and exits 1; it never
  reports "not running" for a process it declined to check.
  "Don't start a second writer" decisions (collision
  guard, `daemon start`, `argus start` read-only mode) use `running_pid`, which
  also counts UNVERIFIED and tells the user how to clear it. The Windows branch
  is unit-tested with a fake `kernel32`, not exercised on Windows in CI.
- `work/` — **trial** (opt-in `argus start --work`, `argus work scan|status`): local git
  history + transcript facts the pipeline skips, session↔commit links labelled
  `exact`/`coauthored`/`inferred`, and `/api/work/*`. Writes only
  `<data_dir>/work.db`; `argus.db` is reached solely through a read-only `ATTACH`
  (asserted by `tests/work/test_isolation.py`). Nothing imports it unless the flag is
  on. Removal = delete this package, `features/work/`, two routes, and `work.db`.
  A **project is a repo, not a folder**: `repos` has one row per folder, and folders
  sharing `project_key` (normalized `origin` URL → else first commit → else the path;
  credentials stripped) are one project. Queries resolve any folder id to the whole
  group and count each commit sha once, keeping the strongest link. Columns added
  after the first trial build are added in `open_work_db` only when missing.
  Time comes from one source per session, best first: `measured` (transcript
  `turn_duration`), `estimated` (transcript gaps), `archive` (argus.db turn gaps, for
  sessions whose transcript is gone; same 5-min cap). Such sessions are titled from
  the prompt nearest their start (`title_source = 'prompt'`, placeholders stripped);
  transcript titles always win. Tokens and cost always come from argus.db turns.
- `detectors/` — alert detectors: the registry, the shared helpers in `base.py`,
  and one module per rule (`tool_error_rate_spike`, `cost_spike`, `cache_hit_drop`).
- `pricing/` — pricing table load / refresh / compute; bundled JSON under repo `pricing/`.
  `argus pricing refresh` writes to `<data_dir>/pricing/` (never into the installed
  package — upgrades wipe it and it may be read-only); `load_pricing_table(user_dir=...)`
  picks the newest version across that dir and the bundled one. An unpriced model
  costs $0 and is logged once per process (placeholders like `<synthetic>` are
  not). Every start, `_reprice_stored_turns` (collector/first_run.py) re-prices
  $0 turns of now-listed models **from their stored token columns** and
  recomputes those sessions (sub-agents before parents). That's needed because
  their transcripts are often already deleted, so the re-read backfill can't
  reach them. New model prices come from
  Anthropic's published per-MTok rates — don't copy LiteLLM numbers unverified.
- `scaffold/` — `argus claude` scaffolding (templates, snapshot, storage).
  **`template create` copies only what `plan_snapshot()` lists**: one walk over
  top-level files and the chosen subfolders, at every depth, that never follows
  a symlink/junction/reparse point (including the subfolder itself) and skips
  `is_secret_file` names and `*.local.json`. The CLI's "left out" message is
  built from the same walk (`secret_files_in(claude, included)`) — don't
  reintroduce `shutil.copytree` or a second, diverging filter.
- `schema/` — shared pydantic types (`Session`, `Turn`, …). Changing a stored
  field ripples into `store/` (columns) and `server/` (serialization).

Delegated subtrees (see their own AGENTS.md): `store/`, `collector/`, `adapters/`, `server/`.

## Local Contracts

- **Version lives in two files** that must stay in lock-step: `__init__.py`
  (`__version__`) and repo-root `pyproject.toml`. Bump both on release (root doc).
- **CLI command groups** are Typer apps wired in `cli.py`. When renaming a
  user-facing command, keep the old name as a `hidden=True` alias and nudge to the
  new one (precedent: `search` → `indexing`). Update help strings, the daemon
  read-only API hint, and dashboard copy together.
- **A missing `~/.claude` is fatal for `argus start` but not for `argusd`.**
  `CoreRuntime.start(require_adapters=True)` (the foreground default) raises
  `NoAdaptersError` — immediate, readable feedback. The daemon
  (`daemon/service.py`) passes `require_adapters=False`, comes up idle with the
  DB open, and polls `CoreRuntime.try_activate()` every `adapter_poll_sec`
  (30 s) so ingest starts by itself once the directory appears (issue #11).
  `try_activate()` is idempotent and returns `True` only on the idle→running
  transition, so callers can log it once. Keep `_FakeRuntime` in
  `tests/daemon/test_service.py` signature-compatible with `CoreRuntime` — it
  asserts the daemon requests the tolerant mode.
- **Detectors are pure and self-registering.** A detector is a new module under
  `detectors/` with a `@register` class whose `detect(repo, now_iso)` only *reads*
  and returns `Finding`s, plus the side-effect import in `detectors/__init__.py`.
  The scheduler (`collector/scheduler.py`) is the only writer of alerts; it also
  resolves findings a detector stops returning, so a rule must keep its
  `dedup_key` stable across runs (tool name, project path) or an alert can never
  resolve. Thresholds live in module-level constants with the reasoning in the
  module docstring — the shipped rules all compare a trailing 7-day window
  against the 28 days before it, via `base.iso_at_offset`, and share the
  `warning` / `critical` severity split. New detector, new severity bands: say
  why in the docstring.
- **Privacy stays intact:** no network calls or telemetry beyond opt-in
  `argus pricing refresh`. Don't add outbound calls.

## Work Guidance

- Follow existing module patterns; keep files focused and small.
- Python ≥ 3.11. Dependencies are deliberately minimal (fastapi, uvicorn,
  pydantic, watchdog, typer, httpx) — don't add deps without cause.

## Verification

`uv run pytest` (full suite). Subsystem tests live under `tests/<subsystem>/`.

## Child DOX Index

- `store/AGENTS.md` — SQLite, migrations, repository; data-safety + path normalization.
- `collector/AGENTS.md` — ingest pipeline and missing-data backfill.
- `adapters/AGENTS.md` — Claude Code adapter and transcript-segment extraction.
- `server/AGENTS.md` — FastAPI routes, static serving, clean shutdown.
