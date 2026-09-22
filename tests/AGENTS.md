# AGENTS.md — tests

Parent: repo-root `AGENTS.md`. See `TESTING.md` for the full per-file catalog;
this doc is the binding conventions.

## Purpose

`pytest` suite mirroring the package layout (`tests/<subsystem>/...`). Run with
`uv run pytest` (config in `pyproject.toml`: `pythonpath=["python"]`,
`testpaths=["tests"]`).

## Local Contracts

- **TDD.** For a bug, write the failing reproduction first and confirm it fails
  for the right reason before fixing. For a feature, write the test alongside it.
- **Every test directory is a package** — add an `__init__.py` when creating a new
  `tests/<x>/` folder (existing dirs all have one).
- **Real on-disk DB, per-test `tmp_path`.** The `repo` fixture (in `conftest.py`)
  uses a real file, not `:memory:`. Don't share state across tests.
- **Synthetic-line factories are duplicated per file** (`asst`/`user`/`mkAssistant`
  style helpers) by convention — don't centralize them.
- **Label regression tests inline** with a comment naming the bug they protect.
- **Assert against contracts, not raw inputs.** Paths go through
  `normalize_project_path` (lowercased drive letter on Windows) — compare to
  `normalize_project_path("C:/x")`, never a hardcoded `"C:/x"`. Fixing a failing
  test by hardcoding a value is prohibited (root `AGENTS.md`).
- **Platform/opt-in skips are expected:** POSIX-only signal tests skip on Windows;
  `os.symlink` tests skip without the Windows symlink privilege (the equivalent
  NTFS-junction cases still run); real-environment tests are gated by
  `ARGUS_REAL_CLAUDE_ROOT`.
- **Never assert on scheduling luck.** Wait for the exact thing you assert on,
  not a proxy for it, and budget the wait generously — a tight poll is a claim
  about machine speed, not about the code. Two tests broke this and only failed
  once CI ran nine combinations on loaded runners: the scheduler test polled
  `det.calls` then asserted on `alerts` (written later), and the first-run test
  asserted a background thread hadn't finished yet. Both are now deterministic;
  the "known Windows scheduler flake" is gone, not tolerated.
- **Anything that closes the DB must first join the background writer threads**
  (`join_first_run_threads` and `join_search_backfill_threads`, both wired into
  the `db` fixture). Those threads write to
  the connection, and closing a `sqlite3` connection out from under another
  thread is undefined behaviour — it segfaulted CI (exit 139) rather than
  failing a test.
- **CLI tests** use `typer.testing.CliRunner` against `from argus.cli import app`.
- **Dashboard source guards** live in `tests/dashboard/` and scan
  `dashboard/src` as text (`test_no_raw_html.py` forbids raw-HTML sinks). The
  dashboard's own unit/e2e tests are `npm test` / `npm run e2e` in `dashboard/`.

## Verification

`uv run pytest` — green except the documented skips. Report any flake or
pre-existing failure explicitly rather than masking it.
