"""DB-only fork de-duplication (transcripts already deleted).

REGRESSION: a real archive held 8 pairs of top-level sessions storing the same
API messages (same message id, timestamp, model and token counts). The
file-verified repair (repair_fork_duplicates_v1) could fix only the one pair
whose fork transcript was still on disk; the other 7 double-counted ~91M
cache-read and ~594k output tokens. An API message id is unique per response,
so an exact match is a copy. The copy stays with the session that continued
with its own work first (a fork/resume is made later, from a session in use);
a session holding nothing but copies gives them up; exact twins keep the
lower session id.
"""
from __future__ import annotations

from argus.collector.first_run import dedupe_shared_messages
from argus.pricing.load import load_pricing_table
from argus.schema.types import ToolCall

from tests.conftest import session_factory, turn_factory

TABLE = load_pricing_table()


def _session(repo, sid: str, turns: list[tuple[str, str]], **kw) -> None:
    """turns: (message id, timestamp). Turn ids are f"{sid}:{message id}"."""
    repo.upsert_session(session_factory(sid, turns[0][1]))
    for i, (mid, ts) in enumerate(turns):
        repo.upsert_turn(turn_factory(f"{sid}:{mid}", sid, ts, **kw).model_copy(update={"sequence": i}))


def _call(repo, sid: str, tool_use_id: str, ts: str) -> None:
    repo.upsert_tool_calls([ToolCall(id=f"{sid}:{tool_use_id}", session_id=sid, turn_index=0,
                                     tool_name="Bash", is_error=0, input_size=1,
                                     subagent_type=None, timestamp=ts)])


def _msgs(repo, sid: str) -> list[str]:
    return [t.id.split(":", 2)[-1] for t in repo.get_turns_for_session(sid)]


def test_fork_gives_copies_back_to_the_session_that_continued_first(repo):
    _session(repo, "claude_code:P", [("m1", "2026-01-01T00:00:00Z"), ("m2", "2026-01-01T00:01:00Z"),
                                     ("p3", "2026-01-01T00:02:00Z")])
    _session(repo, "claude_code:F", [("m1", "2026-01-01T00:00:00Z"), ("m2", "2026-01-01T00:01:00Z"),
                                     ("f3", "2026-01-01T00:05:00Z")])
    _call(repo, "claude_code:P", "tu_shared", "2026-01-01T00:01:00Z")
    _call(repo, "claude_code:F", "tu_shared", "2026-01-01T00:01:00Z")
    _call(repo, "claude_code:F", "tu_own", "2026-01-01T00:05:00Z")

    dedupe_shared_messages(repo, TABLE)

    assert _msgs(repo, "claude_code:P") == ["m1", "m2", "p3"]
    assert _msgs(repo, "claude_code:F") == ["f3"]
    assert repo.count_tool_calls_for_session("claude_code:F") == 1  # tu_own only
    assert repo.count_tool_calls_for_session("claude_code:P") == 1
    fork = repo.get_session("claude_code:F")
    assert fork.turn_count == 1 and fork.started_at == "2026-01-01T00:05:00Z"


def test_session_made_only_of_copies_is_emptied_and_twins_keep_the_lower_id(repo):
    _session(repo, "claude_code:stub", [("m1", "2026-01-01T00:00:00Z")])
    _session(repo, "claude_code:long", [("m1", "2026-01-01T00:00:00Z"), ("l2", "2026-01-01T00:03:00Z")])
    _session(repo, "claude_code:b-twin", [("t1", "2026-02-01T00:00:00Z"), ("t2", "2026-02-01T00:01:00Z")])
    _session(repo, "claude_code:a-twin", [("t1", "2026-02-01T00:00:00Z"), ("t2", "2026-02-01T00:01:00Z")])

    dedupe_shared_messages(repo, TABLE)

    assert _msgs(repo, "claude_code:long") == ["m1", "l2"]
    assert _msgs(repo, "claude_code:stub") == []
    assert repo.get_session("claude_code:stub").turn_count == 0  # hidden by the API
    assert _msgs(repo, "claude_code:a-twin") == ["t1", "t2"]
    assert _msgs(repo, "claude_code:b-twin") == []


def test_not_an_exact_copy_is_left_alone(repo):
    """Same message id but different token counts: not provably a copy."""
    repo.upsert_session(session_factory("claude_code:X", "2026-01-01T00:00:00Z"))
    repo.upsert_session(session_factory("claude_code:Y", "2026-01-01T00:00:00Z"))
    repo.upsert_turn(turn_factory("claude_code:X:m1", "claude_code:X", "2026-01-01T00:00:00Z", output=200))
    repo.upsert_turn(turn_factory("claude_code:Y:m1", "claude_code:Y", "2026-01-01T00:00:00Z", output=999))

    dedupe_shared_messages(repo, TABLE)

    assert _msgs(repo, "claude_code:X") == ["m1"] and _msgs(repo, "claude_code:Y") == ["m1"]


def test_sub_agent_sessions_are_never_touched(repo):
    _session(repo, "claude_code:P", [("m1", "2026-01-01T00:00:00Z")])
    _session(repo, "claude_code:P/agent-a", [("m1", "2026-01-01T00:00:00Z")])

    dedupe_shared_messages(repo, TABLE)

    assert _msgs(repo, "claude_code:P") == ["m1"] and _msgs(repo, "claude_code:P/agent-a") == ["m1"]


def test_idempotent(repo):
    _session(repo, "claude_code:P", [("m1", "2026-01-01T00:00:00Z"), ("p2", "2026-01-01T00:01:00Z")])
    _session(repo, "claude_code:F", [("m1", "2026-01-01T00:00:00Z"), ("f2", "2026-01-01T00:04:00Z")])
    dedupe_shared_messages(repo, TABLE)
    before = {s: _msgs(repo, s) for s in ("claude_code:P", "claude_code:F")}
    dedupe_shared_messages(repo, TABLE)
    assert {s: _msgs(repo, s) for s in before} == before == {
        "claude_code:P": ["m1", "p2"], "claude_code:F": ["f2"]}
