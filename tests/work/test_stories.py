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
    assert story["commits"] == {"total": 2, "exact": 1, "coauthored": 0, "inferred": 1}
    assert (story["added"], story["deleted"], story["files"]) == (11, 2, 4)


def test_untitled_story_falls_back_to_branch():
    (story,) = group_stories([S("a", "feat/x", "2026-09-01T10:00:00Z", "2026-09-01T10:30:00Z", title="")])
    assert story["title"] == "feat/x"
