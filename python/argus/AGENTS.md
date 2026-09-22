# AGENTS.md — Python backend package (`argus`)

Parent: repo-root `AGENTS.md`. Read it first for the workflow and global rules.

## Purpose

The `argus` package: ingest Claude Code transcripts, store them in SQLite, serve
the dashboard, and provide the `argus` CLI. Runs directly from source
(`uv run argus start`) — no build step. Entry point: `argus = "argus.cli:app"`.

## Ownership

This doc owns backend-wide rules and the subsystems that have no child doc:
- `core/` — runtime wiring (`RuntimeOptions`, building the watcher + server).
  **`CoreRuntime.stop()` never closes the DB under a live writer.** It asks
  first-run's background phase (`FirstRunHandle.request_stop`) and the search
  backfill (`request_search_backfill_stop`) to stop after their current file,
  joins both by thread name (`join_first_run_threads`,
  `join_search_backfill_threads`, `writer_join_timeout` each), and if either is
  still alive it logs and **skips `close()`** — process exit reclaims the handle.
  A new background thread that writes to the connection must get the same
  stop-event + name-based join and be added here and to the `db` test fixture.
- `daemon/` — `argusd` background service: pidfile, process lifecycle, logging.
- `detectors/` — alert detectors (registry + individual rules like tool-error-rate spike).
- `pricing/` — pricing table load / refresh / compute; bundled JSON under repo `pricing/`.
- `scaffold/` — `argus claude` scaffolding (templates, snapshot, storage).
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
