"""GET /api/commands: the command ledger, read as inert text."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from argus.collector.first_run import IngestStatus
from argus.pricing.types import PricingTable
from argus.schema.types import Command, ToolCall
from argus.server.api import ApiDeps, build_api
from tests.conftest import session_factory

LOOPBACK = "http://127.0.0.1"   # the DNS-rebinding guard wants a loopback Host


def _ts(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


@pytest.fixture
def client(repo):
    app = FastAPI()
    app.include_router(build_api(repo, ApiDeps(
        pricing_table_version="2026-05-02",
        ingest_status=lambda: IngestStatus(foreground_complete=True, pending=0, processed=1, total=1),
        adapters=[], pricing_table=PricingTable(version="2026-05-02", models={}))))
    return TestClient(app, base_url=LOOPBACK)


def add(repo, sid: str, use_id: str, command: str, *, days_ago: float = 1, severity: str | None = None,
        risks: list[dict] | None = None, secrets: list[str] | None = None, error: int = 0,
        project: str = "/work/alpha", agent: str = "claude_code", shell: str = "bash") -> None:
    if repo.get_session(sid) is None:
        repo.upsert_session(session_factory(sid, _ts(days_ago), project_path=project, agent=agent))
    cid = f"{sid}:{use_id}"
    repo.upsert_tool_calls([ToolCall(id=cid, session_id=sid, turn_index=0, tool_name="Bash", is_error=error,
                                     input_size=1, subagent_type=None, timestamp=_ts(days_ago))])
    repo.upsert_commands([Command(id=cid, session_id=sid, timestamp=_ts(days_ago), shell=shell, cwd=project,
                                  command=command, truncated=False, background=False, severity=severity,
                                  risks=risks or [], secrets=secrets or [])])


@pytest.fixture
def ledger(repo):
    add(repo, "claude_code:a", "t1", "git status", days_ago=1)
    add(repo, "claude_code:a", "t2", "rm -rf /", days_ago=2, severity="high",
        risks=[{"rule": "delete-recursive", "severity": "high"}], error=1)
    add(repo, "claude_code:a/agent-x", "t3", "git reset --hard", days_ago=3, severity="medium",
        risks=[{"rule": "git-reset-hard", "severity": "medium"}])
    add(repo, "claude_code:b", "t4", "rm -rf build", days_ago=4, severity="low",
        risks=[{"rule": "delete-recursive", "severity": "low"}], project="/work/beta")
    add(repo, "codex:c", "t5", "export TOKEN=[redacted:assignment]", days_ago=5, secrets=["assignment"],
        project="/work/beta", agent="codex", shell="shell")
    add(repo, "claude_code:b", "t6", "ls -la", days_ago=40, project="/work/beta")
    return repo


def ids(r) -> list[str]:
    return [i["id"].rsplit(":", 1)[-1] for i in r.json()["items"]]


def test_lists_newest_first_with_everything_a_row_needs(client, ledger):
    r = client.get("/api/commands", params={"window": "all"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    assert ids(r) == ["t1", "t2", "t3", "t4", "t5", "t6"] and r.json()["total"] == 6
    t2 = r.json()["items"][1]
    assert t2 == {
        "id": "claude_code:a:t2", "session_id": "claude_code:a", "timestamp": t2["timestamp"], "agent": "claude_code",
        "shell": "bash", "cwd": "/work/alpha", "project_path": "/work/alpha", "command": "rm -rf /",
        "truncated": False, "background": False, "failed": True, "severity": "high",
        "risks": [{"rule": "delete-recursive", "severity": "high"}], "secrets": [],
    }


def test_window_and_counts(client, ledger):
    r = client.get("/api/commands")                       # default window: 30 days
    assert ids(r) == ["t1", "t2", "t3", "t4", "t5"]
    assert r.json()["counts"] == {"total": 5, "high": 1, "medium": 1, "low": 1, "secrets": 1}
    assert client.get("/api/commands", params={"window": "all"}).json()["counts"]["total"] == 6


def test_filters(client, ledger):
    get = lambda **p: ids(client.get("/api/commands", params={"window": "all", **p}))  # noqa: E731
    assert get(severity="high") == ["t2"]
    assert get(severity="medium") == ["t2", "t3"]          # at least medium
    assert get(severity="low") == ["t2", "t3", "t4"]       # anything flagged
    assert get(secrets="1") == ["t5"]
    assert get(agent="codex") == ["t5"]
    assert get(project="/work/beta") == ["t4", "t5", "t6"]
    assert get(session="claude_code:a") == ["t1", "t2", "t3"]   # a session's sub-agents come with it
    assert get(session="claude_code:a/agent-x") == ["t3"]
    assert get(q="rm -rf") == ["t2", "t4"]
    assert get(q="RM -RF", severity="high") == ["t2"]
    assert get(limit=2, offset=1) == ["t2", "t3"]
    assert client.get("/api/commands", params={"window": "all", "q": "rm", "limit": 1}).json()["total"] == 2


def test_search_text_is_matched_literally(client, repo):
    """`%` and `_` are LIKE wildcards and `'` ends a SQL string: none of them may act as one."""
    add(repo, "claude_code:a", "t1", "echo 100% done")
    add(repo, "claude_code:a", "t2", "echo 100 percent done")
    add(repo, "claude_code:a", "t3", "ls my_file")
    add(repo, "claude_code:a", "t4", "ls myXfile")
    add(repo, "claude_code:a", "t5", "echo x'; DROP TABLE commands; --")
    add(repo, "claude_code:a", "t6", "cd C:\\Users\\me")
    get = lambda q: ids(client.get("/api/commands", params={"q": q}))  # noqa: E731
    assert get("100%") == ["t1"]
    assert get("my_file") == ["t3"]
    assert get("%") == ["t1"]
    assert get("'; DROP TABLE commands; --") == ["t5"]
    assert get("C:\\Users") == ["t6"]
    assert repo.db.execute("SELECT COUNT(*) FROM commands").fetchone()[0] == 6     # still there


def test_a_session_id_is_matched_exactly_never_as_a_pattern(client, repo):
    add(repo, "claude_code:ab", "t1", "ls")
    add(repo, "claude_code:a%", "t2", "ls")
    assert ids(client.get("/api/commands", params={"session": "claude_code:a%"})) == ["t2"]
    assert ids(client.get("/api/commands", params={"session": "claude_code:a_"})) == []
    assert ids(client.get("/api/commands", params={"session": "' OR 1=1 --"})) == []


def test_stored_text_comes_back_as_data_untouched(client, repo):
    """The API never interprets a command: markup and spreadsheet formulas are returned as
    JSON strings; escaping for display is the dashboard's job (and it has no raw-HTML sink)."""
    payload = "<img src=x onerror=alert(1)> =cmd|' /C calc'!A0 ${jndi:x} {{7*7}}"
    add(repo, "claude_code:a", "t1", payload)
    r = client.get("/api/commands")
    assert r.json()["items"][0]["command"] == payload
    assert "<img" not in r.text.replace(payload.replace('"', '\\"'), "")       # appears once, inside the JSON string


@pytest.mark.parametrize("params", [
    {"severity": "critical"}, {"severity": "high' OR 1=1"}, {"limit": -1}, {"limit": 100000}, {"offset": -5},
    {"window": "forever"}, {"secrets": "yes"}, {"q": "x" * 600},
])
def test_bad_parameters_are_refused(client, ledger, params):
    assert client.get("/api/commands", params=params).status_code == 422


def test_an_empty_ledger_answers_cleanly(client):
    r = client.get("/api/commands")
    assert r.json() == {"items": [], "total": 0, "counts": {"total": 0, "high": 0, "medium": 0, "low": 0, "secrets": 0}}
