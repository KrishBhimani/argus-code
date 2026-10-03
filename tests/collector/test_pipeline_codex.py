"""Codex thread structure through the real pipeline."""
from __future__ import annotations

import json
from pathlib import Path

from argus.adapters.codex.adapter import CodexAdapter
from argus.collector.pipeline import ingest_file
from argus.pricing.load import load_pricing_table
from argus.store.repository import Repository

P, C = "01a10342-0000-7000-8000-000000000001", "01a10364-0000-7000-8000-000000000002"


def E(o, t, p, ts="2026-10-03T20:00:00.000Z"):
    return json.dumps({"timestamp": ts, "ordinal": o, "type": t, "payload": p})


def TUR(o, rid, thread, out=10):
    return E(o, "token_usage_record", {"response_id": rid, "turn_id": "U", "thread_id": thread,
                                       "usage": {"input_tokens": 100, "cached_input_tokens": 0, "output_tokens": out}})


def _w(p: Path, lines: list[str]) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(l + "\n" for l in lines), encoding="utf-8")
    return p


# `repo` is the shared conftest fixture (real on-disk DB, closed at teardown).


def test_subagent_rolls_up_and_copied_history_is_skipped(tmp_path: Path, repo: Repository) -> None:
    root = tmp_path / ".codex"
    day = root / "sessions" / "2026" / "10" / "04"
    parent = _w(day / f"rollout-1-{P}.jsonl", [
        E(0, "session_meta", {"id": P, "cwd": "/p"}),
        E(1, "turn_context", {"turn_id": "U", "model": "gpt-6.1-sol"}),
        TUR(2, "rp1", P),
    ])
    _w(day / f"rollout-2-{C}.jsonl", [
        E(0, "session_meta", {"id": C, "parent_thread_id": P, "subagent_history_start_ordinal": 3,
                              "agent_nickname": "Euclid", "thread_source": "subagent",
                              "source": {"subagent": {"thread_spawn": {"parent_thread_id": P, "depth": 1}}}}),
        E(1, "session_meta", {"id": P}),          # copied parent header
        TUR(2, "rp1", P),                          # hypothetical copied usage: must not count
        E(3, "turn_context", {"turn_id": "U", "model": "gpt-6-luna"}),
        E(4, "response_item", {"type": "agent_message", "id": "am",
                                "content": [{"type": "input_text", "text": "the task"}]}),
        TUR(5, "rc1", C, out=5),
    ])
    a, table = CodexAdapter(root), load_pricing_table()
    assert a.discover_session_files() == [parent]
    ingest_file(a, parent, repo, table)
    sub = repo.get_session(f"codex:{P}/{C}")
    assert sub is not None and sub.turn_count == 1 and sub.primary_model == "gpt-6-luna"
    assert sub.metadata["agent_nickname"] == "Euclid" and sub.metadata["depth"] == 1
    top = repo.get_session(f"codex:{P}")
    assert top.metadata.get("sub_agent_session_ids") == [f"codex:{P}/{C}"]
    assert [t.id for t in repo.get_turns_for_session(f"codex:{P}")] == [f"codex:{P}:rp1"]


def test_subagent_growth_after_parent_goes_quiet_is_ingested(tmp_path: Path, repo: Repository) -> None:
    """REGRESSION (PR #52 review): a read of the parent with no new lines must
    still report the thread id, so the pipeline finds the session and
    reconciles its sub-agents. It reported the file stem instead, so a child
    that kept writing after its parent stopped (child fs events are skipped
    by design) was never re-read -- not even on later startups."""
    root = tmp_path / ".codex"
    day = root / "sessions" / "2026" / "10" / "04"
    parent = _w(day / f"rollout-1-{P}.jsonl", [E(0, "session_meta", {"id": P, "cwd": "/p"}), TUR(1, "rp1", P)])
    child = _w(day / f"rollout-2-{C}.jsonl",
               [E(0, "session_meta", {"id": C, "parent_thread_id": P}), TUR(1, "rc1", C)])
    table = load_pricing_table()
    ingest_file(CodexAdapter(root), parent, repo, table)
    assert repo.get_session(f"codex:{P}/{C}").turn_count == 1
    with child.open("a", encoding="utf-8") as fh:
        fh.write(TUR(2, "rc2", C) + "\n")
    ingest_file(CodexAdapter(root), parent, repo, table)  # e.g. the next startup's first pass
    assert repo.get_session(f"codex:{P}/{C}").turn_count == 2
    assert repo.get_session(f"codex:{P}").turn_count == 3  # rollup follows


def test_continuation_segment_merges_into_one_session(tmp_path: Path, repo: Repository) -> None:
    root = tmp_path / ".codex"
    day = root / "sessions" / "2026" / "10" / "02"
    a_file = _w(day / f"rollout-1-{P}.jsonl", [E(0, "session_meta", {"id": P, "cwd": "/p"}),
                                              E(1, "event_msg", {"type": "turn_aborted"})])
    b_file = _w(day / f"rollout-2-{P}_{C}.jsonl", [E(0, "session_meta", {"id": P, "cwd": "/p"}),
                                                  TUR(1, "r1", P), TUR(2, "r2", P)])
    a, table = CodexAdapter(root), load_pricing_table()
    files = a.discover_session_files()
    assert files == sorted([a_file, b_file])
    for f in files:
        ingest_file(a, f, repo, table)
    rows = repo.db.execute("SELECT id, turn_count FROM sessions").fetchall()
    assert [tuple(r) for r in rows] == [(f"codex:{P}", 2)]


def test_archive_move_does_not_double_count(tmp_path: Path, repo: Repository) -> None:
    """Review focus #3."""
    root = tmp_path / ".codex"
    src = _w(root / "sessions" / "2026" / "10" / "02" / f"rollout-1-{P}.jsonl",
             [E(0, "session_meta", {"id": P, "cwd": "/p"}), TUR(1, "r1", P)])
    a, table = CodexAdapter(root), load_pricing_table()
    ingest_file(a, src, repo, table)
    before = repo.get_session(f"codex:{P}")
    dst = root / "archived_sessions" / src.name
    dst.parent.mkdir(parents=True)
    src.rename(dst)
    assert a.discover_session_files() == [dst]
    ingest_file(a, dst, repo, table)
    after = repo.get_session(f"codex:{P}")
    assert (after.turn_count, after.total_output_tokens) == (before.turn_count, before.total_output_tokens)


def test_claude_and_codex_side_by_side(tmp_path: Path, repo: Repository) -> None:
    """Both adapters present: each session keeps its agent; ids never clash."""
    from tests.conftest import assistant_line
    from argus.adapters.claude_code.adapter import ClaudeCodeAdapter

    croot = tmp_path / ".claude"
    cfile = croot / "projects" / "p" / "s1.jsonl"
    cfile.parent.mkdir(parents=True)
    cfile.write_text(json.dumps(assistant_line("m1", 1)) + "\n", encoding="utf-8")
    xroot = tmp_path / ".codex"
    xfile = _w(xroot / "sessions" / f"rollout-1-{P}.jsonl",
               [E(0, "session_meta", {"id": P, "cwd": "/p"}), TUR(1, "r1", P)])
    table = load_pricing_table()
    ingest_file(ClaudeCodeAdapter(croot), cfile, repo, table)
    ingest_file(CodexAdapter(xroot), xfile, repo, table)
    agents = dict(repo.db.execute("SELECT id, agent FROM sessions").fetchall())
    assert agents == {"claude_code:s1": "claude_code", f"codex:{P}": "codex"}
