"""Pipeline regression tests — turn merging, sub-agent double-count, segment gating."""
from __future__ import annotations

import json
from pathlib import Path

from argus.adapters.claude_code.adapter import ClaudeCodeAdapter
from argus.collector.pipeline import ingest_file
from argus.pricing.load import load_pricing_table
from argus.store.repository import normalize_project_path


def _line(msg_id: str, *, sid="sess1", input_tokens=1_000_000, output_tokens=1_000_000) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "sessionId": sid,
            "uuid": "u" + msg_id,
            "timestamp": "2026-05-01T00:00:00Z",
            "cwd": "C:/proj",
            "version": "2.1.94",
            "userType": "external",
            "entrypoint": "cli",
            "message": {
                "id": msg_id,
                "model": "claude-opus-4-7",
                "role": "assistant",
                "content": [],
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            },
        }
    )


def test_incremental_reingest_does_not_reduce_session_totals(tmp_path: Path, repo):
    """REGRESSION: re-ingesting a file must merge new turns, not stomp totals."""
    claude_root = tmp_path / ".claude"
    proj = claude_root / "projects" / "C--proj"
    proj.mkdir(parents=True)
    f = proj / "sess1.jsonl"
    f.write_text(_line("m1") + "\n" + _line("m2") + "\n" + _line("m3") + "\n", encoding="utf-8")

    adapter = ClaudeCodeAdapter(claude_root)
    table = load_pricing_table()

    ingest_file(adapter, f, repo, table)
    after1 = repo.get_session("claude_code:sess1")
    assert after1 is not None
    assert after1.turn_count == 3
    cost1 = after1.total_cost_usd
    assert cost1 > 0

    with f.open("a", encoding="utf-8") as fp:
        fp.write(_line("m4") + "\n")
    ingest_file(adapter, f, repo, table)
    after2 = repo.get_session("claude_code:sess1")
    assert after2 is not None
    assert after2.turn_count == 4
    assert after2.total_cost_usd > cost1  # MUST increase

    # Re-ingest with no new content → totals stay the same.
    ingest_file(adapter, f, repo, table)
    after3 = repo.get_session("claude_code:sess1")
    assert after3 is not None
    assert after3.turn_count == 4
    assert after3.total_cost_usd == after2.total_cost_usd


def test_subagent_files_not_counted_twice(tmp_path: Path, repo):
    """REGRESSION: discover must NOT return sub-agent files; rollup MUST sum."""
    claude_root = tmp_path / ".claude"
    proj = claude_root / "projects" / "C--proj"
    proj.mkdir(parents=True)
    parent_file = proj / "parent.jsonl"
    sub_dir = proj / "parent" / "subagents"
    sub_dir.mkdir(parents=True)
    sub_file = sub_dir / "agent-aabc.jsonl"

    def line(sid: str, mid: str, tokens: int) -> str:
        return json.dumps(
            {
                "type": "assistant",
                "sessionId": sid,
                "uuid": "u" + mid,
                "timestamp": "2026-05-01T00:00:00Z",
                "cwd": "C:/proj",
                "version": "2.1.94",
                "userType": "external",
                "entrypoint": "cli",
                "message": {
                    "id": mid,
                    "model": "claude-opus-4-7",
                    "role": "assistant",
                    "content": [],
                    "usage": {
                        "input_tokens": tokens,
                        "output_tokens": tokens,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                },
            }
        )

    parent_file.write_text(line("parent", "p1", 1_000_000) + "\n" + line("parent", "p2", 1_000_000) + "\n", encoding="utf-8")
    sub_file.write_text(line("parent", "s1", 500_000) + "\n", encoding="utf-8")

    adapter = ClaudeCodeAdapter(claude_root)
    table = load_pricing_table()

    files = adapter.discover_session_files()
    # Sub-agent files MUST be excluded from top-level discovery.
    assert files == [parent_file]

    for fp in files:
        ingest_file(adapter, fp, repo, table)

    parent = repo.get_session("claude_code:parent")
    assert parent is not None
    # parent: 2 turns × (1M+1M) plus sub-agent: 1 turn × (500k+500k)
    # = fresh: 2*1M + 1*500k = 2.5M
    assert parent.total_fresh_input_tokens == 2_500_000
    assert parent.total_output_tokens == 2_500_000
    # 2 parent turns + 1 sub-agent turn = 3
    assert parent.turn_count == 3


def test_subagent_backfills_without_reingesting_parent(tmp_path: Path, repo):
    """REGRESSION: a sub-agent appearing after the parent's last turn must
    roll up on the next ingest tick — no parent growth, no wipe required.

    Models the real bug: a session ingested to EOF, then sub-agent files
    that were never picked up (here: didn't exist at first ingest). The
    second ingest reads zero new parent bytes, yet must still backfill.
    """
    def line(sid: str, mid: str, tokens: int) -> str:
        return json.dumps(
            {
                "type": "assistant",
                "sessionId": sid,
                "uuid": "u" + mid,
                "timestamp": "2026-05-01T00:00:00Z",
                "cwd": "C:/proj",
                "version": "2.1.94",
                "userType": "external",
                "entrypoint": "cli",
                "message": {
                    "id": mid,
                    "model": "claude-opus-4-7",
                    "role": "assistant",
                    "content": [],
                    "usage": {
                        "input_tokens": tokens,
                        "output_tokens": tokens,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                },
            }
        )

    claude_root = tmp_path / ".claude"
    proj = claude_root / "projects" / "C--proj"
    proj.mkdir(parents=True)
    parent_file = proj / "parent.jsonl"
    parent_file.write_text(line("parent", "p1", 1_000_000) + "\n", encoding="utf-8")

    adapter = ClaudeCodeAdapter(claude_root)
    table = load_pricing_table()

    # First ingest: parent only, no sub-agent dir yet. Offset parks at EOF.
    ingest_file(adapter, parent_file, repo, table)
    parent = repo.get_session("claude_code:parent")
    assert parent.total_fresh_input_tokens == 1_000_000
    assert parent.metadata.get("sub_agent_session_ids", []) == []

    # A workflow sub-agent transcript appears (nested layout), but the parent
    # file does NOT grow.
    wf = proj / "parent" / "subagents" / "workflows" / "wf_x"
    wf.mkdir(parents=True)
    (wf / "agent-aabc.jsonl").write_text(line("parent", "s1", 500_000) + "\n", encoding="utf-8")

    # Second ingest of the unchanged parent: must backfill the sub-agent.
    ingest_file(adapter, parent_file, repo, table)
    parent = repo.get_session("claude_code:parent")
    assert parent.total_fresh_input_tokens == 1_500_000
    assert parent.total_output_tokens == 1_500_000
    assert parent.turn_count == 2
    assert len(parent.metadata.get("sub_agent_session_ids", [])) == 1
    # Descriptive fields survived the no-new-parent-bytes recompute. project_path
    # goes through the same normalization as ingest (drive-letter lowercased on
    # Windows so prompt<->session linkage joins byte-for-byte), so compare
    # against that contract rather than the raw input.
    assert parent.project_path == normalize_project_path("C:/proj")


def test_steady_state_reingest_is_a_noop_for_unchanged_subagents(tmp_path: Path, repo, monkeypatch):
    """A re-ingest with no growth anywhere must not rewrite session rows."""
    def line(sid: str, mid: str, tokens: int) -> str:
        return json.dumps(
            {
                "type": "assistant",
                "sessionId": sid,
                "uuid": "u" + mid,
                "timestamp": "2026-05-01T00:00:00Z",
                "cwd": "C:/proj",
                "version": "2.1.94",
                "userType": "external",
                "entrypoint": "cli",
                "message": {
                    "id": mid,
                    "model": "claude-opus-4-7",
                    "role": "assistant",
                    "content": [],
                    "usage": {
                        "input_tokens": tokens,
                        "output_tokens": tokens,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                },
            }
        )

    claude_root = tmp_path / ".claude"
    proj = claude_root / "projects" / "C--proj"
    proj.mkdir(parents=True)
    parent_file = proj / "parent.jsonl"
    parent_file.write_text(line("parent", "p1", 1_000_000) + "\n", encoding="utf-8")
    wf = proj / "parent" / "subagents" / "workflows" / "wf_x"
    wf.mkdir(parents=True)
    (wf / "agent-aabc.jsonl").write_text(line("parent", "s1", 500_000) + "\n", encoding="utf-8")

    adapter = ClaudeCodeAdapter(claude_root)
    table = load_pricing_table()
    ingest_file(adapter, parent_file, repo, table)

    # On a no-growth re-ingest, the adapter must never be asked to read a
    # sub-agent file's bytes (only stat'd), and no session row is rewritten.
    real_ingest = adapter.ingest_file
    read_paths: list[str] = []

    def spy(path, from_offset=0):
        # Sub-agent reads pass a path inside subagents/; the parent re-read is
        # expected (it returns zero new turns).
        if "subagents" in Path(path).parts:
            read_paths.append(str(path))
        return real_ingest(path, from_offset)

    monkeypatch.setattr(adapter, "ingest_file", spy)
    upserts: list[str] = []
    real_upsert = repo.upsert_session
    monkeypatch.setattr(repo, "upsert_session", lambda s: (upserts.append(s.id), real_upsert(s))[1])

    ingest_file(adapter, parent_file, repo, table)
    assert read_paths == []          # no sub-agent file was re-read
    assert upserts == []             # no session row was rewritten


def test_ingests_synthetic_session_end_to_end(tmp_path: Path, repo):
    claude_root = tmp_path / ".claude"
    proj = claude_root / "projects" / "C--proj"
    proj.mkdir(parents=True)
    session_file = proj / "sess1.jsonl"
    session_file.write_text(
        json.dumps(
            {
                "type": "assistant",
                "sessionId": "sess1",
                "uuid": "u1",
                "timestamp": "2026-05-01T00:00:00Z",
                "cwd": "C:/proj",
                "version": "2.1.94",
                "userType": "external",
                "entrypoint": "cli",
                "message": {
                    "id": "m1",
                    "model": "claude-opus-4-7",
                    "role": "assistant",
                    "content": [],
                    "usage": {
                        "input_tokens": 1000,
                        "output_tokens": 2000,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    adapter = ClaudeCodeAdapter(claude_root)
    table = load_pricing_table()
    ingest_file(adapter, session_file, repo, table)
    sessions = repo.list_sessions(limit=10)
    assert len(sessions) == 1
    assert sessions[0].agent == "claude_code"
    assert sessions[0].total_fresh_input_tokens == 1000
    assert sessions[0].total_cost_usd > 0
    assert repo.get_file_offset(str(session_file)) > 0


def test_skips_segment_writes_when_search_flag_off(tmp_path: Path, repo):
    """REGRESSION: opt-out must mean no segments written, even if data exists."""
    claude_root = tmp_path / ".claude"
    proj = claude_root / "projects" / "C--proj"
    proj.mkdir(parents=True)
    session_file = proj / "sess1.jsonl"
    session_file.write_text(
        json.dumps(
            {
                "type": "assistant",
                "sessionId": "sess1",
                "uuid": "u1",
                "timestamp": "2026-05-01T00:00:00Z",
                "cwd": "C:/proj",
                "version": "2.1.94",
                "userType": "external",
                "entrypoint": "cli",
                "message": {
                    "id": "m1",
                    "model": "claude-opus-4-7",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "this is some assistant text we would normally index"}],
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 200,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    adapter = ClaudeCodeAdapter(claude_root)
    table = load_pricing_table()

    assert repo.is_search_indexing_enabled() is False
    ingest_file(adapter, session_file, repo, table)
    assert repo.segment_stats()["total"] == 0

    # Flip on, re-ingest from offset 0 → segments populate.
    repo.set_search_indexing_enabled(True)
    repo.set_file_offset(str(session_file), 0)
    ingest_file(adapter, session_file, repo, table)
    assert repo.segment_stats()["total"] == 1


def test_backfill_indexes_subagent_segments_after_enabling(tmp_path: Path, repo):
    """REGRESSION: enabling search indexing AFTER a session was ingested must
    backfill the SUB-AGENT transcript segments, not just the parent's. Sub-agent
    files sit at EOF, so they're only re-read when their offsets are reset
    (deep_reset). The missing-segments backfill must route a sub-agent to its
    parent + deep_reset — otherwise the Sub-agents tab shows an empty
    "Task given" forever."""
    from argus.collector.first_run import _backfill_missing_derived_data

    def asst(sid: str, mid: str) -> str:
        return json.dumps({
            "type": "assistant", "sessionId": sid, "uuid": "u" + mid,
            "timestamp": "2026-05-01T00:00:00Z", "cwd": "C:/proj", "version": "2.1.94",
            "message": {"id": mid, "model": "claude-opus-4-7", "role": "assistant",
                        "content": [{"type": "text", "text": "working on it"}],
                        "usage": {"input_tokens": 10, "output_tokens": 10,
                                  "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}})

    def user(sid: str, uid: str, text: str) -> str:
        return json.dumps({
            "type": "user", "sessionId": sid, "uuid": uid,
            "timestamp": "2026-05-01T00:00:00Z", "cwd": "C:/proj", "version": "2.1.94",
            "message": {"role": "user", "content": text}})

    claude_root = tmp_path / ".claude"
    proj = claude_root / "projects" / "C--proj"
    proj.mkdir(parents=True)
    parent_file = proj / "parent.jsonl"
    parent_file.write_text(asst("parent", "p1") + "\n", encoding="utf-8")
    sub_dir = proj / "parent" / "subagents"
    sub_dir.mkdir(parents=True)
    (sub_dir / "agent-aabc.jsonl").write_text(
        user("parent", "su1", "Research consensus mechanisms") + "\n"
        + asst("parent", "s1") + "\n",
        encoding="utf-8")

    adapter = ClaudeCodeAdapter(claude_root)
    table = load_pricing_table()

    # Indexing OFF during ingest -> sub-agent exists but no segments.
    assert repo.is_search_indexing_enabled() is False
    ingest_file(adapter, parent_file, repo, table)
    sub_id = "claude_code:parent/agent-aabc"
    assert repo.get_session(sub_id) is not None
    assert repo.count_segments_for_session(sub_id) == 0

    # Enable indexing, then run the backfill (what `argus start` does).
    repo.set_search_indexing_enabled(True)
    _backfill_missing_derived_data([adapter], repo, table)

    # The sub-agent transcript must now be indexed -> Task given is recoverable.
    assert repo.count_segments_for_session(sub_id) > 0
    assert repo._first_user_text(sub_id) == "Research consensus mechanisms"


def _asst(sid: str, mid: str, ts: str) -> str:
    return json.dumps({
        "type": "assistant", "sessionId": sid, "uuid": "u" + mid, "timestamp": ts, "cwd": "C:/proj",
        "version": "2.1.94", "userType": "external", "entrypoint": "cli",
        "message": {"id": mid, "model": "claude-opus-4-7", "role": "assistant",
                    "content": [{"type": "text", "text": "done: summarised the API docs"}],
                    "usage": {"input_tokens": 10, "output_tokens": 5,
                              "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}},
    }) + "\n"


def _prompt(sid: str, ts: str) -> str:
    return json.dumps({
        "type": "user", "sessionId": sid, "uuid": "u-prompt", "timestamp": ts, "cwd": "C:/proj",
        "isSidechain": True, "message": {"role": "user", "content": "Research the zebrafish API docs"},
    }) + "\n"


def test_prompt_only_subagent_does_not_block_the_parent(tmp_path: Path, repo):
    """REGRESSION: with search indexing on, a sub-agent file holding only its
    prompt (no reply yet) stored that prompt's segment before the sub-agent's
    session row existed -> FOREIGN KEY constraint failed. Since ingest became
    one transaction per file, that rolled back the whole PARENT tick on every
    retry, so a sub-agent cancelled before its first reply froze its parent
    session forever. 9 such errors sat in a real archive's parse_errors."""
    repo.set_search_indexing_enabled(True)
    root = tmp_path / ".claude"
    proj = root / "projects" / "C--proj"
    (proj / "s1" / "subagents").mkdir(parents=True)
    parent = proj / "s1.jsonl"
    sub = proj / "s1" / "subagents" / "agent-a.jsonl"
    parent.write_text(_asst("s1", "m1", "2026-05-01T00:00:00Z"), encoding="utf-8")
    sub.write_text(_prompt("s1", "2026-05-01T00:00:01Z"), encoding="utf-8")
    adapter, table = ClaudeCodeAdapter(root), load_pricing_table()

    ingest_file(adapter, parent, repo, table)  # must not raise

    assert repo.get_session("claude_code:s1").turn_count == 1
    assert repo.get_file_offset(str(parent)) == parent.stat().st_size
    assert repo.get_session("claude_code:s1/agent-a") is None
    assert repo.get_file_offset(str(sub)) == 0  # prompt kept for the next read
    assert repo.recent_parse_errors(10) == []

    # The sub-agent replies: its prompt is stored now, with the reply.
    with sub.open("a", encoding="utf-8") as fh:
        fh.write(_asst("s1", "m2", "2026-05-01T00:00:09Z"))
    ingest_file(adapter, parent, repo, table)

    assert repo.get_session("claude_code:s1/agent-a").turn_count == 1
    assert repo.get_file_offset(str(sub)) == sub.stat().st_size
    assert repo.count_segments_for_session("claude_code:s1/agent-a") == 2  # prompt + reply
    assert repo.get_session("claude_code:s1").turn_count == 2  # rollup includes the sub-agent
