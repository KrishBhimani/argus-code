"""CodexAdapter façade: presence, discovery, containment, skip rules."""
from __future__ import annotations

import json
from pathlib import Path

from argus.adapters.codex.adapter import CodexAdapter
from argus.adapters.registry import registered_adapter_names


def _meta(tid, **extra):
    return json.dumps({"timestamp": "2026-10-03T20:00:00.000Z", "ordinal": 0, "type": "session_meta",
                       "payload": {"id": tid, **extra}})


def _write(p: Path, *lines: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(l + "\n" for l in lines), encoding="utf-8")
    return p


def test_registered() -> None:
    assert "codex" in registered_adapter_names()


def test_codex_home_env_missing_dir_is_absent(tmp_path: Path, monkeypatch) -> None:
    """Review focus #5: CODEX_HOME pointing nowhere never falls back to ~/.codex."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "missing"))
    a = CodexAdapter()
    assert a.root_path() == (tmp_path / "missing").resolve(strict=False)
    assert a.is_present() is False


def test_discovery_and_skip_rules(tmp_path: Path) -> None:
    root = tmp_path / ".codex"
    top = _write(root / "sessions" / "2026" / "10" / "04" / "rollout-1-P.jsonl", _meta("P"))
    child = _write(root / "sessions" / "2026" / "10" / "04" / "rollout-2-C.jsonl", _meta("C", parent_thread_id="P"))
    hist = _write(root / "history.jsonl", "{}")
    other = _write(root / "tmp" / "x.jsonl", _meta("X"))
    a = CodexAdapter(root)
    assert a.is_present()
    assert a.discover_session_files() == [top]
    assert a.sub_session_files_for(top) == [child]
    assert a.native_session_id(top) == "P" and a.native_session_id(child) == "C"
    assert a.should_skip(top) is False
    assert a.should_skip(child) is True
    assert a.should_skip(hist) is True and a.should_skip(other) is True


def test_should_skip_partial_head(tmp_path: Path) -> None:
    """Review focus #1: a rollout whose first line is mid-write is skipped
    (could be a sub-agent child); the next fs event retries it."""
    root = tmp_path / ".codex"
    f = root / "sessions" / "rollout-N.jsonl"
    f.parent.mkdir(parents=True)
    f.write_text(_meta("N")[:30], encoding="utf-8")
    a = CodexAdapter(root)
    assert a.should_skip(f) is True
    f.write_text(_meta("N") + "\n", encoding="utf-8")
    assert a.should_skip(f) is False


def test_ingest_refuses_paths_outside_session_dirs(tmp_path: Path) -> None:
    root = tmp_path / ".codex"
    (root / "sessions").mkdir(parents=True)
    outside = _write(tmp_path / "evil.jsonl", _meta("E"))
    r, off = CodexAdapter(root).ingest_file(outside, 0)
    assert r.turns == [] and r.parse_errors == [] and off == 0


def test_defines_every_adapter_protocol_method(tmp_path: Path) -> None:
    """The collector calls the optional protocol hooks directly (e.g.
    first_run walks extra_watch_paths() of every present adapter), and a
    Protocol's default bodies are not inherited, so each must exist here."""
    a = CodexAdapter(tmp_path)
    for name in ("root_path", "is_present", "discover_session_files", "ingest_file",
                 "extra_watch_paths", "ingest_extra", "sub_session_files_for",
                 "should_skip", "normalize_model_name", "native_session_id"):
        assert callable(getattr(a, name, None)), name
    # history.jsonl ingestion is deferred (needs a migration): nothing to tail.
    assert a.extra_watch_paths() == []
