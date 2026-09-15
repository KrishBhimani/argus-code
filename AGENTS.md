# AGENTS.md — Argus (argus-code) root work contract

> This repository uses the **DOX framework**: AGENTS.md files are binding work
> contracts for their subtree. Before editing any path, read this file and every
> AGENTS.md on the route from the repo root down to that path. The nearest doc
> controls local detail; this root controls repo-wide rules. No child doc may
> weaken DOX. Re-read the chain in-session; do not rely on memory.
>
> **After a meaningful change, run the DOX pass:** update the nearest owning
> AGENTS.md (and any affected parent/child index) when you change purpose, scope,
> contracts, workflows, inputs/outputs, or the doc tree itself; delete stale text;
> then re-check changed paths against the chain. Pure no-behavior edits may leave
> docs unchanged, but the pass still happens.

## Purpose

Argus is a **local-first** analytics dashboard for Claude Code and Codex CLI (and
other coding agents). A watcher ingests each agent's `.jsonl` session transcripts
into a local SQLite archive (`~/.argus/argus.db`); a FastAPI server serves a static React
dashboard at `http://localhost:4242`. No network, no telemetry, no LLM calls
(except opt-in `argus pricing refresh`). Backend + dashboard ship together in the
`argus-code` wheel on PyPI.

Orientation docs (prose, not contracts — read but don't duplicate into AGENTS.md):
- `ARCHITECTURE.md` — how the pieces fit together (read before adding a feature).
- `CONTRIBUTING.md` — dev quick start (`uv sync`, `uv run argus start`).
- `TESTING.md` — test catalog and conventions.

## Ownership

This root owns: the development workflow below, global engineering rules, the
release process, and the top-level Child DOX Index. Code-level contracts live in
the nearest child AGENTS.md.

## Local Contracts

### Development workflow (feature / bug → PR → merge → release → tag → publish)

Follow this end-to-end for every feature or bug fix.

1. **Branch.** Never commit feature/bug work directly to `main`. Branch from an
   up-to-date `main`: `fix/<slug>` for bugs, `feat/<slug>` for features.
2. **Implement test-first.** Reproduce the bug (or specify the feature) with a
   failing test, then make it pass. See `tests/AGENTS.md`.
3. **Commit** in small, logical units. Conventional-commit subjects
   (`fix:`, `feat:`, `test:`, `release:`, `docs:`). End every commit message with:
   ```
   Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
   ```
4. **Verify** before opening the PR — see Verification below. Tests must be green
   (the one known Windows flake is the scheduler timing test; confirm it passes in
   isolation).
5. **Push & open PR** against `main`: `gh pr create --base main --head <branch>`.
   End the PR body with:
   ```
   🤖 Generated with [Claude Code](https://claude.com/claude-code)
   ```
6. **Merge** with squash and delete the branch:
   `gh pr merge <n> --squash --delete-branch`. Then sync local `main`.
7. **Release** (only when cutting a version — see Release process below).
8. **Tag + GitHub release:** `gh release create vX.Y.Z --target main --title vX.Y.Z --notes "..."`.
9. **PyPI publish is the maintainer's manual step** — see Release process.

### Release process (manual — no CI release, no stored credentials)

When cutting version `X.Y.Z`:
1. Bump the version in **both** files with the Edit tool (not PowerShell
   `Set-Content` — it injects a BOM): `pyproject.toml` (`version = "X.Y.Z"`) and
   `python/argus/__init__.py` (`__version__ = "X.Y.Z"`).
2. `uv lock` to sync `uv.lock` to the new version.
3. If the dashboard changed, rebuild and refresh the shipped copy — see
   `dashboard/AGENTS.md`.
4. Clean-build the artifacts: **`rm -rf dist && uv build`** (always clear `dist/`
   first, or stale files get uploaded).
5. Confirm the wheel ships the right version + dashboard before publishing.
6. Tag + GitHub release (workflow step 8).
7. **Publish to PyPI — maintainer only:** `uv publish --token pypi-<token>`. The
   token is the maintainer's secret; **an agent must never run this or handle the
   token.** Stop and hand off.

### Screenshots (README + site)

`assets/` is the only tracked image location (`docs/` is gitignored). Regenerate
every dashboard screenshot with an Argus instance running on a spare port:
`uv run argus start --port 4243`, then
`node scripts/screenshots/capture.mjs --out <tmp>` and
`node scripts/screenshots/frame.mjs --in <tmp>`. Filenames are stable; the README
and the site repo (`KrishBhimani/argus`) hotlink them from `main`. The capture
script redacts the Settings parse-error paths in-page before shooting; extend the
same hook rather than editing images by hand.

## Work Guidance

Global engineering rules (binding):
- **Never destroy user data in `~/.argus/argus.db`.** Migrations must be
  non-destructive, idempotent, and self-healing. See `python/argus/store/AGENTS.md`.
- **No hacks to make a test pass.** Fix the real cause, or correct a stale test
  expectation to match the real contract (and say so). This is a prod package used
  by many; never hardcode a value just to go green.
- **Backend runs from source** (`uv run argus start`) — no build step for Python.
  Only the dashboard has a build step.
- Plans/specs under `docs/superpowers/` are scratch and gitignored — do not commit them.

## Verification

- Backend: `uv run pytest` (must be green; ~480 tests, ~35s on Windows).
- Dashboard: `cd dashboard && npm test && npm run build && npm run size` (all must
  succeed); `npm run e2e` before a release.
- Built form smoke test: see `CONTRIBUTING.md` "Running the built form".

## User Preferences

- Releases are cut deliberately by the maintainer; agents prepare everything up to
  (and including the GitHub tag/release when asked) but never run `uv publish`.
- Prefer honest reporting over green-at-any-cost: surface flakes, skips, and
  pre-existing failures explicitly.

## Child DOX Index

- `python/argus/AGENTS.md` — Python backend package; owns the subsystems without
  their own doc (`core`, `daemon`, `detectors`, `pricing`, `scaffold`, `schema`)
  and indexes the four below.
  - `python/argus/store/AGENTS.md` — SQLite, migrations, repository; data-safety
    and path-normalization contracts.
  - `python/argus/collector/AGENTS.md` — ingest pipeline and the missing-data
    backfill (incl. sub-agent segment re-read).
  - `python/argus/adapters/AGENTS.md` — Claude Code and Codex adapters,
    transcript-segment extraction; sub-agent file layouts.
  - `python/argus/server/AGENTS.md` — FastAPI routes, static serving, clean shutdown.
- `dashboard/AGENTS.md` — React SPA dashboard and the `dashboard-dist` ship chain.
- `tests/AGENTS.md` — pytest conventions and platform quirks.
