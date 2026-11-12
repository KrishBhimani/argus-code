"""Session-id -> transcript-file lookup used by the backfills.

Was keyed by bare file stem (Claude's native id). Codex's native id is the
thread id from the file's first record, and one Codex thread can span several
files (continuation segments), so the lookup is now "<agent>:<native id>" ->
list of files. For Claude the keys are exactly the old ones."""
from __future__ import annotations

from pathlib import Path

from argus.adapters.base import native_session_id_for
from argus.adapters.claude_code.adapter import ClaudeCodeAdapter
from argus.collector.session_files import files_by_session


class _StemOnly:
    """A third-party adapter that predates native_session_id()."""
    agent = "other"

    def __init__(self, files):
        self.files = files

    def discover_session_files(self):
        return self.files


class _Threaded:
    agent = "codex"

    def __init__(self, mapping):
        self.mapping = mapping

    def discover_session_files(self):
        return list(self.mapping)

    def native_session_id(self, path):
        return self.mapping[path]


def test_claude_keys_are_agent_plus_stem(tmp_path: Path) -> None:
    proj = tmp_path / ".claude" / "projects" / "p"
    proj.mkdir(parents=True)
    f = proj / "abc.jsonl"
    f.write_text("", encoding="utf-8")
    a = ClaudeCodeAdapter(tmp_path / ".claude")
    assert native_session_id_for(a, f) == "abc"
    assert files_by_session([a]) == {"claude_code:abc": [(a, f)]}


def test_adapter_without_method_falls_back_to_stem() -> None:
    a = _StemOnly([Path("/x/s1.jsonl")])
    assert files_by_session([a]) == {"other:s1": [(a, Path("/x/s1.jsonl"))]}


def test_one_session_can_span_several_files() -> None:
    f1, f2 = Path("/c/rollout-a.jsonl"), Path("/c/rollout-a_b.jsonl")
    a = _Threaded({f1: "T", f2: "T"})
    assert files_by_session([a]) == {"codex:T": [(a, f1), (a, f2)]}
