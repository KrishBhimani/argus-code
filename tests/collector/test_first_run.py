"""Foreground (recent) vs background (older) first-pass ingest."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from argus.adapters.claude_code.adapter import ClaudeCodeAdapter
from argus.collector.first_run import run_first_pass_ingest
from argus.pricing.load import load_pricing_table


def _line(sid: str, mid: str, ts: str) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "sessionId": sid,
            "uuid": "u",
            "timestamp": ts,
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
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            },
        }
    )


def test_ingests_recent_files_in_foreground_older_in_background(tmp_path: Path, repo):
    """REGRESSION: status.processed/total reflect both phases after backfill done."""
    claude_root = tmp_path / ".claude"
    proj = claude_root / "projects" / "C--proj"
    proj.mkdir(parents=True)

    recent_file = proj / "recent.jsonl"
    old_file = proj / "old.jsonl"
    recent_file.write_text(
        _line("rec", "mr", "2026-05-22T00:00:00Z") + "\n", encoding="utf-8"
    )
    old_file.write_text(
        _line("old", "mo", "2025-01-01T00:00:00Z") + "\n", encoding="utf-8"
    )

    # Backdate old_file's mtime so it falls outside the recent-days cutoff.
    old_mtime = time.mktime((2025, 1, 1, 0, 0, 0, 0, 0, 0))
    os.utime(old_file, (old_mtime, old_mtime))

    adapter = ClaudeCodeAdapter(claude_root)
    table = load_pricing_table()

    handle = run_first_pass_ingest([adapter], repo, table, recent_days=30)

    handle.wait_foreground(timeout=10)
    ids_after_fg = sorted(s.id for s in repo.list_sessions(limit=10))
    # The foreground guarantee is that the *recent* file is queryable as soon
    # as wait_foreground() returns. We deliberately do NOT assert the old file
    # is still absent: the background thread starts immediately, so that would
    # be asserting it loses a race, which is scheduling luck rather than a
    # contract. macOS CI ingested both before this line and failed on it.
    assert "claude_code:recent" in ids_after_fg
    s = handle.status()
    assert s.total == 2

    handle.wait_backfill(timeout=10)
    ids_after_bg = sorted(s.id for s in repo.list_sessions(limit=10))
    assert ids_after_bg == ["claude_code:old", "claude_code:recent"]
    s = handle.status()
    assert s.pending == 0
    assert s.processed == 2


def test_backfill_fills_missing_tool_use_ids(tmp_path: Path, repo):
    """Pre-upgrade transcript_segments rows (tool_use_id NULL) get relinked
    by the startup backfill without wiping or re-enabling anything."""
    from argus.collector.first_run import _backfill_missing_derived_data
    from argus.collector.pipeline import ingest_file

    claude_root = tmp_path / ".claude"
    proj = claude_root / "projects" / "C--proj"
    proj.mkdir(parents=True)
    f = proj / "s1.jsonl"
    assistant = {
        "type": "assistant",
        "sessionId": "s1",
        "uuid": "ua",
        "timestamp": "2026-05-01T00:00:00Z",
        "cwd": "C:/proj",
        "version": "2.1.94",
        "userType": "external",
        "entrypoint": "cli",
        "message": {
            "id": "m1",
            "model": "claude-opus-4-7",
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "tu_x", "name": "Bash",
                 "input": {"command": "ls"}},
            ],
            "usage": {
                "input_tokens": 1,
                "output_tokens": 1,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        },
    }
    user = {
        "type": "user",
        "sessionId": "s1",
        "uuid": "ub",
        "timestamp": "2026-05-01T00:00:01Z",
        "cwd": "C:/proj",
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "tu_x",
                 "is_error": True, "content": "boom: exit 1"},
            ],
        },
    }
    f.write_text(json.dumps(assistant) + "\n" + json.dumps(user) + "\n",
                 encoding="utf-8")

    repo.set_search_indexing_enabled(True)
    adapter = ClaudeCodeAdapter(claude_root)
    table = load_pricing_table()
    ingest_file(adapter, f, repo, table)

    # Simulate rows written before the tool_use_id column existed.
    with repo.db:
        repo.db.execute("UPDATE transcript_segments SET tool_use_id = NULL")
    assert [c["id"] for c in repo.sessions_missing_tool_use_ids(10)] == ["claude_code:s1"]

    _backfill_missing_derived_data([adapter], repo, table)

    row = repo.db.execute(
        "SELECT tool_use_id FROM transcript_segments WHERE role = 'tool_result'"
    ).fetchone()
    assert row["tool_use_id"] == "tu_x"
    assert repo.sessions_missing_tool_use_ids(10) == []


def test_backfill_rereads_streamed_output_tokens_once(tmp_path: Path, repo):
    """REGRESSION: turns ingested when extract_turns took usage from the first
    streamed line hold placeholder output_tokens. The startup backfill re-reads
    every session on disk once (flagged in app_meta), including sub-agent
    files, and does not re-run on the next start."""
    from argus.collector.first_run import (
        STREAMED_OUTPUT_FIX_KEY,
        _backfill_missing_derived_data,
    )
    from argus.collector.pipeline import ingest_file

    claude_root = tmp_path / ".claude"
    proj = claude_root / "projects" / "C--proj"
    (proj / "s1" / "subagents").mkdir(parents=True)

    def line(sid: str, out: int, stop: str | None, uuid: str) -> str:
        return json.dumps(
            {
                "type": "assistant",
                "sessionId": sid,
                "uuid": uuid,
                "timestamp": "2026-05-01T00:00:00Z",
                "cwd": "C:/proj",
                "version": "2.1.94",
                "userType": "external",
                "entrypoint": "cli",
                "message": {
                    "id": "m1",
                    "model": "claude-opus-4-7",
                    "role": "assistant",
                    "stop_reason": stop,
                    "content": [
                        {"type": "tool_use", "id": f"tu_{uuid}", "name": "Bash", "input": {}}
                    ],
                    "usage": {
                        "input_tokens": 1,
                        "output_tokens": out,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                },
            }
        )

    parent = proj / "s1.jsonl"
    sub = proj / "s1" / "subagents" / "agent-a.jsonl"
    parent.write_text(line("s1", 7, None, "a") + "\n" + line("s1", 642, "tool_use", "b") + "\n",
                      encoding="utf-8")
    sub.write_text(line("s1", 3, None, "c") + "\n" + line("s1", 500, "end_turn", "d") + "\n",
                   encoding="utf-8")

    adapter = ClaudeCodeAdapter(claude_root)
    table = load_pricing_table()
    ingest_file(adapter, parent, repo, table)

    # Simulate rows written by the pre-fix extractor (first-line placeholder).
    with repo.db:
        repo.db.execute("UPDATE turns SET output_tokens = 7 WHERE session_id = 'claude_code:s1'")
        repo.db.execute("UPDATE turns SET output_tokens = 3 WHERE session_id = 'claude_code:s1/agent-a'")
        repo.db.execute("UPDATE sessions SET computed_at = '2026-01-01T00:00:00+00:00'")
    assert repo.get_app_meta(STREAMED_OUTPUT_FIX_KEY) is None

    _backfill_missing_derived_data([adapter], repo, table)

    out = {r["session_id"]: r["output_tokens"] for r in repo.db.execute(
        "SELECT session_id, output_tokens FROM turns")}
    assert out == {"claude_code:s1": 642, "claude_code:s1/agent-a": 500}
    assert repo.get_app_meta(STREAMED_OUTPUT_FIX_KEY) == "1"

    # Second start: nothing is re-read. A re-ingest recomputes the session and
    # bumps computed_at, so an unchanged computed_at proves it didn't run.
    computed = lambda: {r["id"]: r["computed_at"] for r in repo.db.execute(  # noqa: E731
        "SELECT id, computed_at FROM sessions")}
    before = computed()
    _backfill_missing_derived_data([adapter], repo, table)
    assert computed() == before


def test_streamed_output_fix_is_marked_done_on_a_fresh_db(tmp_path: Path, repo):
    """A fresh install has nothing to correct: the one-shot flag is set before
    the first ingest so the files it just read aren't re-read next start."""
    from argus.collector.first_run import STREAMED_OUTPUT_FIX_KEY

    claude_root = tmp_path / ".claude"
    proj = claude_root / "projects" / "C--proj"
    proj.mkdir(parents=True)
    (proj / "s1.jsonl").write_text(
        _line("s1", "m1", "2026-05-22T00:00:00Z") + "\n", encoding="utf-8"
    )
    handle = run_first_pass_ingest([ClaudeCodeAdapter(claude_root)], repo,
                                   load_pricing_table(), recent_days=30)
    handle.wait_backfill(timeout=10)
    handle.join(timeout=10)
    assert repo.get_app_meta(STREAMED_OUTPUT_FIX_KEY) == "1"
    assert repo.get_app_meta(STREAMED_OUTPUT_FIX_KEY + "_started_at") is None


def test_backfill_reprices_zero_cost_turns(tmp_path: Path, repo):
    """Turns ingested before their model was in the pricing table (cost 0)
    get repriced by the startup backfill once the table knows the model."""
    from argus.collector.first_run import _backfill_missing_derived_data
    from argus.collector.pipeline import ingest_file

    claude_root = tmp_path / ".claude"
    proj = claude_root / "projects" / "C--proj"
    proj.mkdir(parents=True)
    f = proj / "s1.jsonl"
    line = {
        "type": "assistant",
        "sessionId": "s1",
        "uuid": "ua",
        "timestamp": "2026-05-01T00:00:00Z",
        "cwd": "C:/proj",
        "version": "2.1.94",
        "userType": "external",
        "entrypoint": "cli",
        "message": {
            "id": "m1",
            "model": "claude-fable-5",
            "role": "assistant",
            # The tool_use matters: it keeps sessions_missing_tool_calls from
            # coincidentally re-ingesting this session, so the test exercises
            # the repricing path specifically.
            "content": [
                {"type": "text", "text": "hi"},
                {"type": "tool_use", "id": "tu_1", "name": "Bash", "input": {}},
            ],
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 2000,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        },
    }
    f.write_text(json.dumps(line) + "\n", encoding="utf-8")

    adapter = ClaudeCodeAdapter(claude_root)
    table = load_pricing_table()
    ingest_file(adapter, f, repo, table)

    # Simulate rows ingested under a pre-fable pricing table.
    with repo.db:
        repo.db.execute("UPDATE turns SET cost_usd = 0")
        repo.db.execute("UPDATE sessions SET total_cost_usd = 0")
    assert [c["id"] for c in repo.sessions_with_unpriced_turns(
        list(table.models.keys()), 10)] == ["claude_code:s1"]

    _backfill_missing_derived_data([adapter], repo, table)

    session = repo.get_session("claude_code:s1")
    # 1000 in * $10/M + 2000 out * $50/M = 0.01 + 0.10
    assert abs(session.total_cost_usd - 0.11) < 1e-9
    assert repo.sessions_with_unpriced_turns(list(table.models.keys()), 10) == []


def test_backfill_reprices_claude_opus_5_turns_after_table_upgrade(tmp_path: Path, repo):
    """REGRESSION (H3): claude-opus-5 turns ingested under the 2026-06-12 table
    (which lacked the model) are $0; the next start with the newer bundled
    table re-prices them through sessions_with_unpriced_turns."""
    from argus.collector.first_run import _backfill_missing_derived_data
    from argus.collector.pipeline import ingest_file
    from argus.pricing.load import _bundled_dir

    claude_root = tmp_path / ".claude"
    proj = claude_root / "projects" / "C--proj"
    proj.mkdir(parents=True)
    f = proj / "s1.jsonl"
    line = json.loads(_line("s1", "m1", "2026-09-01T00:00:00Z"))
    line["message"]["model"] = "claude-opus-5"
    line["message"]["content"] = [{"type": "tool_use", "id": "tu_1", "name": "Bash", "input": {}}]
    line["message"]["usage"].update(input_tokens=1000, output_tokens=2000)
    f.write_text(json.dumps(line) + "\n", encoding="utf-8")
    adapter = ClaudeCodeAdapter(claude_root)

    old = load_pricing_table(_bundled_dir() / "2026-06-12.json")
    ingest_file(adapter, f, repo, old)
    assert repo.get_session("claude_code:s1").total_cost_usd == 0

    _backfill_missing_derived_data([adapter], repo, load_pricing_table())
    # 1000 in * $5/M + 2000 out * $25/M = 0.005 + 0.05
    assert abs(repo.get_session("claude_code:s1").total_cost_usd - 0.055) < 1e-9


def _opus5_line(sid: str, mid: str, ts: str, inp: int, out: int) -> str:
    line = json.loads(_line(sid, mid, ts))
    line["message"]["model"] = "claude-opus-5"
    line["message"]["usage"].update(input_tokens=inp, output_tokens=out)
    return json.dumps(line) + "\n"


def test_reprices_zero_cost_turns_whose_transcript_is_gone(tmp_path: Path, repo):
    """REGRESSION: a real archive held 645 claude-opus-5 turns ($105.71) at $0,
    all from transcripts Claude Code had already deleted. Re-pricing by
    re-reading the file can't reach them, but the turns keep their token
    counts, so the cost is recomputed in the DB, sub-agent rollup included."""
    from argus.pricing.load import _bundled_dir

    claude_root = tmp_path / ".claude"
    proj = claude_root / "projects" / "C--proj"
    (proj / "s1" / "subagents").mkdir(parents=True)
    parent = proj / "s1.jsonl"
    sub = proj / "s1" / "subagents" / "agent-a.jsonl"
    parent.write_text(_opus5_line("s1", "m1", "2026-09-01T00:00:00Z", 1000, 2000), encoding="utf-8")
    sub.write_text(_opus5_line("s1", "m2", "2026-09-01T00:00:05Z", 2000, 4000), encoding="utf-8")
    adapter = ClaudeCodeAdapter(claude_root)

    old = load_pricing_table(_bundled_dir() / "2026-06-12.json")  # lacks claude-opus-5
    from argus.collector.pipeline import ingest_file

    ingest_file(adapter, parent, repo, old)
    assert repo.get_session("claude_code:s1").total_cost_usd == 0
    sub.unlink()
    parent.unlink()  # Claude Code's cleanup: only the archive remains

    handle = run_first_pass_ingest([adapter], repo, load_pricing_table())
    assert handle.join(timeout=30)

    # parent: 1000*$5/M + 2000*$25/M = 0.055; sub: 2000*$5/M + 4000*$25/M = 0.11
    assert abs(repo.get_session("claude_code:s1/agent-a").total_cost_usd - 0.11) < 1e-9
    assert abs(repo.get_session("claude_code:s1").total_cost_usd - (0.055 + 0.11)) < 1e-9
    assert repo.sessions_with_unpriced_turns(list(load_pricing_table().models), 10) == []


def test_placeholder_model_does_not_warn(caplog):
    """`<synthetic>` is Claude Code's local placeholder (e.g. "No response
    requested."), not a model a pricing table could ever list."""
    import logging

    from argus.pricing import compute

    compute._warned_unknown.clear()
    with caplog.at_level(logging.WARNING, logger="argus.pricing"):
        cost = compute.compute_turn_cost({"model": "<synthetic>", "output_tokens": 0}, load_pricing_table())
    assert cost == 0.0
    assert not [r for r in caplog.records if "No price" in r.getMessage()]
