"""Evidence levels: exact > coauthored > inferred; each commit links to one session."""
from __future__ import annotations

from argus.store.db import open_db
from argus.store.repository import Repository
from argus.work.db import open_work_db, set_meta
from argus.work.linker import link_repo
from tests.conftest import session_factory, turn_factory


def _core(tmp_path, turns: dict[str, list[str]]):
    repo = Repository(open_db(tmp_path / "argus.db"))
    for sid, times in turns.items():
        repo.upsert_session(session_factory(sid, times[0]))
        for i, ts in enumerate(times):
            repo.upsert_turn(turn_factory(f"{sid}:m{i}", sid, ts))
    repo.db.close()


def _commit(conn, sha, ts, email="me@example.com", agent=0):
    conn.execute("INSERT INTO commits VALUES (1, ?, 'x', ?, ?, ?, 's', 1, ?, 0, 0, 0)", (sha, email, ts, ts, agent))


def _links(conn):
    return {r["sha"]: (r["session_id"], r["evidence"]) for r in conn.execute("SELECT * FROM session_commits")}


def test_evidence_levels_and_scope(tmp_path):
    _core(tmp_path, {"claude_code:A": ["2026-09-01T10:00:00Z"], "claude_code:B": ["2026-09-01T12:00:00Z"]})
    conn = open_work_db(tmp_path)
    conn.execute("INSERT INTO repos (id, root, display_name, user_emails) VALUES (1, '/r', 'r', '[\"me@example.com\"]')")
    conn.executemany("INSERT INTO session_repo VALUES (?, 1, '/r', 'main')", [("claude_code:A",), ("claude_code:B",)])
    _commit(conn, "aaaaaaa111", "2026-09-01T18:00:00Z")                    # outside window, but claimed
    conn.execute("INSERT INTO commit_claims VALUES ('claude_code:A', 'aaaaaaa', NULL)")
    _commit(conn, "bbbbbbb222", "2026-09-01T10:10:00Z", email="bot@x", agent=1)  # co-authored, A's window
    _commit(conn, "ccccccc333", "2026-09-01T12:20:00Z")                    # mine, B's window
    _commit(conn, "ddddddd444", "2026-09-01T12:21:00Z", email="mate@x")    # teammate: never inferred

    counts = link_repo(conn, 1)

    assert _links(conn) == {
        "aaaaaaa111": ("claude_code:A", "exact"),
        "bbbbbbb222": ("claude_code:A", "coauthored"),
        "ccccccc333": ("claude_code:B", "inferred"),
    }
    assert counts == {"exact": 1, "coauthored": 1, "inferred": 1}


def test_conflict_goes_to_nearest_preceding_turn(tmp_path):
    _core(tmp_path, {"claude_code:A": ["2026-09-01T10:00:00Z"], "claude_code:B": ["2026-09-01T10:20:00Z"]})
    conn = open_work_db(tmp_path)
    conn.execute("INSERT INTO repos (id, root, display_name, user_emails) VALUES (1, '/r', 'r', '[\"me@example.com\"]')")
    conn.executemany("INSERT INTO session_repo VALUES (?, 1, '/r', 'main')", [("claude_code:A",), ("claude_code:B",)])
    _commit(conn, "eeeeeee555", "2026-09-01T10:25:00Z")
    link_repo(conn, 1)
    assert _links(conn) == {"eeeeeee555": ("claude_code:B", "inferred")}


def test_global_email_counts_as_mine(tmp_path):
    _core(tmp_path, {"claude_code:A": ["2026-09-01T10:00:00Z"]})
    conn = open_work_db(tmp_path)
    conn.execute("INSERT INTO repos (id, root, display_name, user_emails) VALUES (1, '/r', 'r', '[]')")
    conn.execute("INSERT INTO session_repo VALUES ('claude_code:A', 1, '/r', 'main')")
    set_meta(conn, "global_user_email", "me@example.com")
    _commit(conn, "fffffff666", "2026-09-01T10:05:00Z")
    link_repo(conn, 1)
    assert _links(conn)["fffffff666"][1] == "inferred"


def test_relink_is_idempotent(tmp_path):
    _core(tmp_path, {"claude_code:A": ["2026-09-01T10:00:00Z"]})
    conn = open_work_db(tmp_path)
    conn.execute("INSERT INTO repos (id, root, display_name, user_emails) VALUES (1, '/r', 'r', '[\"me@example.com\"]')")
    conn.execute("INSERT INTO session_repo VALUES ('claude_code:A', 1, '/r', 'main')")
    _commit(conn, "ggggggg777", "2026-09-01T10:05:00Z")
    link_repo(conn, 1)
    link_repo(conn, 1)
    assert conn.execute("SELECT COUNT(*) FROM session_commits").fetchone()[0] == 1
