"""Shared pytest fixtures and synthetic-line factories."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from argus.schema.types import Alert, Session, Turn
from argus.store.db import open_db
from argus.store.repository import Repository
from argus.collector.first_run import join_first_run_threads
from argus.collector.search_backfill import join_search_backfill_threads


# ─── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
def db(db_path: Path):
    conn = open_db(db_path)
    yield conn
    # First-run's background phase writes to this connection. Closing it while
    # that thread is mid-write is undefined behaviour — it segfaulted CI (exit
    # 139), taking the whole pytest process down instead of failing a test.
    # The guard lives here, at the one place that closes the connection, so a
    # test that starts first-run cannot reintroduce the race by forgetting to
    # wait. CoreRuntime.stop() does the equivalent for production.
    # The search-index backfill writes to it too (same hazard).
    stuck = join_first_run_threads(timeout=10) + join_search_backfill_threads(timeout=10)
    conn.close()
    assert not stuck, f"first-run thread still running at teardown: {stuck}"


@pytest.fixture
def repo(db) -> Repository:
    return Repository(db)


# ─── Factories ────────────────────────────────────────────────────────


def assistant_line(
    msg_id: str,
    seq: int,
    usage: dict | None = None,
    content_type: str = "text",
    *,
    sid: str = "s1",
    cwd: str = "/p",
    version: str = "2.1.94",
) -> dict[str, Any]:
    """Synthetic Claude Code assistant JSONL line."""
    if usage is None:
        usage = {
            "input_tokens": 10,
            "output_tokens": 20,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }
    return {
        "type": "assistant",
        "sessionId": sid,
        "uuid": f"u{seq}",
        "timestamp": f"2026-05-01T00:00:0{seq}Z",
        "cwd": cwd,
        "version": version,
        "userType": "external",
        "entrypoint": "cli",
        "message": {
            "id": msg_id,
            "model": "claude-opus-4-7",
            "role": "assistant",
            "content": [{"type": content_type, "text": "x"}],
            "usage": usage,
        },
    }


def user_tool_result(tool_use_id: str, is_error: bool, seq: int) -> dict[str, Any]:
    """Synthetic Claude Code user-line carrying a tool_result block."""
    return {
        "type": "user",
        "sessionId": "s1",
        "uuid": f"u{seq}",
        "timestamp": f"2026-05-01T00:00:1{seq}Z",
        "cwd": "/p",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "is_error": is_error,
                    "content": "x",
                }
            ],
        },
    }


def session_factory(
    sid: str,
    ts: str,
    *,
    agent: str = "claude_code",
    cost: float = 1.5,
) -> Session:
    """Synthetic Session row for API/repository tests."""
    return Session(
        id=sid,
        agent=agent,
        agent_version="2.1.94",
        project_path="/p",
        started_at=ts,
        ended_at=ts,
        duration_sec=0,
        total_fresh_input_tokens=100,
        total_output_tokens=200,
        total_cache_read_tokens=0,
        total_cache_write_tokens=0,
        total_cost_usd=cost,
        primary_model="claude-opus-4-7",
        turn_count=1,
        pricing_table_version="2026-05-02",
        computed_at=ts,
        agent_reported_cost_usd=None,
        metadata={},
    )


def turn_factory(
    tid: str,
    sid: str,
    ts: str,
    *,
    cost: float = 1.5,
    fresh: int = 100,
    output: int = 200,
    cache_read: int = 0,
    cache_write: int = 0,
    model: str = "claude-opus-4-7",
) -> Turn:
    return Turn(
        id=tid,
        session_id=sid,
        sequence=0,
        timestamp=ts,
        model=model,
        model_raw=model,
        fresh_input_tokens=fresh,
        output_tokens=output,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        cache_write_5m_tokens=None,
        cache_write_1h_tokens=None,
        tool_calls_count=0,
        cost_usd=cost,
        metadata={},
    )


def alert_factory(
    *,
    detector: str = "tool_error_rate_spike",
    dedup_key: str = "Bash",
    severity: str = "warning",
    title: str = "Bash error rate jumped to 11.6% this week (baseline 4.8%)",
    message: str = "Last 7d: 11.6% over 120 calls. Prior 28d: 4.8% over 450 calls.",
    metadata: dict | None = None,
    first_seen_at: str = "2026-05-27T12:00:00Z",
    last_seen_at: str = "2026-05-27T12:00:00Z",
    seen_at: str | None = None,
    resolved_at: str | None = None,
) -> Alert:
    return Alert(
        detector=detector,
        dedup_key=dedup_key,
        severity=severity,
        title=title,
        message=message,
        metadata=metadata
        or {
            "window_rate": 0.116,
            "baseline_rate": 0.048,
            "window_calls": 120,
            "baseline_calls": 450,
            "multiple": 2.42,
        },
        first_seen_at=first_seen_at,
        last_seen_at=last_seen_at,
        seen_at=seen_at,
        resolved_at=resolved_at,
    )
