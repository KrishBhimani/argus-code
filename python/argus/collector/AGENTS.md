# AGENTS.md — collector (ingest pipeline + backfill)

Parent: `python/argus/AGENTS.md`. Turns transcript files into stored rows.

## Purpose

`pipeline.py` ingests one file tick; `first_run.py` orchestrates the first-pass
ingest (foreground recent files, background older files) and backfills derived data
after a schema/feature upgrade.

## Local Contracts

- **Offset-driven reads.** `ingest_file` reads only bytes after the stored file
  offset, upserts turns/tool_calls/segments, then recomputes the session. A
  fully-read file sits at EOF and is **not** re-read on the next tick. To force a
  re-extract you MUST reset its offset to 0 (`repo.set_file_offset(path, 0)`) —
  re-calling `ingest_file` alone does nothing for an EOF file.
- **Chunked ingest == one-pass ingest** (pinned by
  `tests/collector/test_incremental_invariant.py`; keep it green for every
  adapter). `_apply_result` owns the cross-tick merge: a stored turn with the
  same id and a *smaller* `sequence` on a tick that didn't start at offset 0 is
  the head of a message whose tail this tick holds → keep head
  sequence/timestamp/input/cache, `max` output, add the tail's tool_use blocks
  that aren't stored yet (idempotent if the same tail is re-read), and
  re-point this tick's calls to the head's sequence. A read from offset 0 (or an
  equal sequence = the same lines re-read) overwrites, which is how pre-fix rows
  get rewritten. `tool_error_ids` are applied by id after the call upsert.
  `_header_for_recompute(..., authoritative=from_offset == 0)` keeps the stored
  `project_path`/`started_at` on later ticks; `build_session` widens
  start/end with every stored turn.
- **Sub-agents are walked via the parent.** A parent ingest discovers
  `adapter.sub_session_files_for(parent)` and ingests any that **grew past their
  offset**. Sub-agent session ids contain `/` (`<parent>/agent-<hex>`).
- **Segments are gated on indexing.** Parent and sub-agent segments are written
  only when `repo.is_search_indexing_enabled()` (see `store/AGENTS.md`). So
  enabling indexing *after* ingest requires re-reading the relevant files.
- **Backfill (`_backfill_missing_derived_data`) routes through parents.** Ids with
  `/` are skipped by the processing loop ("walked via parents"). Anything needing a
  sub-agent file re-read must add the **parent** id to both the work set and
  `deep_reset` (deep_reset zeroes the sub-agent file offsets). Missing-segment
  sessions therefore map to their parent + deep_reset — otherwise sub-agent
  segments never backfill and the dashboard's "Task given" stays empty.
- **One-shot backfills are flagged in `app_meta`.** The Agent/Task `subagent_type`
  re-read (`backfill_agent_subagent_type_v1`) marks itself done once its candidate
  list fits in a run, because default-agent calls are NULL forever and would
  otherwise re-trigger every start.
- **"Re-read everything on disk once" sweeps** (`_REREAD_ALL_SWEEPS`:
  `backfill_streamed_output_tokens_v1` for placeholder `output_tokens`,
  `backfill_tool_errors_v1` for `is_error` flags lost when a tool_result landed
  in a later tick). Pre-fix rows are indistinguishable in the DB, so the
  candidate set is every top-level session whose `computed_at` predates the
  sweep's `…_started_at` stamp and whose file still exists (deep_reset for
  sub-agents). The stamp is written by `run_first_pass_ingest` **before** any
  ingest so this process's own writes land after it; a fresh DB marks the fix
  done outright. The flag flips **after** the re-reads, only when no remaining
  candidate went un-attempted — so a capped run can't mark it done early.
  Sessions whose transcript Claude Code already deleted keep their old values —
  the archive can't be corrected from data that no longer exists.
- **In-DB repairs run on the writer path only.** `repair_session_duration_v1`
  (`_repair_session_durations_once`, from `run_first_pass_ingest`) recomputes
  `duration_sec`/`started_at_ms`/`ended_at_ms` from the stored ISO strings. The
  read-only dashboard under argusd never runs it.
- **Bounded per run.** Backfill candidates are capped (`BACKFILL_CAP`, 200) per `argus start`;
  large installs may need several restarts to converge. If you add a new bounded
  sweep, surface what was deferred rather than silently truncating.

## Verification

`uv run pytest tests/collector` — covers incremental re-ingest, sub-agent
backfill, segment gating, and the "enable indexing late" path.
