"""Forked Claude sessions must not re-count the turns they copied (H2).

A forked transcript (``sessionKind: "bg"``) begins with verbatim copies of its
parent's lines: same line ``uuid``, same ``message.id``, ``sessionId`` rewritten
to the fork, and the original id kept in a ``session_id`` field. Turns are keyed
``{session_id}:{message.id}``, so before the fix every copy became a new turn of
the fork — one real fork re-counted ~24.8M cache-read tokens and 110 tool calls.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus.adapters.claude_code.adapter import ClaudeCodeAdapter
from argus.collector.pipeline import ingest_file
from argus.pricing.load import load_pricing_table

PARENT, FORK = "parent-1", "fork-1"


def _asst(sid: str, mid: str, ts: str, uuid: str, *, tool: str | None = None,
          origin: str | None = None) -> dict:
    line = {
        "type": "assistant", "sessionId": sid, "uuid": uuid, "timestamp": ts,
        "cwd": "/proj", "version": "2.1.94",
        "message": {
            "id": mid, "model": "claude-opus-4-7", "role": "assistant",
            "content": [{"type": "tool_use", "id": tool, "name": "Bash", "input": {}}]
            if tool else [{"type": "text", "text": "x"}],
            "usage": {"input_tokens": 100, "output_tokens": 10,
                      "cache_read_input_tokens": 1_000_000, "cache_creation_input_tokens": 0},
        },
    }
    if origin is not None:
        line["session_id"] = origin
        line["sessionKind"] = "bg"
    return line


def _parent_lines() -> list[dict]:
    return [
        _asst(PARENT, "m1", "2026-05-01T10:00:00.000Z", "u1", tool="t1"),
        _asst(PARENT, "m2", "2026-05-01T10:05:00.000Z", "u2"),
    ]


def _fork_lines() -> list[dict]:
    copies = [{**ln, "sessionId": FORK, "session_id": PARENT, "sessionKind": "bg"}
              for ln in _parent_lines()]
    own = [_asst(FORK, "f1", "2026-05-02T09:00:00.000Z", "u9", tool="t9")]
    return copies + own


def _setup(tmp_path: Path):
    root = tmp_path / ".claude"
    proj = root / "projects" / "-proj"
    proj.mkdir(parents=True)
    parent, fork = proj / f"{PARENT}.jsonl", proj / f"{FORK}.jsonl"
    parent.write_text("".join(json.dumps(x) + "\n" for x in _parent_lines()), encoding="utf-8")
    fork.write_text("".join(json.dumps(x) + "\n" for x in _fork_lines()), encoding="utf-8")
    return ClaudeCodeAdapter(root), parent, fork


def _rows(repo, sid: str) -> tuple[list[str], list[str]]:
    turns = [r[0].rsplit(":", 1)[1] for r in repo.db.execute(
        "SELECT id FROM turns WHERE session_id = ? ORDER BY id", (sid,))]
    calls = [r[0].rsplit(":", 1)[1] for r in repo.db.execute(
        "SELECT id FROM tool_calls WHERE session_id = ? ORDER BY id", (sid,))]
    return turns, calls


def _assert_deduped(repo) -> None:
    assert _rows(repo, f"claude_code:{PARENT}") == (["m1", "m2"], ["t1"])
    assert _rows(repo, f"claude_code:{FORK}") == (["f1"], ["t9"])
    fork = repo.get_session(f"claude_code:{FORK}")
    assert fork.turn_count == 1
    assert fork.total_cache_read_tokens == 1_000_000
    assert fork.started_at == "2026-05-02T09:00:00.000Z"  # not the parent's start


def test_fork_ingested_after_parent_skips_copied_turns(tmp_path: Path, repo) -> None:
    adapter, parent, fork = _setup(tmp_path)
    table = load_pricing_table()
    ingest_file(adapter, parent, repo, table)
    ingest_file(adapter, fork, repo, table)
    _assert_deduped(repo)


def test_fork_ingested_before_parent_is_reclaimed(tmp_path: Path, repo) -> None:
    """Order-independent: when the origin session is ingested later, the fork's
    copies are handed back and the fork's totals recomputed."""
    adapter, parent, fork = _setup(tmp_path)
    table = load_pricing_table()
    ingest_file(adapter, fork, repo, table)
    ingest_file(adapter, parent, repo, table)
    _assert_deduped(repo)


def test_session_id_field_alone_does_not_drop_a_turn(tmp_path: Path, repo) -> None:
    """Real non-copied lines can carry a differing session_id; a turn is only a
    copy if its message is actually stored under that origin session."""
    root = tmp_path / ".claude"
    proj = root / "projects" / "-proj"
    proj.mkdir(parents=True)
    f = proj / "solo.jsonl"
    f.write_text(json.dumps(_asst("solo", "s1", "2026-05-01T00:00:00.000Z", "u1",
                                  origin="someone-else")) + "\n", encoding="utf-8")
    ingest_file(ClaudeCodeAdapter(root), f, repo, load_pricing_table())
    assert _rows(repo, "claude_code:solo")[0] == ["s1"]


def test_repair_removes_existing_fork_duplicates_once(tmp_path: Path, repo) -> None:
    """Archives ingested before the fix hold the copies under the fork. The
    one-shot repair deletes only those duplicated derived rows and recomputes."""
    from argus.collector.first_run import FORK_DEDUP_REPAIR_KEY, _repair_fork_duplicates_once

    adapter, parent, fork = _setup(tmp_path)
    table = load_pricing_table()
    ingest_file(adapter, parent, repo, table)
    ingest_file(adapter, fork, repo, table)
    # Re-create the pre-fix state: copies stored under the fork.
    repo.db.execute(
        "INSERT INTO turns SELECT replace(id, 'claude_code:parent-1', 'claude_code:fork-1'),"
        " 'claude_code:fork-1', sequence, timestamp, model, model_raw, fresh_input_tokens,"
        " output_tokens, cache_read_tokens, cache_write_tokens, cache_write_5m_tokens,"
        " cache_write_1h_tokens, tool_calls_count, cost_usd, '{}'"
        " FROM turns WHERE session_id = 'claude_code:parent-1'")
    repo.db.execute(
        "INSERT INTO tool_calls (id, session_id, turn_index, tool_name, is_error, input_size,"
        " subagent_type, timestamp) SELECT replace(id, 'parent-1', 'fork-1'),"
        " 'claude_code:fork-1', turn_index, tool_name, is_error, input_size, subagent_type,"
        " timestamp FROM tool_calls WHERE session_id = 'claude_code:parent-1'")
    repo.db.execute("UPDATE sessions SET turn_count = 3, started_at = '2026-05-01T10:00:00.000Z'"
                    " WHERE id = 'claude_code:fork-1'")

    _repair_fork_duplicates_once([adapter], repo, table)
    _assert_deduped(repo)
    assert repo.get_app_meta(FORK_DEDUP_REPAIR_KEY) == "1"

    before = [tuple(r) for r in repo.db.execute("SELECT * FROM turns ORDER BY id")]
    repo.set_app_meta(FORK_DEDUP_REPAIR_KEY, "0")
    _repair_fork_duplicates_once([adapter], repo, table)  # idempotent
    assert [tuple(r) for r in repo.db.execute("SELECT * FROM turns ORDER BY id")] == before
