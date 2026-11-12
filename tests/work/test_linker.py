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
    _commit(conn, "bbbbbbb222", "2026-09-01T10:10:00Z", agent=1)           # mine and co-authored, A's window
    _commit(conn, "ccccccc333", "2026-09-01T12:20:00Z")                    # mine, B's window
    _commit(conn, "ddddddd444", "2026-09-01T12:21:00Z", email="mate@x")    # teammate: never inferred

    counts = link_repo(conn, 1)

    assert _links(conn) == {
        "aaaaaaa111": ("claude_code:A", "exact"),
        "bbbbbbb222": ("claude_code:A", "coauthored"),
        "ccccccc333": ("claude_code:B", "inferred"),
    }
    assert counts == {"exact": 1, "files": 0, "coauthored": 1, "inferred": 1}


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


def test_teammates_agent_commit_is_not_mine(tmp_path):
    """Regression: a teammate's commit with an agent co-author trailer, made while one of my
    sessions was active, was linked to my session (69 such commits in one real repo)."""
    _core(tmp_path, {"claude_code:A": ["2026-09-01T10:00:00Z"]})
    conn = open_work_db(tmp_path)
    conn.execute("INSERT INTO repos (id, root, display_name, user_emails) VALUES (1, '/r', 'r', '[\"me@example.com\"]')")
    conn.execute("INSERT INTO session_repo VALUES ('claude_code:A', 1, '/r', 'main')")
    _commit(conn, "hhhhhhh888", "2026-09-01T10:05:00Z", email="mate@x", agent=1)
    assert link_repo(conn, 1) == {"exact": 0, "files": 0, "coauthored": 0, "inferred": 0}
    assert _links(conn) == {}


def _repo(conn):
    conn.execute("INSERT INTO repos (id, root, display_name, user_emails) VALUES (1, '/r', 'r', '[\"me@example.com\"]')")


def _edited(conn, sid, path, first, last=None):
    conn.execute("INSERT INTO session_files VALUES (?, ?, 1, ?, ?)", (sid, path, first, last or first))


def test_a_commit_of_files_the_session_edited_is_linked_whatever_the_clock_says(tmp_path):
    """The user commits by hand, hours after the session's last turn: timing finds nothing,
    but the commit changes the file the session edited."""
    _core(tmp_path, {"claude_code:A": ["2026-09-01T10:00:00Z"]})
    conn = open_work_db(tmp_path)
    _repo(conn)
    conn.execute("INSERT INTO session_repo VALUES ('claude_code:A', 1, '/r', 'main')")
    _edited(conn, "claude_code:A", "/r/src/a.py", "2026-09-01T10:00:00Z")
    _commit(conn, "aaaaaaa111", "2026-09-02T09:00:00Z")
    conn.execute("INSERT INTO commit_files VALUES (1, 'aaaaaaa111', 'src/a.py', 1, 0)")
    _commit(conn, "bbbbbbb222", "2026-09-02T09:05:00Z")                     # another file: not this session's
    conn.execute("INSERT INTO commit_files VALUES (1, 'bbbbbbb222', 'src/other.py', 1, 0)")
    counts = link_repo(conn, 1)
    assert _links(conn) == {"aaaaaaa111": ("claude_code:A", "files")}
    assert counts == {"exact": 0, "files": 1, "coauthored": 0, "inferred": 0}


def test_files_beat_the_clock_and_the_session_sharing_more_files_wins(tmp_path):
    _core(tmp_path, {"claude_code:A": ["2026-09-01T10:00:00Z"], "claude_code:B": ["2026-09-01T12:00:00Z"]})
    conn = open_work_db(tmp_path)
    _repo(conn)
    conn.executemany("INSERT INTO session_repo VALUES (?, 1, '/r', 'main')", [("claude_code:A",), ("claude_code:B",)])
    _edited(conn, "claude_code:A", "/r/a.py", "2026-09-01T10:00:00Z")
    _edited(conn, "claude_code:A", "/r/b.py", "2026-09-01T10:00:00Z")
    _edited(conn, "claude_code:B", "/r/a.py", "2026-09-01T12:00:00Z")
    _commit(conn, "ccccccc333", "2026-09-01T12:05:00Z")                     # 5 min after B's turn
    conn.executemany("INSERT INTO commit_files VALUES (1, 'ccccccc333', ?, 1, 0)", [("a.py",), ("b.py",)])
    link_repo(conn, 1)
    assert _links(conn) == {"ccccccc333": ("claude_code:A", "files")}


def test_files_never_link_a_commit_made_before_the_edit_or_long_after_or_by_a_teammate(tmp_path):
    _core(tmp_path, {"claude_code:A": ["2026-09-10T10:00:00Z"]})
    conn = open_work_db(tmp_path)
    _repo(conn)
    conn.execute("INSERT INTO session_repo VALUES ('claude_code:A', 1, '/r', 'main')")
    _edited(conn, "claude_code:A", "/r/a.py", "2026-09-10T10:00:00Z")
    for sha, ts, email in (("ddddddd444", "2026-09-09T10:00:00Z", "me@example.com"),    # before the edit
                           ("eeeeeee555", "2026-09-25T10:00:00Z", "me@example.com"),    # 15 days later
                           ("fffffff666", "2026-09-10T11:00:00Z", "mate@x")):           # a teammate's
        _commit(conn, sha, ts, email=email)
        conn.execute("INSERT INTO commit_files VALUES (1, ?, 'a.py', 1, 0)", (sha,))
    link_repo(conn, 1)
    assert _links(conn) == {}


def test_a_claimed_commit_stays_exact(tmp_path):
    _core(tmp_path, {"claude_code:A": ["2026-09-01T10:00:00Z"], "claude_code:B": ["2026-09-01T10:00:00Z"]})
    conn = open_work_db(tmp_path)
    _repo(conn)
    conn.executemany("INSERT INTO session_repo VALUES (?, 1, '/r', 'main')", [("claude_code:A",), ("claude_code:B",)])
    _edited(conn, "claude_code:B", "/r/a.py", "2026-09-01T10:00:00Z")
    _commit(conn, "ggggggg777", "2026-09-01T10:05:00Z")
    conn.execute("INSERT INTO commit_files VALUES (1, 'ggggggg777', 'a.py', 1, 0)")
    conn.execute("INSERT INTO commit_claims VALUES ('claude_code:A', 'ggggggg', NULL)")
    link_repo(conn, 1)
    assert _links(conn) == {"ggggggg777": ("claude_code:A", "exact")}


def test_a_session_that_edited_nothing_gets_no_commit_by_the_clock(tmp_path):
    """Seen on real data: a $0.56 "summarise this PR" session read as shipped, because the
    user committed two unrelated changes within 30 minutes of it."""
    _core(tmp_path, {"claude_code:A": ["2026-09-01T10:00:00Z"], "claude_code:B": ["2026-09-02T10:00:00Z"],
                     "claude_code:C": ["2026-09-03T10:00:00Z"], "claude_code:D": ["2026-09-04T10:00:00Z"]})
    conn = open_work_db(tmp_path)
    _repo(conn)
    conn.executemany("INSERT INTO session_repo VALUES (?, 1, '/r', 'main')",
                     [("claude_code:A",), ("claude_code:B",), ("claude_code:C",), ("claude_code:D",)])
    # A: read for edits, made none.  B: no record at all.  C: edited, but outside this repo.  D: like A.
    conn.executemany("INSERT INTO edits_seen VALUES (?)", [("claude_code:A",), ("claude_code:C",), ("claude_code:D",)])
    _edited(conn, "claude_code:C", "/elsewhere/notes.md", "2026-09-03T10:00:00Z")
    _commit(conn, "aaaaaaa111", "2026-09-01T10:05:00Z")            # by me, 5 min after A's turn
    _commit(conn, "bbbbbbb222", "2026-09-02T10:05:00Z")
    _commit(conn, "ccccccc333", "2026-09-03T10:05:00Z")
    _commit(conn, "ddddddd444", "2026-09-04T10:05:00Z", agent=1)   # the commit itself names an agent
    conn.execute("INSERT INTO commits VALUES (1, 'eeeeeee555', 'x', 'me@example.com', '2026-09-04T10:06:00Z', '2026-09-04T10:06:00Z', 'Merge main', 2, 0, 0, 0, 0)")
    # a stash entry an older scan stored: two parents like a merge, but no ref reaches it
    conn.execute("INSERT INTO commits VALUES (1, 'fffffff666', 'x', 'me@example.com', '2026-09-01T10:06:00Z', '2026-09-01T10:06:00Z', 'On main: parked', 2, 0, 0, 0, 0)")
    conn.execute("INSERT INTO commit_reach (repo_id, sha, on_default, pushed, reachable) VALUES (1, 'fffffff666', 0, 0, 0)")
    link_repo(conn, 1)
    assert _links(conn) == {
        "bbbbbbb222": ("claude_code:B", "inferred"),     # no record: the clock is all there is
        "ccccccc333": ("claude_code:C", "inferred"),     # it did edit files: a shell or unmapped change is plausible
        "ddddddd444": ("claude_code:D", "coauthored"),   # an agent made this commit; D is the one that was running
        "eeeeeee555": ("claude_code:D", "inferred"),     # a merge needs no edit
    }
