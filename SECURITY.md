# Security Policy

## Reporting a vulnerability

If you find a security issue in Argus, please **don't open a public
GitHub issue**. Instead, one of:

- Open a private [GitHub Security Advisory](https://github.com/KrishBhimani/argus-code/security/advisories/new) on the repository, or
- Email the maintainer (see commit history for an address).

I'll respond within a few days. Please include enough detail to
reproduce — a proof-of-concept JSONL fixture, a curl command, or a
repro repo. If the issue is a path traversal / file-disclosure bug,
**do not include the actual contents of any file you were able to
read** in the report.

## Supported versions

Argus is pre-1.0. Only the **latest published version** receives
security fixes. Pinning to an older 0.x is at your own risk.

## Threat model

Argus is built for one user, one machine. It assumes:

- **Trusted local environment.** Anyone with shell access to your
  account can already read `~/.claude/` and `~/.codex/`, so Argus
  reading the same files isn't an additional privilege.
- **Least read access.** From a Codex home (`$CODEX_HOME`, default
  `~/.codex`) Argus reads only the session rollouts under `sessions/`
  and `archived_sessions/`. It never opens `auth.json` (cached
  credentials), `config.toml`, the SQLite state databases, memories,
  logs, plugins or skills.
- **Untrusted JSONL contents.** Session logs are produced by Claude
  Code and Codex, but their contents (model names, project paths,
  transcript text, tool inputs) are user-controlled. Argus treats them
  as untrusted input — they're validated before use, escaped before
  rendering in the dashboard, and never executed. Codex's encrypted
  reasoning is never stored, and Codex parse errors never record the
  offending line's content.
- **Untrusted browsers visiting localhost.** Other webpages the user
  loads while Argus is running are not trusted. They cannot read
  Argus data (Same-Origin Policy on responses, backed by a loopback
  `Host` allowlist so DNS rebinding can't turn a hostile domain into
  a same-origin one) and cannot trigger state changes (CSRF Origin
  check on POST).

## Defaults that matter

- **`127.0.0.1`-only by default.** The server doesn't listen on `0.0.0.0`
  unless you pass `--host 0.0.0.0`, which prints a loud warning. Anyone
  on your LAN can read every prompt you've ever sent if you flip this.
- **Transcript indexing is opt-in.** The full-text search index over
  agent replies, your prompts and replies, and tool output is off until
  you enable it via Settings or `argus indexing enable`. Cost/token
  analytics work without it.
- **No external network calls.** The only outbound HTTP request in the
  entire codebase is `argus pricing refresh`, a manual command that
  fetches one JSON file from LiteLLM's GitHub. There is no telemetry,
  no analytics, no embedding API, no LLM call.

## Out of scope

The following are **not** security issues for the purpose of this policy:

- An attacker with shell access to the user's account reads
  `~/.argus/argus.db`. (They can read `~/.claude/` and `~/.codex/`
  directly anyway.)
- A user runs `argus start --host 0.0.0.0` and someone on their LAN
  reads the dashboard. The warning message at startup is intentional.
- Cost figures don't match `ccusage` exactly. After the windowed-
  aggregation fix the two now use the same per-turn bucketing, but
  small residual differences remain: (a) argus uses a bundled pricing
  table while ccusage fetches LiteLLM live — run
  `argus pricing refresh` to close the gap; (b) argus distinguishes
  5-minute vs 1-hour cache-write rates where ccusage uses one flat
  rate. Both are deliberate choices, not security issues.
- A dev-only dependency (pytest, freezegun, pytest-cov) has a CVE.
  Dev-only dependencies don't ship to wheel consumers.

## What's already hardened

Things this codebase deliberately does to reduce attack surface:

- All SQL queries use parameterized statements (no string-concatenated
  user input into SQL).
- All user-controlled strings rendered in the dashboard go through
  `escapeHtml()` before reaching `innerHTML`. The only HTML allowed
  through is `<mark>...</mark>` from FTS5 `snippet()`, applied via a
  separate `safeSnippet()` helper that escapes everything else.
- `watchdog` does not follow symlinks by default. Every read additionally
  goes through the adapter's `ingest_file`, which calls
  `Path.resolve(strict=True)` and refuses anything that doesn't
  canonicalise under the agent's own tree — `~/.claude/` for
  `ClaudeCodeAdapter`, and only the Codex home's `sessions/` /
  `archived_sessions/` for `CodexAdapter` — symlink or NTFS junction.
  The check sits at that single choke point (not at each caller) so
  discovery, the watcher, and the sub-agent walk are all covered by
  construction; sub-agent discovery filters as well.
- Per-tick read cap of 64 MiB on session JSONL and `history.jsonl`,
  so a runaway or hostile multi-GB file can't OOM the process.
- CSRF Origin check on all non-GET API routes (FastAPI middleware in
  `python/argus/server/app.py`).
- Loopback `Host` header allowlist on **every** route, static mount
  included (`is_loopback_host` in `python/argus/server/app.py`). This is
  what stops DNS rebinding: a page on `evil.com` that repoints its own
  DNS at `127.0.0.1` still sends `Host: evil.com` and gets a 421, so it
  never reaches the GET routes the Origin check deliberately exempts.
  Skipped only under `--host 0.0.0.0`, which is already a warned opt-out.
- No build hooks running on user machines. The wheel ships pure Python
  + bundled data (`dashboard-dist/`, `pricing/`, `templates/`).
  Installation runs no arbitrary code.
- `pyproject.toml` `[tool.hatch.build.targets.wheel]` whitelist — only
  `python/argus`, `dashboard-dist`, `pricing`, and `templates` ship. No
  source tests, no docs, no `src/` legacy. The sdist has its own
  allow-list (`[tool.hatch.build.targets.sdist] only-include`), so local
  files in the build directory (other worktrees, editor or agent state)
  can't end up in a published source archive.
- `argus claude` scaffolding is a pure file copy — no templating,
  substitution, or code execution. It writes only to the path you hand
  `init` (created if absent) or under `~/.argus/templates/`, and never
  overwrites an existing `CLAUDE.md`. That containment is enforced, not
  just intended: every destination must canonicalise under the target
  directory, so a hostile repo shipping `.claude/` as a symlink or NTFS
  junction can't redirect the write, and template names are validated as
  plain directory names so `--template ../../x` can't escape the store.

If you spot a regression on any of the above, that's a real bug and
worth reporting.
