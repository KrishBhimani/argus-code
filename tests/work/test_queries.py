"""Read queries behind /api/work/*: numbers come from argus.db (read-only) + work.db."""
from __future__ import annotations

from argus.store.db import open_db
from argus.store.repository import Repository
from argus.work import queries
from argus.work.db import open_work_db
from tests.conftest import session_factory, turn_factory


def _world(tmp_path):
    r = Repository(open_db(tmp_path / "argus.db"))
    for sid, ts in (("claude_code:A", "2026-09-10T10:00:00Z"), ("claude_code:B", "2026-09-20T22:30:00Z")):
        r.upsert_session(session_factory(sid, ts))
        r.upsert_turn(turn_factory(f"{sid}:m1", sid, ts, cost=2.0))
    r.db.close()
    conn = open_work_db(tmp_path)
    conn.execute("INSERT INTO repos (id, root, display_name, user_emails) VALUES (1, '/r', 'proj', '[\"me@x\"]')")
    conn.executemany("INSERT INTO session_repo VALUES (?, 1, '/r', ?)", [("claude_code:A", "feat"), ("claude_code:B", "main")])
    conn.executemany("INSERT INTO session_facts VALUES (?, ?, 'ai', NULL)", [("claude_code:A", "Build A"), ("claude_code:B", "Fix B")])
    conn.executemany("INSERT INTO active_spans VALUES (?, ?, ?, 'measured')",
                     [("claude_code:A", "2026-09-10T10:00:00Z", 3_600_000), ("claude_code:B", "2026-09-20T22:30:00Z", 1_800_000)])
    conn.execute("INSERT INTO commits VALUES (1, 'abc1234', 'Me', 'me@x', '2026-09-10T10:10:00Z', '2026-09-10T10:10:00Z', 'feat: a', 1, 0, 5, 1, 1)")
    conn.execute("INSERT INTO commits VALUES (1, 'def5678', 'Mate', 'mate@x', '2026-09-11T10:00:00Z', '2026-09-11T10:00:00Z', 'chore', 1, 0, 1, 0, 1)")
    conn.execute("INSERT INTO commit_files VALUES (1, 'abc1234', 'src/a.py', 5, 1)")
    conn.execute("INSERT INTO session_commits VALUES ('claude_code:A', 1, 'abc1234', 'exact')")
    conn.execute("INSERT INTO turn_attribution VALUES ('claude_code:A:m1', 'superpowers:brainstorming', NULL, NULL, NULL)")
    return conn


def test_projects_list(tmp_path):
    conn = _world(tmp_path)
    (p,) = queries.projects(conn, now_iso="2026-09-25T00:00:00Z")
    assert (p["id"], p["display_name"], p["present"]) == (1, "proj", True)
    assert p["active_ms_30d"] == 5_400_000 and p["commits_30d"] == 1   # mine only
    assert p["last_worked_at"] == "2026-09-20T22:30:00Z" and len(p["daily_active_ms"]) == 30


def test_overview_tiles_and_stories(tmp_path):
    conn = _world(tmp_path)
    o = queries.overview(conn, 1, "2026-09-01T00:00:00Z", "2026-09-25T00:00:00Z")
    assert o["tiles"]["active_ms"] == 5_400_000 and o["tiles"]["cost"] == 4.0
    assert o["tiles"]["commits"] == 1 and o["tiles"]["cost_per_commit"] == 4.0
    assert [s["title"] for s in o["stories"]] == ["Fix B", "Build A"]
    assert o["breakdowns"]["files"] == [{"name": "src/a.py", "value": 1}]
    assert o["breakdowns"]["skills"][0]["name"] == "superpowers:brainstorming"
    assert queries.overview(conn, 1, "2026-09-01T00:00:00Z", "2026-09-25T00:00:00Z", scope="all")["tiles"]["commits"] == 2


def test_daily_series_uses_viewer_timezone(tmp_path):
    conn = _world(tmp_path)
    o = queries.overview(conn, 1, "2026-09-20T00:00:00Z", "2026-09-22T00:00:00Z", tz=330)
    days = o["daily"]["days"]
    assert days == ["2026-09-20", "2026-09-21", "2026-09-22"]
    # 22:30Z on the 20th is 04:00 on the 21st in IST, so B's time lands on the 21st
    assert o["daily"]["active_ms"][days.index("2026-09-21")] == 1_800_000
    assert o["daily"]["active_ms"][days.index("2026-09-20")] == 0


def test_timeline_nests_commits_under_sessions(tmp_path):
    conn = _world(tmp_path)
    t = queries.timeline(conn, 1, "2026-09-01T00:00:00Z", "2026-09-25T00:00:00Z", scope="all")
    items = [i for d in t["days"] for i in d["items"]]
    session_a = next(i for i in items if i.get("session_id") == "claude_code:A")
    assert [c["sha"] for c in session_a["commits"]] == ["abc1234"] and session_a["commits"][0]["evidence"] == "exact"
    assert any(i["kind"] == "commit" and i["sha"] == "def5678" for i in items)   # teammate commit, unlinked
    only_sessions = queries.timeline(conn, 1, "2026-09-01T00:00:00Z", "2026-09-25T00:00:00Z", kind="sessions")
    assert all(i["kind"] == "session" for d in only_sessions["days"] for i in d["items"])
