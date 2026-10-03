# AGENTS.md — adapters (Claude Code + Codex)

Parent: `python/argus/AGENTS.md`. Parses agent transcript files into typed results.

## Purpose

`claude_code/` reads Claude Code's `.jsonl` transcripts: `adapter.py` discovers and
ingests files, `schemas.py` validates each line (`AssistantLine`, `UserLine`, …),
`extract_transcript.py` derives searchable segments.

`codex/` reads Codex rollouts (`$CODEX_HOME/sessions`, `archived_sessions`):
`lines.py` (envelope reader), `discover.py` (home, containment, `ThreadIndex`),
`records.py` (pure per-record interpretation), `state.py` (the per-file fold and
`ContextCache`), `ingest_file.py` (one read → `AdapterIngestResult`), `adapter.py`
(`CodexAdapter`, `agent = "codex"`).

Every adapter class must define **all** protocol methods, including the optional
hooks (`extra_watch_paths`, `ingest_extra`, `sub_session_files_for`,
`should_skip`, `normalize_model_name`, `native_session_id`): the collector calls
them directly and a `Protocol`'s default bodies are not inherited.
`native_session_id(path)` is the agent's own id for a file (Claude: the stem);
the collector keys sub-session ids and backfill lookups on it
(`base.native_session_id_for` falls back to the stem for adapters without it).

## Claude Code (`claude_code/`)

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
- **Known data limitation (Claude Code) — do not design against it:** sub-agent ids are exactly
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

## Codex (`codex/`)

Shapes were profiled on real rollouts (Codex Desktop, cli 0.159.2). Binding:

- **Home and reads.** Root is `$CODEX_HOME`, else `~/.codex`; a set-but-missing
  `CODEX_HOME` means the adapter is absent (never fall back to `~/.codex`). Only
  `sessions/` and `archived_sessions/` are read. Never opened: `auth.json`,
  `*.sqlite*`, `config.toml`, `memories/`, `log/`, `plugins/`, `skills/`,
  `history.jsonl`. Containment (`contained()`) is enforced in
  `CodexAdapter.ingest_file`, the single choke point, as for Claude.
- **Identity** is the first record's `session_meta.id` (thread id), never the
  file name. One thread may span several files (continuation segments
  `<thread>_<suffix>.jsonl`); each keeps its own offset and all feed one session
  `codex:<thread>`. `native_session_id()` returns the thread id.
- **Turn = one `token_usage_record`** (`native_turn_id = response_id`,
  `sequence` = the record's byte offset). `input_tokens` includes cached and
  cache-write tokens; `output_tokens` includes reasoning. `event_msg/token_count`
  is a fallback only for files with no usage record earlier in the file, and only
  when `total_token_usage.total_tokens` advanced (real files repeat it). Model
  and effort come from the `turn_context` with the same Codex `turn_id`, else the
  latest one, else `thread_settings_applied`, else `"unknown"`.
- **Tool calls** come from `item_completed` items (`CommandExecution` → `shell`,
  `FileChange` → `apply_patch`, `McpToolCall` → `mcp__<server>__<tool>`,
  `Extension` → its `kind`, `ImageView` → `view_image`) plus model-side calls
  other than the `exec`/`wait` code-mode wrappers. `mcp__*`-namespaced function
  calls are not counted (their `McpToolCall` item is). A request is assigned to
  the usage record that follows it in its task; an item to the one before it.
  `is_error` only on explicit signals (non-zero `exit_code`, failed status,
  `result.isError`).
- **Chunked == one pass.** Every read starts at `window_for()`: if a Codex task
  (`task_started` … `task_complete`/`turn_aborted`) is still open at the stored
  offset, the read re-parses it from its start, so a task's usage records and
  items are always seen together and re-emitted (idempotently, keyed by Codex
  ids) until it ends. The same `FileState.apply` fold drives the head scan and
  the read; `ContextCache` is only an optimisation (a miss rescans the head).
  Guarded by `tests/collector/test_codex_incremental_invariant.py`.
- **Sub-agents.** A child is a file whose first `session_meta` names a parent
  (`parent_thread_id` or `source.subagent.thread_spawn.parent_thread_id`). It is
  skipped by discovery and the watcher and reached via `sub_session_files_for`,
  flattened under its top-level ancestor as `codex:<root>/<child>` (depth,
  nickname, role, path kept in metadata). Records with
  `ordinal < subagent_history_start_ordinal` (or
  `history_base.end_ordinal_exclusive`) are copied history and skipped.
  `should_skip` is also true while a file's first line is incomplete.
  Because child fs events are skipped, a child is re-read only on a parent
  ingest — so a read of the parent with **no new lines must still report the
  thread id** in its header (not the file stem), or the collector can't find
  the session and never reconciles a child that kept writing after its parent
  went quiet. `sub_session_files_for` rescans the sessions tree at most every
  `SUB_REFRESH_INTERVAL_SEC` (it runs on every parent tick); discovery always
  rescans, so a new child is picked up on a later parent tick or at startup.
- **Privacy.** Never stored: reasoning, injected developer/user
  `response_item` messages, base instructions, `git.repository_url`.
  `ParseError.raw_line_truncated` is always `""`.
- **Known limits.** Long-context (>272K input) pricing tier not modelled;
  `gpt-5-codex` is unpriced ($0). Files with no task markers are not re-parsed
  per task, so a tool call split from its response by a read boundary may stay
  unassigned to a turn. `history.jsonl` prompts are not ingested (needs a
  migration). A child whose parent file is gone is not shown. A session's
  `started_at` is fixed by the first file ingested, so a continuation segment
  ingested before its original file (out of discovery order) keeps the later
  start. `.jsonl.zst` rollouts are not read.

## Verification

`uv run pytest tests/adapters`. Real-data tests are opt-in:
`ARGUS_REAL_CLAUDE_ROOT` (a real `~/.claude/`) and `ARGUS_REAL_CODEX_ROOT` (a real
Codex home; cross-checks stored output tokens against the rollouts' usage
records).
