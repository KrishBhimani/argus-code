"""Codex through the real pipeline: parent/child rollup, idempotence, backfill by native id."""
from __future__ import annotations

import json
from pathlib import Path

from argus.adapters.codex.adapter import CodexAdapter
from argus.collector.first_run import _backfill_missing_derived_data
from argus.collector.pipeline import ingest_file
from argus.pricing.load import load_pricing_table
from argus.store.repository import normalize_project_path

TS0 = "2026-09-01T10:00:00.000Z"
THREAD = "019a0000-0000-7000-8000-000000000001"
CHILD = "019a0000-0000-7000-8000-000000000002"


def env(kind: str, payload: dict, ts: str = TS0, ordinal: int | None = None) -> dict:
    line = {"timestamp": ts, "type": kind, "payload": payload}
    if ordinal is not None:
        line["ordinal"] = ordinal
    return line


def meta(thread: str = THREAD, ts: str = TS0, **extra) -> dict:
    payload = {
        "id": thread,
        "timestamp": ts,
        "cwd": "C:\\proj",
        "originator": "codex_cli_rs",
        "cli_version": "0.155.0",
        "source": "cli",
        "model_provider": "openai",
    }
    payload.update(extra)
    return env("session_meta", payload, ts)


def ctx(model: str = "gpt-5.5", ts: str = TS0, **extra) -> dict:
    payload = {"cwd": "C:\\proj", "approval_policy": "on-request", "model": model, "effort": "medium", "summary": "auto"}
    payload.update(extra)
    return env("turn_context", payload, ts)


def tc(total: dict, last: dict | None, ts: str = TS0) -> dict:
    info = {"total_token_usage": total}
    if last is not None:
        info["last_token_usage"] = last
    return env("event_msg", {"type": "token_count", "info": info, "rate_limits": None}, ts)


def usage(inp: int, cached: int = 0, out: int = 0, reasoning: int = 0, cw: int | None = None) -> dict:
    u = {"input_tokens": inp, "cached_input_tokens": cached, "output_tokens": out, "reasoning_output_tokens": reasoning, "total_tokens": inp + out}
    if cw is not None:
        u["cache_write_input_tokens"] = cw
    return u


def fc(name: str, args: dict, call_id: str, ts: str = TS0, namespace: str | None = None) -> dict:
    payload = {"type": "function_call", "name": name, "arguments": json.dumps(args), "call_id": call_id}
    if namespace:
        payload["namespace"] = namespace
    return env("response_item", payload, ts)


def jsonl(lines: list[dict]) -> str:
    return "\n".join(json.dumps(l) for l in lines) + "\n"


def _day(tmp_path: Path) -> Path:
    day = tmp_path / ".codex" / "sessions" / "2026" / "09" / "01"
    day.mkdir(parents=True)
    return day


def test_codex_parent_child_rollup_through_pipeline(tmp_path: Path, repo):
    """Codex child threads are separate files; the parent rollup must sum them
    once, expose them on the Sub-agents tab, and the watcher must skip them."""
    day = _day(tmp_path)
    parent = day / f"rollout-2026-09-01T10-00-00-{THREAD}.jsonl"
    child = day / f"rollout-2026-09-01T10-00-01-{CHILD}.jsonl"
    up = usage(1_000_000, out=1_000_000)
    uc = usage(500_000, out=500_000)
    parent.write_text(
        jsonl([meta(), ctx("gpt-5.5"), fc("spawn_agent", {"agent_type": "worker"}, "sp1", namespace="collaboration"), tc(up, up)]),
        encoding="utf-8", newline="\n",
    )
    child.write_text(
        jsonl([
            meta(CHILD, parent_thread_id=THREAD, subagent_history_start_ordinal=0),
            env("turn_context", {"model": "gpt-5.5"}, ordinal=0),
            env("event_msg", {"type": "token_count", "info": {"total_token_usage": uc, "last_token_usage": uc}}, ordinal=1),
        ]),
        encoding="utf-8", newline="\n",
    )

    adapter = CodexAdapter(tmp_path / ".codex")
    table = load_pricing_table()
    assert adapter.discover_session_files() == [parent]
    assert adapter.should_skip(child)
    ingest_file(adapter, parent, repo, table)

    p = repo.get_session(f"codex:{THREAD}")
    c = repo.get_session(f"codex:{THREAD}/{CHILD}")
    assert p is not None and c is not None
    assert p.turn_count == 2 and p.total_fresh_input_tokens == 1_500_000
    assert p.total_cost_usd > c.total_cost_usd > 0
    assert p.metadata["sub_agent_session_ids"] == [c.id]
    assert p.project_path == normalize_project_path("C:\\proj")
    assert p.agent == "codex" and p.agent_version == "0.155.0" and p.primary_model == "gpt-5.5"
    subs = repo.subagent_summaries(p.id)
    assert len(subs) == 1 and subs[0]["id"] == c.id
    calls = repo.db.execute(
        "SELECT tool_name, subagent_type FROM tool_calls WHERE session_id = ?", (p.id,)
    ).fetchall()
    assert [(r["tool_name"], r["subagent_type"]) for r in calls] == [("collaboration__spawn_agent", "worker")]

    # Re-ingest with nothing new: idempotent.
    ingest_file(adapter, parent, repo, table)
    again = repo.get_session(p.id)
    assert again.total_cost_usd == p.total_cost_usd and again.turn_count == 2


def test_codex_backfill_finds_file_by_native_id(tmp_path: Path, repo):
    day = _day(tmp_path)
    f = day / f"rollout-2026-09-01T10-00-00-{THREAD}.jsonl"
    u = usage(10, out=2)
    f.write_text(jsonl([meta(), ctx(), fc("shell", {}, "c1"), tc(u, u)]), encoding="utf-8", newline="\n")
    adapter = CodexAdapter(tmp_path / ".codex")
    table = load_pricing_table()
    ingest_file(adapter, f, repo, table)
    repo.db.execute("DELETE FROM tool_calls")  # simulate an archive row from before tool_calls existed
    assert repo.db.execute("SELECT COUNT(*) AS n FROM tool_calls").fetchone()["n"] == 0
    _backfill_missing_derived_data([adapter], repo, table)
    assert repo.db.execute("SELECT COUNT(*) AS n FROM tool_calls").fetchone()["n"] == 1


def test_codex_stub_session_creates_no_row(tmp_path: Path, repo):
    """Real-world shape: a prompt typed with no model response yet. No turns,
    so no session row (mirrors the Claude rule), but the offset advances."""
    day = _day(tmp_path)
    f = day / f"rollout-2026-09-01T10-00-00-{THREAD}.jsonl"
    f.write_text(
        jsonl([meta(), env("event_msg", {"type": "user_message", "kind": "plain", "message": "hi"}), ctx()]),
        encoding="utf-8", newline="\n",
    )
    adapter = CodexAdapter(tmp_path / ".codex")
    ingest_file(adapter, f, repo, load_pricing_table())
    assert repo.get_session(f"codex:{THREAD}") is None
    assert repo.get_file_offset(str(f)) > 0
