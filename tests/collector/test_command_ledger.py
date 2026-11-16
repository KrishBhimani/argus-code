"""The command ledger through the real pipeline: what an agent ran, stored masked and inert."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from argus.adapters.claude_code.adapter import ClaudeCodeAdapter
from argus.adapters.codex.adapter import CodexAdapter
from argus.collector import first_run
from argus.collector.first_run import COMMANDS_BACKFILL_KEY, _backfill_missing_derived_data
from argus.collector.pipeline import ingest_file
from argus.pricing.load import load_pricing_table
from argus.store.db import open_db
from argus.store.repository import Repository

SECRET = "ghp_" + "a1B2c3D4e5F6g7H8i9J0" * 2      # built from parts; not a real token


def _asst(msg_id: str, blocks: list[dict], *, ts="2026-05-01T00:00:00Z", cwd="C:/proj", extra: dict | None = None) -> str:
    return json.dumps({
        "type": "assistant", "sessionId": "sess1", "uuid": "u" + msg_id, "timestamp": ts, "cwd": cwd,
        "version": "2.1.94", "userType": "external", "entrypoint": "cli", **(extra or {}),
        "message": {"id": msg_id, "model": "claude-opus-4-7", "role": "assistant", "content": blocks,
                    "usage": {"input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": 0,
                              "cache_creation_input_tokens": 0}},
    })


def _use(use_id: str, name: str, **inp) -> dict:
    return {"type": "tool_use", "id": use_id, "name": name, "input": inp}


def _result(use_id: str, *, error=False) -> str:
    return json.dumps({"type": "user", "sessionId": "sess1", "uuid": "r" + use_id, "timestamp": "2026-05-01T00:00:05Z",
                       "cwd": "C:/proj", "userType": "external",
                       "message": {"role": "user", "content": [
                           {"type": "tool_result", "tool_use_id": use_id, "content": "out", "is_error": error}]}})


def _claude(tmp_path: Path, lines: list[str]) -> tuple[ClaudeCodeAdapter, Path]:
    proj = tmp_path / ".claude" / "projects" / "C--proj"
    proj.mkdir(parents=True, exist_ok=True)
    f = proj / "sess1.jsonl"
    f.write_text("".join(l + "\n" for l in lines), encoding="utf-8")
    return ClaudeCodeAdapter(tmp_path / ".claude"), f


def _rows(repo: Repository) -> dict[str, sqlite3.Row]:
    return {r["id"].rsplit(":", 1)[-1]: r for r in repo.db.execute("SELECT * FROM commands ORDER BY id")}


def test_shell_commands_are_stored_with_their_risk_and_folder(tmp_path, repo):
    adapter, f = _claude(tmp_path, [
        _asst("m1", [_use("t1", "Bash", command="git status", description="check"),
                     _use("t2", "Read", file_path="C:/proj/a.py")]),
        _asst("m2", [_use("t3", "PowerShell", command="Remove-Item -Recurse -Force C:\\", run_in_background=True)],
              ts="2026-05-01T00:01:00Z", cwd="C:/proj/sub"),
        _result("t3", error=True),
    ])
    ingest_file(adapter, f, repo, load_pricing_table())
    rows = _rows(repo)
    assert set(rows) == {"t1", "t3"}                       # Read is not a shell command
    t1, t3 = rows["t1"], rows["t3"]
    assert (t1["shell"], t1["command"], t1["cwd"], t1["severity"], t1["background"]) == ("bash", "git status", "C:/proj", None, 0)
    assert (t1["session_id"], t1["timestamp"]) == ("claude_code:sess1", "2026-05-01T00:00:00Z")
    assert (t3["shell"], t3["cwd"], t3["severity"], t3["background"]) == ("powershell", "C:/proj/sub", "high", 1)
    assert json.loads(t3["risks"]) == [{"rule": "delete-recursive", "severity": "high"}]
    assert json.loads(t1["risks"]) == [] and json.loads(t1["secrets"]) == []
    # whether it failed is the tool call's fact; the ledger row hangs off that call
    assert repo.db.execute("SELECT is_error FROM tool_calls WHERE id = ?", (t3["id"],)).fetchone()[0] == 1


def test_a_secret_in_a_command_never_reaches_the_database_file(tmp_path):
    """The whole point of masking at ingest: check the bytes on disk, not just the column."""
    data = tmp_path / "data"
    repo = Repository(open_db(data / "argus.db"))
    adapter, f = _claude(tmp_path, [_asst("m1", [_use("t1", "Bash", command=f"export GITHUB_TOKEN={SECRET} && gh pr list")])])
    ingest_file(adapter, f, repo, load_pricing_table())
    row = _rows(repo)["t1"]
    assert row["command"] == "export GITHUB_TOKEN=[redacted:github-token] && gh pr list"
    assert json.loads(row["secrets"]) == ["github-token"]
    repo.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    repo.db.close()
    for p in data.iterdir():
        assert SECRET.encode() not in p.read_bytes(), p.name


def test_reading_the_same_lines_again_changes_nothing_and_chunks_equal_one_pass(tmp_path, repo):
    lines = [_asst("m1", [_use("t1", "Bash", command="ls")]), _asst("m2", [_use("t2", "Bash", command="git push -f")])]
    adapter, f = _claude(tmp_path, lines[:1])
    table = load_pricing_table()
    ingest_file(adapter, f, repo, table)
    with f.open("a", encoding="utf-8") as fh:
        fh.write(lines[1] + "\n")
    ingest_file(adapter, f, repo, table)
    chunked = [tuple(r) for r in repo.db.execute("SELECT * FROM commands ORDER BY id")]
    repo.set_file_offset(str(f), 0)
    ingest_file(adapter, f, repo, table)
    assert [tuple(r) for r in repo.db.execute("SELECT * FROM commands ORDER BY id")] == chunked and len(chunked) == 2


def test_a_sub_agents_commands_are_stored_under_its_own_session(tmp_path, repo):
    adapter, f = _claude(tmp_path, [_asst("m1", [_use("t1", "Bash", command="ls")])])
    sub = f.parent / "sess1" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-abc.jsonl").write_text(_asst("s1", [_use("t9", "Bash", command="rm -rf build")]) + "\n", encoding="utf-8")
    ingest_file(adapter, f, repo, load_pricing_table())
    assert {r["session_id"] for r in _rows(repo).values()} == {"claude_code:sess1", "claude_code:sess1/agent-abc"}


def test_commands_go_when_their_session_or_tool_call_goes(tmp_path, repo):
    """The ledger never outlives or contradicts the rows it describes: a de-duplicated fork
    copy and a wiped session take their commands with them."""
    adapter, f = _claude(tmp_path, [_asst("m1", [_use("t1", "Bash", command="ls"), _use("t2", "Bash", command="pwd")])])
    ingest_file(adapter, f, repo, load_pricing_table())
    with repo.transaction():
        repo.db.execute("DELETE FROM tool_calls WHERE id = 'claude_code:sess1:t1'")
    assert set(_rows(repo)) == {"t2"}
    with repo.transaction():
        repo.db.execute("DELETE FROM sessions WHERE id = 'claude_code:sess1'")
    assert _rows(repo) == {}


def test_malformed_commands_are_skipped_not_fatal(tmp_path, repo):
    adapter, f = _claude(tmp_path, [_asst("m1", [
        _use("t1", "Bash", command=["rm", "-rf", "/"]), _use("t2", "Bash"), _use("t3", "Bash", command=""),
        {"type": "tool_use", "id": "t4", "name": "Bash", "input": "oops"}, _use("t5", "Bash", command="echo ok")])])
    ingest_file(adapter, f, repo, load_pricing_table())
    assert set(_rows(repo)) == {"t5"}
    assert repo.db.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0] == 5   # the calls themselves still count


def test_text_that_looks_like_sql_is_stored_as_text(tmp_path, repo):
    cmd = "echo '); DROP TABLE commands; --"
    adapter, f = _claude(tmp_path, [_asst("m1", [_use("t1", "Bash", command=cmd)])])
    ingest_file(adapter, f, repo, load_pricing_table())
    assert _rows(repo)["t1"]["command"] == cmd
    assert repo.db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


def test_codex_shell_commands_are_stored(tmp_path, repo):
    thread = "01a10342-0000-7000-8000-000000000001"

    def E(o, t, p):
        return json.dumps({"timestamp": "2026-10-03T20:00:00.000Z", "ordinal": o, "type": t, "payload": p})

    day = tmp_path / ".codex" / "sessions" / "2026" / "10" / "04"
    day.mkdir(parents=True)
    f = day / f"rollout-1-{thread}.jsonl"
    f.write_text("".join(l + "\n" for l in [
        E(0, "session_meta", {"id": thread, "cwd": "/work/proj", "cli_version": "0.159.2"}),
        E(1, "event_msg", {"type": "task_started", "turn_id": "U"}),
        E(2, "token_usage_record", {"response_id": "r1", "turn_id": "U", "thread_id": thread,
                                    "usage": {"input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 10}}),
        E(3, "event_msg", {"type": "item_completed", "item": {
            "type": "CommandExecution", "id": "cmd1", "command": ["git", "reset", "--hard", "HEAD~1"],
            "cwd": "/work/proj/pkg", "status": "completed", "exit_code": 0}}),
        E(4, "event_msg", {"type": "item_completed", "item": {
            "type": "CommandExecution", "id": "cmd2", "command": "echo one two", "status": "completed", "exit_code": 0}}),
        E(5, "event_msg", {"type": "task_complete", "turn_id": "U"}),
    ]), encoding="utf-8")
    ingest_file(CodexAdapter(tmp_path / ".codex"), f, repo, load_pricing_table())
    rows = _rows(repo)
    assert (rows["cmd1"]["shell"], rows["cmd1"]["command"], rows["cmd1"]["cwd"], rows["cmd1"]["severity"]) == (
        "shell", "git reset --hard HEAD~1", "/work/proj/pkg", "medium")
    assert (rows["cmd2"]["command"], rows["cmd2"]["cwd"]) == ("echo one two", "/work/proj")   # falls back to the thread's folder


def test_an_archive_from_before_the_ledger_gains_the_table_and_is_re_read_once(tmp_path):
    db = tmp_path / "argus.db"
    conn = open_db(db)
    conn.execute("DROP TABLE commands")
    conn.execute("UPDATE app_meta SET value = '7' WHERE key = 'schema_version'")
    conn.close()
    conn = open_db(db)
    assert conn.execute("SELECT COUNT(*) FROM commands").fetchone()[0] == 0
    assert {r[1] for r in conn.execute("PRAGMA index_list(commands)")} >= {"idx_commands_session", "idx_commands_time"}
    conn.close()


def _pre_ledger_archive(tmp_path, repo):
    """Two sessions ingested before the ledger existed; s1 is still being written."""
    root = tmp_path / ".claude"
    proj = root / "projects" / "C--proj"
    proj.mkdir(parents=True)
    table = load_pricing_table()
    adapter = ClaudeCodeAdapter(root)
    files = {}
    for sid in ("s1", "s2"):
        f = proj / f"{sid}.jsonl"
        f.write_text(_asst(f"m-{sid}", [_use(f"t-{sid}", "Bash", command=f"echo {sid}")]).replace('"sess1"', f'"{sid}"') + "\n",
                     encoding="utf-8")
        ingest_file(adapter, f, repo, table)
        files[sid] = f
    sub = proj / "s2" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-x.jsonl").write_text(_asst("m-sub", [_use("t-sub", "Bash", command="echo sub")]) + "\n", encoding="utf-8")
    ingest_file(adapter, files["s2"], repo, table)
    with repo.transaction():
        repo.db.execute("DELETE FROM commands")                      # as an archive from before migration 008
    # An established archive: every older "re-read everything once" sweep finished long ago,
    # so nothing but the ledger backfill gives a reason to re-read a file.
    for key in (first_run.STREAMED_OUTPUT_FIX_KEY, first_run.TOOL_ERRORS_FIX_KEY, "backfill_agent_subagent_type_v1"):
        repo.set_app_meta(key, "1")
    return adapter, table, files


def _ledger(repo) -> set[str]:
    return {r[0].rsplit(":", 1)[-1] for r in repo.db.execute("SELECT id FROM commands")}


def test_an_existing_archive_is_filled_once_from_the_transcripts_still_on_disk(tmp_path, repo):
    adapter, table, files = _pre_ledger_archive(tmp_path, repo)
    files["s2"].unlink()                                             # its transcript is gone: nothing to read
    # s1 gets new lines and is ingested before the backfill runs, as a session in use at
    # upgrade time is. Seen on real data: such sessions kept only their new commands.
    first_run._prepare_reread_sweep(repo, COMMANDS_BACKFILL_KEY)     # startup order: sweeps are stamped, then files ingested
    with files["s1"].open("a", encoding="utf-8") as fh:
        fh.write(_asst("m-new", [_use("t-new", "Bash", command="echo new")]).replace('"sess1"', '"s1"') + "\n")
    ingest_file(adapter, files["s1"], repo, table)
    assert _ledger(repo) == {"t-new"}

    _backfill_missing_derived_data([adapter], repo, table)

    assert _ledger(repo) == {"t-new", "t-s1"}                        # s2's rows can't be recovered
    assert repo.get_app_meta(COMMANDS_BACKFILL_KEY) == "1"


def test_a_sub_agents_commands_are_backfilled_through_its_parent(tmp_path, repo):
    adapter, table, _ = _pre_ledger_archive(tmp_path, repo)
    _backfill_missing_derived_data([adapter], repo, table)
    assert _ledger(repo) == {"t-s1", "t-s2", "t-sub"}


def test_the_ledger_backfill_takes_several_starts_when_capped_and_then_stops(tmp_path, repo, monkeypatch):
    adapter, table, _ = _pre_ledger_archive(tmp_path, repo)
    monkeypatch.setattr(first_run, "BACKFILL_CAP", 1)
    _backfill_missing_derived_data([adapter], repo, table)
    assert repo.get_app_meta(COMMANDS_BACKFILL_KEY) is None and len(_ledger(repo)) < 3
    for _ in range(4):
        _backfill_missing_derived_data([adapter], repo, table)
    assert _ledger(repo) == {"t-s1", "t-s2", "t-sub"} and repo.get_app_meta(COMMANDS_BACKFILL_KEY) == "1"


def test_a_shell_call_with_no_usable_command_does_not_keep_the_backfill_running(tmp_path, repo):
    """A blank or malformed command has a tool call and never a ledger row; once the backfill
    is marked done, that must not trigger a re-read on every start."""
    adapter, f = _claude(tmp_path, [_asst("m1", [_use("t1", "Bash", command="")])])
    table = load_pricing_table()
    ingest_file(adapter, f, repo, table)
    _backfill_missing_derived_data([adapter], repo, table)
    assert repo.get_app_meta(COMMANDS_BACKFILL_KEY) == "1"
    before = repo.db.execute("SELECT computed_at FROM sessions").fetchone()[0]
    _backfill_missing_derived_data([adapter], repo, table)
    assert repo.db.execute("SELECT computed_at FROM sessions").fetchone()[0] == before


def test_the_ledger_mirrors_the_stored_tool_calls_not_just_this_reads_calls(tmp_path, repo, monkeypatch):
    """Seen on real data (174 of 7,603 commands, two sessions): a re-read can leave a call
    out of its result (the lines are tagged as copied from another session) while the call's
    row is still stored under this session. Every stored shell call gets its ledger row."""
    from argus.collector import pipeline

    adapter, f = _claude(tmp_path, [_asst("m1", [_use("t1", "Bash", command="echo one")])])
    table = load_pricing_table()
    ingest_file(adapter, f, repo, table)
    with repo.transaction():
        repo.db.execute("DELETE FROM commands")
    real = pipeline._drop_fork_copies
    monkeypatch.setattr(pipeline, "_drop_fork_copies",
                        lambda repo, sid, result: real(repo, sid, result).model_copy(update={"tool_calls": []}))
    repo.set_file_offset(str(f), 0)
    ingest_file(adapter, f, repo, table)
    assert set(_rows(repo)) == {"t1"}


def test_a_command_whose_call_is_not_stored_is_left_out(tmp_path, repo, monkeypatch):
    from argus.collector import pipeline

    adapter, f = _claude(tmp_path, [_asst("m1", [_use("t1", "Bash", command="echo one")])])
    real = pipeline._drop_fork_copies
    monkeypatch.setattr(pipeline, "_drop_fork_copies",
                        lambda repo, sid, result: real(repo, sid, result).model_copy(update={"tool_calls": []}))
    ingest_file(adapter, f, repo, load_pricing_table())       # must not raise a FOREIGN KEY error
    assert _rows(repo) == {} and repo.get_session("claude_code:sess1") is not None


def test_a_transcript_larger_than_one_read_is_backfilled_to_its_end(tmp_path, repo, monkeypatch):
    """Seen on real data: two transcripts over 64 MB (the per-read cap) were re-read only up
    to the cap, so the commands in their last third stayed out of the ledger."""
    from argus.adapters.claude_code import ingest_file as claude_ingest

    lines = [_asst(f"m{i}", [_use(f"t{i}", "Bash", command=f"echo {i}")]) for i in range(6)]
    adapter, f = _claude(tmp_path, lines)
    table = load_pricing_table()
    ingest_file(adapter, f, repo, table)
    with repo.transaction():
        repo.db.execute("DELETE FROM commands")
    for key in (first_run.STREAMED_OUTPUT_FIX_KEY, first_run.TOOL_ERRORS_FIX_KEY, "backfill_agent_subagent_type_v1"):
        repo.set_app_meta(key, "1")
    monkeypatch.setattr(claude_ingest, "MAX_TICK_BYTES", len(lines[0]) * 2 + 10)   # two lines per read

    _backfill_missing_derived_data([adapter], repo, table)

    assert _ledger(repo) == {f"t{i}" for i in range(6)}
    assert repo.get_file_offset(str(f)) == f.stat().st_size


def test_sessions_that_can_never_be_filled_do_not_crowd_out_the_rest(tmp_path, repo, monkeypatch):
    """Under the per-run cap, a session whose shell call has no usable command stays a
    candidate for ever; the backfill must still move past it to the sessions behind it."""
    root = tmp_path / ".claude"
    proj = root / "projects" / "C--proj"
    proj.mkdir(parents=True)
    adapter, table = ClaudeCodeAdapter(root), load_pricing_table()
    for sid, cmd in (("a1", ""), ("a2", ""), ("b1", "echo real")):
        f = proj / f"{sid}.jsonl"
        f.write_text(_asst(f"m-{sid}", [_use(f"t-{sid}", "Bash", command=cmd)]).replace('"sess1"', f'"{sid}"') + "\n", encoding="utf-8")
        ingest_file(adapter, f, repo, table)
    with repo.transaction():
        repo.db.execute("DELETE FROM commands")
    for key in (first_run.STREAMED_OUTPUT_FIX_KEY, first_run.TOOL_ERRORS_FIX_KEY, "backfill_agent_subagent_type_v1"):
        repo.set_app_meta(key, "1")
    monkeypatch.setattr(first_run, "BACKFILL_CAP", 1)
    for _ in range(3):
        _backfill_missing_derived_data([adapter], repo, table)
    assert _ledger(repo) == {"t-b1"} and repo.get_app_meta(COMMANDS_BACKFILL_KEY) == "1"
