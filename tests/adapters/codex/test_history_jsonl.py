"""Codex history.jsonl -> prompts with exact session links and project healing."""
from __future__ import annotations

import json

from argus.adapters.codex.history_jsonl import ingest_history_file, line_to_prompt
from tests.conftest import session_factory

THREAD = "019a0000-0000-7000-8000-000000000001"


def test_line_to_prompt_shape():
    p = line_to_prompt({"session_id": THREAD, "ts": 1756720800, "text": "/init"}, "")
    assert p.timestamp_ms == 1756720800000 and p.session_id == f"codex:{THREAD}"
    assert p.is_slash == 1 and p.project_path == "" and p.pasted_chars == 0
    assert line_to_prompt({"session_id": THREAD, "ts": 1, "text": "   "}, "") is None
    assert line_to_prompt({"ts": 1, "text": "no session"}, "") is None


def test_ingest_links_project_when_session_exists_and_heals_later(tmp_path, repo):
    h = tmp_path / "history.jsonl"
    h.write_text(json.dumps({"session_id": THREAD, "ts": 10, "text": "first"}) + "\n", encoding="utf-8")
    s = ingest_history_file(h, repo)
    assert s.inserted == 1 and s.resolved == 0
    rows = repo.db.execute("SELECT project_path, session_id FROM prompts").fetchall()
    assert rows[0]["project_path"] == "" and rows[0]["session_id"] == f"codex:{THREAD}"

    repo.upsert_session(session_factory(f"codex:{THREAD}", "2026-09-01T10:00:00Z", agent="codex"))
    with h.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"session_id": THREAD, "ts": 20, "text": "second"}) + "\n")
    s2 = ingest_history_file(h, repo)
    assert s2.inserted == 1 and s2.resolved == 1
    paths = {r["project_path"] for r in repo.db.execute("SELECT project_path FROM prompts").fetchall()}
    assert paths == {"/p"}


def test_truncated_file_restarts_from_zero(tmp_path, repo):
    h = tmp_path / "history.jsonl"
    h.write_text(json.dumps({"session_id": THREAD, "ts": 10, "text": "a" * 50}) + "\n", encoding="utf-8")
    ingest_history_file(h, repo)
    h.write_text(json.dumps({"session_id": THREAD, "ts": 11, "text": "b"}) + "\n", encoding="utf-8")
    s = ingest_history_file(h, repo)
    assert s.inserted == 1


def test_malformed_lines_are_counted_not_fatal(tmp_path, repo):
    h = tmp_path / "history.jsonl"
    h.write_text("{oops\n" + json.dumps({"session_id": THREAD, "ts": 12, "text": "ok"}) + "\n", encoding="utf-8")
    s = ingest_history_file(h, repo)
    assert s.inserted == 1 and s.parse_errors == 1
