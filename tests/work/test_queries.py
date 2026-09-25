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


def test_estimated_active_time_is_flagged_everywhere(tmp_path):
    """Review I-5 / spec §5: time estimated from line gaps (older Claude Code, no
    turn_duration) must be labelled as an estimate ("≈"), not shown as measured."""
    conn = _world(tmp_path)
    frm, to = "2026-09-01T00:00:00Z", "2026-09-25T00:00:00Z"
    assert queries.overview(conn, 1, frm, to)["tiles"]["active_estimated"] is False
    conn.execute("DELETE FROM active_spans WHERE session_id = 'claude_code:B'")
    conn.execute("INSERT INTO active_spans VALUES ('claude_code:B', '2026-09-20T22:30:00Z', 60000, 'estimated')")
    o = queries.overview(conn, 1, frm, to)
    assert o["tiles"]["active_estimated"] is True
    assert {s["title"]: s["active_estimated"] for s in o["stories"]} == {"Fix B": True, "Build A": False}
    (p,) = queries.projects(conn, now_iso="2026-09-25T00:00:00Z")
    assert p["active_estimated"] is True
    items = [i for d in queries.timeline(conn, 1, frm, to)["days"] for i in d["items"] if i["kind"] == "session"]
    assert {i["session_id"]: i["active_estimated"] for i in items} == {"claude_code:A": False, "claude_code:B": True}


def test_overview_survives_a_very_busy_project(tmp_path):
    """Review minor-1, upgraded (it breaks the main view): the skill/MCP breakdown bound
    every turn id in range as a SQL variable, so > 32,766 turns in 30 days raised
    'too many SQL variables' and the Overview returned 500."""
    import sqlite3 as sq

    conn = _world(tmp_path)
    conn.close()
    core = sq.connect(tmp_path / "argus.db")
    core.executemany(
        "INSERT INTO turns (id, session_id, sequence, timestamp, model, model_raw, cost_usd, metadata) "
        "VALUES (?, 'claude_code:A', ?, '2026-09-12T10:00:00Z', 'm', 'm', 0.001, '{}')",
        [(f"claude_code:A:bulk{i}", i) for i in range(33_000)])
    core.commit()
    core.close()
    conn = open_work_db(tmp_path)
    conn.execute("INSERT INTO turn_attribution VALUES ('claude_code:A:bulk7', 'superpowers:tdd', NULL, NULL, NULL)")
    o = queries.overview(conn, 1, "2026-09-01T00:00:00Z", "2026-09-25T00:00:00Z")
    assert {s["name"] for s in o["breakdowns"]["skills"]} == {"superpowers:brainstorming", "superpowers:tdd"}


def _grouped(tmp_path):
    """proj lives in two folders (/r and an older clone) with the same remote, plus a fork elsewhere."""
    conn = _world(tmp_path)
    r = Repository(open_db(tmp_path / "argus.db"))
    r.upsert_session(session_factory("claude_code:C", "2026-09-12T10:00:00Z"))
    r.upsert_turn(turn_factory("claude_code:C:m1", "claude_code:C", "2026-09-12T10:00:00Z", cost=3.0))
    r.upsert_session(session_factory("claude_code:F", "2026-09-13T10:00:00Z"))
    r.upsert_turn(turn_factory("claude_code:F:m1", "claude_code:F", "2026-09-13T10:00:00Z", cost=7.0))
    r.db.close()
    key = "remote:github.com/me/proj"
    conn.execute("UPDATE repos SET project_key = ? WHERE id = 1", (key,))
    conn.execute("INSERT INTO repos (id, root, display_name, user_emails, project_key) VALUES (2, '/old/proj-clone', 'proj-clone', '[\"me@x\"]', ?)", (key,))
    conn.execute("INSERT INTO repos (id, root, display_name, user_emails, project_key) VALUES (3, '/fork/proj', 'proj', '[\"me@x\"]', 'remote:github.com/other/proj')")
    conn.executemany("INSERT INTO session_repo VALUES (?, ?, ?, 'main')", [("claude_code:C", 2, "/old/proj-clone"), ("claude_code:F", 3, "/fork/proj")])
    conn.execute("INSERT INTO session_facts VALUES ('claude_code:C', 'Old clone work', 'ai', NULL)")
    # the clone holds the same commit abc1234 (weaker link there) plus one of its own
    conn.execute("INSERT INTO commits VALUES (2, 'abc1234', 'Me', 'me@x', '2026-09-10T10:10:00Z', '2026-09-10T10:10:00Z', 'feat: a', 1, 0, 5, 1, 1)")
    conn.execute("INSERT INTO commits VALUES (2, 'cl00001', 'Me', 'me@x', '2026-09-12T10:05:00Z', '2026-09-12T10:05:00Z', 'feat: c', 1, 0, 2, 0, 1)")
    conn.execute("INSERT INTO commit_files VALUES (2, 'abc1234', 'src/a.py', 5, 1)")
    conn.execute("INSERT INTO session_commits VALUES ('claude_code:C', 2, 'abc1234', 'inferred')")
    conn.execute("INSERT INTO session_commits VALUES ('claude_code:C', 2, 'cl00001', 'exact')")
    return conn


def test_folders_of_one_repo_are_one_project_and_forks_stay_apart(tmp_path):
    conn = _grouped(tmp_path)
    ps = {p["id"]: p for p in queries.projects(conn, now_iso="2026-09-25T00:00:00Z")}
    assert set(ps) == {1, 3}
    p = ps[1]
    assert p["display_name"] == "proj" and p["folder_ids"] == [1, 2]
    assert [f["root"] for f in p["folders"]] == ["/r", "/old/proj-clone"]
    assert p["commits_30d"] == 2          # abc1234 once, cl00001; Mate's commit isn't "mine"
    assert p["active_ms_30d"] == 5_400_000


def test_grouped_overview_counts_shared_commits_once_with_the_best_link(tmp_path):
    conn = _grouped(tmp_path)
    frm, to = "2026-09-01T00:00:00Z", "2026-09-25T00:00:00Z"
    o = queries.overview(conn, 1, frm, to)
    assert o["tiles"]["commits"] == 2 and o["tiles"]["cost"] == 7.0    # A 2 + B 2 + C 3; the fork's 7 is not here
    assert sorted(s["title"] for s in o["stories"]) == ["Build A", "Fix B", "Old clone work"]
    build_a = next(s for s in o["stories"] if s["title"] == "Build A")
    assert build_a["commits"]["exact"] == 1                             # exact beats the clone's inferred link
    assert o["breakdowns"]["files"] == [{"name": "src/a.py", "value": 1}]
    assert queries.overview(conn, 2, frm, to) == o                      # any folder's id opens the project
    t = queries.timeline(conn, 2, frm, to, scope="all")
    shas = [c["sha"] for d in t["days"] for i in d["items"] for c in ([i] if i["kind"] == "commit" else i["commits"])]
    assert shas.count("abc1234") == 1
    assert queries.first_activity(conn, 2) == "2026-09-10T10:00:00Z"


def test_time_prefers_measured_then_transcript_estimate_then_archive_estimate(tmp_path):
    conn = _world(tmp_path)
    # A has measured time; add archive rows that must be ignored for it
    conn.execute("INSERT INTO active_spans VALUES ('claude_code:A', '2026-09-10T10:05:00Z', 999999, 'archive')")
    # B keeps only an archive estimate
    conn.execute("DELETE FROM active_spans WHERE session_id = 'claude_code:B'")
    conn.execute("INSERT INTO active_spans VALUES ('claude_code:B', '2026-09-20T22:30:00Z', 60000, 'archive')")
    o = queries.overview(conn, 1, "2026-09-01T00:00:00Z", "2026-09-25T00:00:00Z")
    assert o["tiles"]["active_ms"] == 3_600_000 + 60_000 and o["tiles"]["active_estimated"] is True
    fix_b = next(s for s in o["stories"] if s["title"] == "Fix B")
    assert fix_b["active_ms"] == 60_000 and fix_b["active_estimated"] is True


def test_daily_series_carry_output_tokens_and_cost_and_stories_carry_tokens(tmp_path):
    conn = _world(tmp_path)   # each session: one turn, 200 output tokens, $2
    o = queries.overview(conn, 1, "2026-09-09T00:00:00Z", "2026-09-12T00:00:00Z")
    d = o["daily"]
    assert d["days"] == ["2026-09-09", "2026-09-10", "2026-09-11", "2026-09-12"]
    assert d["output_tokens"] == [0, 200, 0, 0] and d["cost"] == [0, 2.0, 0, 0]
    assert o["stories"][0]["output_tokens"] == 200
