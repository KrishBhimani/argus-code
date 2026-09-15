# AGENTS.md — adapters (Claude Code + Codex transcript adapters)

Parent: `python/argus/AGENTS.md`. Parses agent transcript files into typed results.

## Purpose

`claude_code/` reads Claude Code's `.jsonl` transcripts: `adapter.py` discovers and
ingests files, `schemas.py` validates each line (`AssistantLine`, `UserLine`, …),
`extract_transcript.py` derives searchable segments.

`codex/` reads OpenAI Codex CLI rollouts (`~/.codex/sessions/**/rollout-*.jsonl`):
`discover.py` finds files and peeks line 1 to build a thread index, `lines.py`
normalizes both on-disk formats into `Line` records, `extract_turns.py` /
`extract_tool_calls.py` / `extract_transcript.py` derive rows, `state.py` carries
per-file context across ticks, `history_jsonl.py` feeds the Prompts page.

## Shared contracts (every adapter)

- **`native_session_id(path)` is the only way a stored id maps back to a file.**
  The collector (backfills, sub-session ids) calls it; never assume the file stem.
  Claude Code returns the stem; Codex returns the thread uuid.
- Adapters never import each other. Shared helpers (segment cap, realpath
  containment) are duplicated per adapter on purpose.

## Local Contracts — Claude Code (`claude_code/`)

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

## Local Contracts — Codex (`codex/`)

- **Root and discovery.** `$CODEX_HOME` else `~/.codex`; present when `sessions/`
  exists. Rollouts are `sessions/**` and `archived_sessions/**` files named
  `rollout-<ts>-<thread uuid>[_<rollout uuid>].jsonl[.zst]`. Discovery peeks line 1
  of every rollout (`ThreadIndex`) to classify format and parent links; it returns
  root threads only. A child (meta `parent_thread_id` or
  `source.subagent.thread_spawn.parent_thread_id`) is reached via
  `sub_session_files_for(parent)` and skipped by the watcher, like Claude's
  `subagents/`. `should_skip` is also true for every non-rollout `.jsonl` under
  `~/.codex` (history, logs).
- **Ids.** `native_session_id` is the thread uuid (plus `_<rollout>` for revert
  files, which are distinct sessions); sub-sessions are `codex:<parent>/<child>`.
- **Two on-disk formats.** Envelope (`{timestamp, ordinal?, type, payload}`, Codex
  v0.34+) and legacy raw (line 1 `{id, timestamp, instructions}` then bare
  Responses items, no usage). Both normalize to `Line`; the format is decided once
  per file from line 1. Legacy files are still produced by some Codex builds in
  2026 — real data on the dev machine had six of seven — so this path is live.
- **Turn = one `token_count`.** Usage from `last_token_usage`; a repeated
  `total_token_usage` is a rate-limit refresh and is skipped; totals are only
  differenced when `last` is absent; all-zero usage (the context-window-exceeded
  rewrite) is skipped. Model = latest `turn_context.model`, else
  `thread_settings_applied`, else `unknown` (never a fabricated default). Legacy
  raw files emit one zero-token turn per assistant message so tool calls and
  transcript still exist.
- **Holdback.** A tick consumes up to the last `token_count` line; the tail waits
  for the next tick so every tool call/segment has its turn. A lone `session_meta`
  at offset 0 is consumed on its own. Legacy raw and `.zst` files consume whole.
  A prompt-only stub (no model response) therefore never becomes a session.
- **Inherited history.** Paginated children/forks drop lines with
  `ordinal < subagent_history_start_ordinal | forked_from_ordinal_exclusive |
  history_base.end_ordinal_exclusive`. Legacy children/forks drop `token_count`
  lines within 1 s of the meta timestamp (replayed parent usage). Duplicated
  legacy segments/tool calls in children are a known limitation.
- **Cross-tick state** (`state.py`) lives on the adapter instance keyed by
  resolved path and is rebuilt from the file head on a miss; the protocol
  signature is unchanged. Turn ids are `tc@<byte offset>` (`msg@` for legacy),
  segment ids `<offset>:<block>` — stable across re-reads.
- **User prompts** come from `user_message` events with `kind` absent or
  `"plain"` (real v0.45 files tag injected context `"environment_context"`) or
  paginated `item_completed` user items; raw user `response_item` messages are
  never indexed.
- **Prompts.** `history.jsonl` lines carry `session_id`; rows are written with
  `project_path=''` when the session is not ingested yet and healed by
  `Repository.resolve_prompt_projects()`.
- **zstd is optional.** Without a decoder (`compression.zstd` on 3.14+ or the
  `zstandard` package), `.zst` rollouts are skipped with one recorded parse error
  and offset = size, never retried every tick.
- **Unverified against a rollout with model responses** (the dev machine's real
  files are prompt-only stubs): `token_count` cadence, `item_completed.item.type`
  casing, exec-output error markers, `apply_patch` item kind, MCP `namespace`
  shape. `tests/adapters/codex/test_real_root.py` is the gate.

## Verification

`uv run pytest tests/adapters`. Real-data tests are gated by
`ARGUS_REAL_CLAUDE_ROOT` / `ARGUS_REAL_CODEX_ROOT` and skipped otherwise.
`uv run python scripts/codex_doctor.py` prints a structure-only diagnosis of a
machine's Codex rollouts (record kinds, counts, what Argus extracts, parse
errors) — safe to paste into an issue; ask for it before debugging "no Codex
sessions" reports.
