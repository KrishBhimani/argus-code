from argus.work.stories import SessionSummary, group_stories


def S(sid, branch, first, last, active=1000, cost=1.0, title=None, commits=()):
    return SessionSummary(sid, sid if title is None else title, branch, first, last, active, cost, 1, [], [], list(commits))


def test_same_branch_within_gap_is_one_story():
    stories = group_stories([
        S("a", "feat", "2026-09-01T10:00:00Z", "2026-09-01T11:00:00Z", active=500, title="short"),
        S("b", "feat", "2026-09-02T09:00:00Z", "2026-09-02T12:00:00Z", active=9000, title="long one"),
        S("c", "fix", "2026-09-02T13:00:00Z", "2026-09-02T14:00:00Z"),
        S("d", "feat", "2026-09-08T10:00:00Z", "2026-09-08T11:00:00Z"),
    ])
    assert [s["session_ids"] for s in stories] == [["d"], ["c"], ["a", "b"]]  # newest first
    assert stories[2]["title"] == "long one" and stories[2]["active_ms"] == 9500


def test_commit_counts_by_evidence():
    (story,) = group_stories([S("a", "feat", "2026-09-01T10:00:00Z", "2026-09-01T11:00:00Z", commits=[
        {"sha": "1", "evidence": "exact", "added": 10, "deleted": 2, "files": 3},
        {"sha": "2", "evidence": "inferred", "added": 1, "deleted": 0, "files": 1}])])
    assert story["commits"] == {"total": 2, "exact": 1, "files": 0, "coauthored": 0, "inferred": 1}
    assert (story["added"], story["deleted"], story["files"]) == (11, 2, 4)


def test_untitled_story_falls_back_to_branch():
    (story,) = group_stories([S("a", "feat/x", "2026-09-01T10:00:00Z", "2026-09-01T10:30:00Z", title="")])
    assert story["title"] == "feat/x"


def test_story_carries_key_and_latest_folder():
    a = SessionSummary("claude_code:a", "A", "feat", "2026-09-01T10:00:00Z", "2026-09-01T11:00:00Z", 0, 1.0, 1, repo_id=4)
    b = SessionSummary("claude_code:b", "B", "feat", "2026-09-02T10:00:00Z", "2026-09-02T11:00:00Z", 0, 1.0, 1, repo_id=7)
    (st,) = group_stories([a, b], gap_days=2)
    assert st["key"] == "claude_code:a" and st["repo_id"] == 7


def F(sid, branch, first, last, files, **kw):
    """A session with a file-edit record: `files` are the project files it edited (may be none)."""
    s = S(sid, branch, first, last, **kw)
    s.files = frozenset(files)
    return s


def test_unrelated_sessions_on_main_are_separate_threads():
    """Seen on real data: 54 of 61 sessions ran on main or development, and any two within
    two days became one thread whatever they were about."""
    stories = group_stories([
        F("a", "main", "2026-09-01T10:00:00Z", "2026-09-01T11:00:00Z", ["api/routes.py", "api/auth.py"]),
        F("b", "main", "2026-09-01T12:00:00Z", "2026-09-01T13:00:00Z", ["docs/guide.md"]),
        F("c", "main", "2026-09-02T09:00:00Z", "2026-09-02T10:00:00Z", ["api/auth.py", "tests/test_auth.py"]),
    ])
    assert [s["session_ids"] for s in stories] == [["b"], ["a", "c"]]   # newest-started thread first
    assert stories[1]["files_edited"] == 3 and stories[0]["files_edited"] == 1


def test_a_session_joins_the_recent_thread_it_shares_most_files_with_even_across_branches():
    stories = group_stories([
        F("a", "main", "2026-09-01T10:00:00Z", "2026-09-01T11:00:00Z", ["x.py", "y.py"]),
        F("b", "main", "2026-09-01T12:00:00Z", "2026-09-01T13:00:00Z", ["x.py", "p.py", "q.py"]),
        F("c", "fix/q", "2026-09-02T09:00:00Z", "2026-09-02T10:00:00Z", ["p.py", "q.py"]),
    ])
    assert sorted(s["session_ids"] for s in stories) == [["a", "b", "c"]]


def test_one_shared_file_among_many_does_not_join_two_threads():
    """A file everything touches (a changelog, a lockfile) is not a shared piece of work."""
    big = [f"src/m{i}.py" for i in range(8)]
    stories = group_stories([
        F("a", "main", "2026-09-01T10:00:00Z", "2026-09-01T11:00:00Z", [*big, "CHANGELOG.md"]),
        F("b", "main", "2026-09-01T12:00:00Z", "2026-09-01T13:00:00Z", [*[f"web/c{i}.tsx" for i in range(8)], "CHANGELOG.md"]),
    ])
    assert len(stories) == 2


def test_shared_files_only_join_within_the_gap():
    stories = group_stories([
        F("a", "main", "2026-09-01T10:00:00Z", "2026-09-01T11:00:00Z", ["x.py"]),
        F("b", "main", "2026-09-08T10:00:00Z", "2026-09-08T11:00:00Z", ["x.py"]),
    ])
    assert len(stories) == 2


def test_a_feature_branch_still_holds_its_sessions_together():
    """Planning (no edits), then building, on one feature branch is one thread; the same two on
    a long-lived branch are not, since the branch says nothing there."""
    plan_build = lambda branch: group_stories([   # noqa: E731
        F("a", branch, "2026-09-01T10:00:00Z", "2026-09-01T11:00:00Z", []),
        F("b", branch, "2026-09-01T12:00:00Z", "2026-09-01T13:00:00Z", ["x.py"]),
    ])
    assert len(plan_build("feat/x")) == 1
    assert len(plan_build("main")) == 2 and len(plan_build("development")) == 2
    assert len(group_stories([F("a", "release", "2026-09-01T10:00:00Z", "2026-09-01T11:00:00Z", []),
                              F("b", "release", "2026-09-01T12:00:00Z", "2026-09-01T13:00:00Z", ["x.py"])],
                             long_lived={"release"})) == 2


def test_sessions_without_a_file_record_keep_the_branch_rule():
    """Transcripts already deleted have no record (files is None, not empty): group as before."""
    stories = group_stories([
        S("a", "main", "2026-09-01T10:00:00Z", "2026-09-01T11:00:00Z"),
        S("b", "main", "2026-09-01T12:00:00Z", "2026-09-01T13:00:00Z"),
        F("c", "main", "2026-09-01T14:00:00Z", "2026-09-01T15:00:00Z", ["x.py"]),
    ])
    assert sorted(s["session_ids"] for s in stories) == [["a", "b", "c"]]
    assert stories[0]["files_edited"] == 1


def test_files_edited_is_unknown_when_no_session_has_a_record_and_zero_when_none_edited():
    (old,) = group_stories([S("a", "feat", "2026-09-01T10:00:00Z", "2026-09-01T11:00:00Z")])
    (ask,) = group_stories([F("a", "feat", "2026-09-01T10:00:00Z", "2026-09-01T11:00:00Z", [])])
    (mix,) = group_stories([S("a", "feat", "2026-09-01T10:00:00Z", "2026-09-01T11:00:00Z"),
                            F("b", "feat", "2026-09-01T12:00:00Z", "2026-09-01T13:00:00Z", [])])
    assert (old["files_edited"], ask["files_edited"], mix["files_edited"]) == (None, 0, None)
