# AGENTS.md — adapters (Claude Code transcript adapter)

Parent: `python/argus/AGENTS.md`. Parses agent transcript files into typed results.

## Purpose

`claude_code/` reads Claude Code's `.jsonl` transcripts: `adapter.py` discovers and
ingests files, `schemas.py` validates each line (`AssistantLine`, `UserLine`, …),
`extract_transcript.py` derives searchable segments.

## Local Contracts

- **Discovery excludes sub-agent files.** `discover_session_files()` returns
  top-level session files only; sub-agent transcripts are reached via
  `sub_session_files_for(parent_file)`, which walks the parent's `subagents/`
  directory (including the nested `subagents/workflows/<wf>/agent-*.jsonl` layout).
- **Sub-agent session id** = `<parent_session_id>/<sub_file_stem>` (e.g.
  `claude_code:<parent>/agent-<hex>`). The `/` is what marks a row as a sub-agent
  everywhere downstream.
- **`extract_transcript_segments`** emits `RawSegment`s with roles `assistant`,
  `thinking`, `user`, and `tool_result`; each is byte-capped (`_cap_text`). The
  first `user` segment of a sub-agent is its "Task given" in the dashboard.
- **`subagent_type` comes from the `Agent` tool (formerly `Task`).** Both names
  are recognised (`_SUBAGENT_TOOLS` in `extract_tool_calls.py`); rows ingested
  before the rename fix are re-read once by the collector's one-shot backfill
  (`backfill_agent_subagent_type_v1` in `app_meta`). A call with no
  `subagent_type` in its input (the default agent) legitimately stays NULL.
- **A streamed message is many lines; `output_tokens` comes from the final one.**
  Claude Code appends one `assistant` line per content block while a reply
  streams, all sharing `message.id`. Early lines carry the API's `message_start`
  placeholder usage (single-digit `output_tokens`); only the last line has the
  real count. `extract_turns` therefore takes `max(output_tokens)` across the
  group, while `input`/`cache_*` (fixed before generation, identical on every
  line) come from the first. Taking the first line for output under-counted by
  ~13% on real transcripts — don't regress to `group[0]` for output.
- **Per-tick values must not pretend to describe the file.** The watcher feeds
  a live transcript in many small ticks, so `ingest_file` sees a slice. Turn
  `sequence` (and each call's `turn_index`) is the **byte offset of the
  message's first line** — file-wide monotonic, identical however the file was
  chunked; the dashboard renumbers turns 1..n for display. The header's
  `project_path` is the first parsed line's `cwd` and `started_at` the slice's
  first assistant line: only a read from offset 0 makes them the session's
  values (the collector keeps the stored ones otherwise). Tool errors are
  returned twice: on calls in the same slice, and as `tool_error_ids` for the
  collector to apply by id to calls stored by an earlier tick.
- **Forked transcripts carry copies of their parent's lines.** A fork
  (`sessionKind: "bg"`) starts with verbatim copies (same `uuid`/`message.id`,
  `sessionId` rewritten, the parent's id kept in `session_id`). `copied_from()`
  in `extract_turns.py` reads that claim into `metadata.origin_session_id`; the
  claim alone is not proof (real non-copied lines can differ on `session_id`),
  so the collector decides — the adapter only reports.
- **Known data limitation — do not design against it:** sub-agent ids are exactly
  one level deep, and there is no stored workflow grouping or spawn-turn link.
  Don't build features that assume a nested sub-agent tree or a spawn→child edge;
  the data isn't there.
- **`ingest_file(path, offset)` is pure-ish:** it parses from a byte offset and
  returns `(result, new_offset)`. It does not write to the DB — `collector/` owns
  persistence and offset bookkeeping.
- **Path containment is enforced at `ClaudeCodeAdapter.ingest_file`, and that is
  deliberate.** It is the one choke point every read reaches — discovery, the
  watcher's fs events, and the pipeline's sub-agent walk — so a path that doesn't
  `resolve()` under the adapter's root gets an empty result and a logged warning
  instead of being read. Don't move this check out to the callers: it lived only
  in `discover_session_files()` once, and `sub_agent_files_for()` plus the watcher
  silently bypassed it into an arbitrary-file-read (bytes reached `parse_errors`,
  which `GET /api/parse-errors` serves out). `sub_agent_files_for()` now *also*
  takes `claude_root` and filters — required, not optional, so a new caller must
  confront it. Covers symlinks and NTFS junctions alike (both resolve out).

## Verification

`uv run pytest tests/adapters`. Real-`~/.claude/` tests are gated by
`ARGUS_REAL_CLAUDE_ROOT` and skipped otherwise.
