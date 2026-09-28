"""argus work github enable|disable|refresh go through the same functions as the dashboard."""
from __future__ import annotations

from typer.testing import CliRunner

from argus.cli import app
from argus.work import github
from argus.work.db import open_work_db

runner = CliRunner()


def test_enable_refresh_disable_and_status(tmp_path, monkeypatch):
    calls: list = []
    monkeypatch.setattr(github, "refresh", lambda conn, now: calls.append(now) or {"checked": 2, "error": None})
    d = str(tmp_path)
    r = runner.invoke(app, ["work", "github", "enable", "--data-dir", d])
    assert r.exit_code == 0, r.output
    assert "GitHub PR status: on" in r.output and len(calls) == 1
    r = runner.invoke(app, ["work", "github", "refresh", "--data-dir", d])
    assert r.exit_code == 0 and "checked 2 PR(s)" in r.output and len(calls) == 2
    assert "GitHub: on" in runner.invoke(app, ["work", "status", "--data-dir", d]).output
    assert runner.invoke(app, ["work", "github", "disable", "--data-dir", d]).exit_code == 0
    conn = open_work_db(tmp_path)
    assert github.is_enabled(conn) is False
    conn.close()
