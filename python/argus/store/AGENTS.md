# AGENTS.md — store (SQLite, migrations, repository)

Parent: `python/argus/AGENTS.md`. The data layer; `~/.argus/argus.db` is also a
durable **archive** (rows outlive Claude Code's own `.jsonl` cleanup).

## Purpose

`db.py` opens/migrates the DB; `migrations/inline.py` holds the schema + versioned
migrations; `repository.py` is the typed read/write API over SQLite.

## Local Contracts

- **Never destroy user data.** Migrations are forward-only and must be
  **non-destructive, idempotent, and re-runnable**. No `DROP`/`DELETE`/`TRUNCATE`
  of user rows to "fix" a schema. Add a new migration by appending `(N, MIGRATION_00N)`
  to the versioned list with the next number **and** listing what it creates in
  `_MIGRATION_ARTIFACTS` (`db.py`; `open_db` raises `KeyError` without an entry).
- **`schema_version` is a claim; artifacts are checked.** After the version loop,
  `open_db` re-runs any migration whose listed artifacts (tables, indexes,
  triggers, `table.column`) are missing, never stamping the version backwards.
  This heals a crash between DDL and the version bump, and a DB where another
  branch's migration stamped the same number (a real DB reached "7" through an
  unmerged branch and never got `idx_turns_message`). It relies on every
  migration being idempotent, so keep them that way.
- **The one sanctioned delete of ingested rows is fork de-duplication**
  (`delete_duplicated_turns`, `delete_fork_copy_turns`): turns/tool calls that a forked session stored as
  copies of *another stored session's* messages. It removes derived duplicates
  only — the origin session keeps the rows, the session row itself stays — and
  callers must verify each copy first (see `collector/AGENTS.md`): against the
  fork's transcript, or when that's gone, by an exact match of message id,
  timestamp, model and token counts (`dedupe_shared_messages`). Don't widen it
  into a general "clean up" path.
- **`idx_turns_message` (MIGRATION_007)** indexes the message-id suffix of
  `turns.id` (`substr(id, length(session_id) + 2)`); queries must use that exact
  expression to hit it.
- **`open_db` self-heals half-applied migrations** and runs each migration
  transactionally. `_run_migration` ignores a `duplicate column name` error so a
  re-run of an `ADD COLUMN` is a no-op. `_split_statements` respects trigger
  `BEGIN…END` and `CASE…END`, so don't hand it naive `;`-splitting assumptions.
  The DB runs in autocommit (`isolation_level=None`); `executescript` force-commits.
- **Writes go through `Repository.transaction()`** — never `with self.db:` (in
  autocommit mode it issues no BEGIN, so every `executemany` row committed alone
  and its `__exit__` ended a caller's transaction) and never `self.db.commit()`.
  Every write method carries `@_writes`; add it to any new one. The outermost
  `transaction()` runs `BEGIN IMMEDIATE`/`COMMIT`/`ROLLBACK`, nested calls use a
  SAVEPOINT. It holds the connection's `write_lock` (an `RLock` on the
  `ArgusConnection` that `open_db` returns via `factory=`) for the whole block:
  **writers serialise, readers don't take the lock**, so a reader thread sharing
  the connection can see an open transaction's uncommitted rows for its duration
  (one file ingest). Another *process* writing the same file (e.g. an `argus`
  CLI command while argusd ingests) waits up to `busy_timeout` (5 s).
- **`normalize_project_path` is a cross-source join key.** It maps `\`→`/`,
  strips a trailing `/`, preserves empty, and **lowercases on Windows**. Both the
  session-ingest side and the `history.jsonl` prompt side MUST pass project paths
  through it so the prompt↔session linkage join matches byte-for-byte. Tests assert
  against `normalize_project_path(...)`, not raw paths — do not "fix" that by
  hardcoding a cased path.
- **Transcript indexing is opt-in**, stored as `app_meta.enable_transcript_search`
  via `is_search_indexing_enabled` / `set_search_indexing_enabled`. Segments are
  written by `collector/` only when this is on. Backfill selectors
  (`sessions_missing_segments`, `_missing_tool_calls`, `_missing_tool_use_ids`,
  `_with_unpriced_turns`, `top_level_sessions_computed_before`) feed
  `collector/first_run.py` — keep their shapes stable. The last one is
  deliberately unbounded: the collector filters to files still on disk before
  applying its per-run cap.
- **Upserts are idempotent** (conflict-replace) so re-ingesting a file from a reset
  offset never duplicates or corrupts rows. One exception is deliberate:
  `tool_calls.is_error` is monotonic (`MAX(old, new)` on conflict, and
  `mark_tool_calls_errored` only sets 0 → 1), because the error comes from a
  tool_result that may be read in a different tick than the call.
- **`upsert_session` never rewrites `started_at`** (insert-only), but does
  rewrite `duration_sec`/`started_at_ms`/`ended_at_ms`; callers must pass a
  session whose start is the file's start. `repair_session_time_columns`
  re-derives those three columns from the stored ISO strings (idempotent).

## Work Guidance

When adding a column/table: add it to `inline.py` (new migration), surface it in
`repository.py`, and update `schema/` types + `server/` serialization as needed.

## Verification

`uv run pytest tests/store` — includes migration self-heal and normalization tests.
