"""One-shot repairs for rows written by the pre-fix incremental ingest (H1)."""
from __future__ import annotations

import json
from pathlib import Path

from argus.adapters.claude_code.adapter import ClaudeCodeAdapter
from argus.collector import first_run
from argus.collector.first_run import (
    SESSION_DURATION_REPAIR_KEY,
    TOOL_ERRORS_FIX_KEY,
    _backfill_missing_derived_data,
    run_first_pass_ingest,
)
from argus.collector.pipeline import ingest_file
from argus.pricing.load import load_pricing_table
from tests.conftest import session_factory


def _asst(sid: str, mid: str, ts: str, tool_id: str) -> str:
    return json.dumps({
        "type": "assistant", "sessionId": sid, "uuid": f"a-{mid}", "timestamp": ts,
        "cwd": "/proj", "version": "2.1.94",
        "message": {
            "id": mid, "model": "claude-opus-4-7", "role": "assistant",
            "content": [{"type": "tool_use", "id": tool_id, "name": "Bash", "input": {}}],
            "usage": {"input_tokens": 1, "output_tokens": 1,
                      "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
        },
    })


def _err(sid: str, tool_id: str, ts: str) -> str:
    return json.dumps({
        "type": "user", "sessionId": sid, "uuid": f"r-{tool_id}", "timestamp": ts, "cwd": "/proj",
        "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tool_id, "is_error": True, "content": "boom"}]},
    })


def test_session_duration_repair_fixes_inconsistent_rows_once(repo):
    """REGRESSION (H1a): a late tick overwrote duration_sec / started_at_ms from
    its own first line while started_at (insert-only) stayed right."""
    repo.upsert_session(session_factory("claude_code:a", "2026-04-01T00:00:00Z"))
    repo.db.execute(
        "UPDATE sessions SET ended_at = '2026-04-30T00:00:03Z', duration_sec = 3,"
        " started_at_ms = 1777507200000 WHERE id = 'claude_code:a'"
    )
    run_first_pass_ingest([], repo, load_pricing_table()).join(timeout=10)

    row = repo.db.execute(
        "SELECT duration_sec, started_at_ms, ended_at_ms FROM sessions").fetchone()
    assert row["duration_sec"] == 29 * 86_400 + 3
    assert row["started_at_ms"] == 1775001600000
    assert row["ended_at_ms"] == 1775001600000 + (29 * 86_400 + 3) * 1000
    assert repo.get_app_meta(SESSION_DURATION_REPAIR_KEY) == "1"
    # Idempotent: a second pass has nothing left to change.
    assert repo.repair_session_time_columns() == 0


def _two_sessions(tmp_path: Path, repo):
    root = tmp_path / ".claude"
    proj = root / "projects" / "-proj"
    proj.mkdir(parents=True)
    files = []
    for sid in ("s1", "s2"):
        f = proj / f"{sid}.jsonl"
        f.write_text(
            _asst(sid, f"m-{sid}", "2026-05-01T00:00:00Z", f"t-{sid}") + "\n"
            + _err(sid, f"t-{sid}", "2026-05-01T00:00:30Z") + "\n",
            encoding="utf-8",
        )
        files.append(f)
    adapter = ClaudeCodeAdapter(root)
    table = load_pricing_table()
    for f in files:
        ingest_file(adapter, f, repo, table)
    # Simulate the pre-fix archive: the error landed in a later tick and was lost.
    repo.db.execute("UPDATE tool_calls SET is_error = 0")
    repo.db.execute("UPDATE sessions SET computed_at = '2026-01-01T00:00:00+00:00'")
    return adapter, table


def test_tool_error_sweep_rereads_sessions_once(tmp_path: Path, repo):
    """REGRESSION (H1b): historical calls whose error arrived in a later tick
    are stored as successes; a one-shot re-read restores the flags."""
    adapter, table = _two_sessions(tmp_path, repo)

    _backfill_missing_derived_data([adapter], repo, table)

    errs = [r[0] for r in repo.db.execute("SELECT is_error FROM tool_calls")]
    assert errs == [1, 1]
    assert repo.get_app_meta(TOOL_ERRORS_FIX_KEY) == "1"


def test_tool_error_sweep_flag_waits_until_every_session_was_reread(
    tmp_path: Path, repo, monkeypatch
):
    """The flag must only flip once the sweep actually finished — with a per-run
    cap smaller than the candidate set it takes two starts, not one."""
    adapter, table = _two_sessions(tmp_path, repo)
    monkeypatch.setattr(first_run, "BACKFILL_CAP", 1)

    _backfill_missing_derived_data([adapter], repo, table)
    assert repo.get_app_meta(TOOL_ERRORS_FIX_KEY) is None
    assert sorted(r[0] for r in repo.db.execute("SELECT is_error FROM tool_calls")) == [0, 1]

    _backfill_missing_derived_data([adapter], repo, table)
    assert repo.get_app_meta(TOOL_ERRORS_FIX_KEY) == "1"
    assert [r[0] for r in repo.db.execute("SELECT is_error FROM tool_calls")] == [1, 1]
