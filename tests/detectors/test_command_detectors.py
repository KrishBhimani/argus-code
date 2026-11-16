"""risky_command and secret_in_command: alerts from the command ledger."""
from __future__ import annotations

from datetime import datetime, timedelta

from argus.detectors.registry import registered_detector_names
from argus.detectors.risky_command import RiskyCommandDetector
from argus.detectors.secret_in_command import SecretInCommandDetector
from argus.schema.types import Command, ToolCall
from tests.conftest import session_factory

NOW = "2026-05-27T12:00:00Z"


def _ts(days_ago: float) -> str:
    return (datetime.fromisoformat(NOW.replace("Z", "+00:00")) - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


def add(repo, sid: str, use_id: str, *, days_ago: float = 1, severity: str | None = None, rule: str = "delete-recursive",
        secrets: list[str] | None = None, project: str = "/work/alpha") -> None:
    if repo.get_session(sid) is None:
        repo.upsert_session(session_factory(sid, _ts(days_ago), project_path=project))
    cid = f"{sid}:{use_id}"
    repo.upsert_tool_calls([ToolCall(id=cid, session_id=sid, turn_index=0, tool_name="Bash", is_error=0, input_size=1,
                                     subagent_type=None, timestamp=_ts(days_ago))])
    repo.upsert_commands([Command(id=cid, session_id=sid, timestamp=_ts(days_ago), shell="bash", cwd=project,
                                  command="x", truncated=False, background=False, severity=severity,
                                  risks=[{"rule": rule, "severity": severity}] if severity else [], secrets=secrets or [])])


def test_both_are_registered():
    assert {"risky_command", "secret_in_command"} <= set(registered_detector_names())


def test_one_finding_per_session_with_a_high_risk_command(repo):
    add(repo, "claude_code:a", "t1", severity="high")
    add(repo, "claude_code:a", "t2", severity="high", rule="git-force-push")
    add(repo, "claude_code:a/agent-x", "t3", severity="high")          # a sub-agent's count with its session
    add(repo, "claude_code:a", "t4", severity="low")                   # routine: not an alert
    add(repo, "claude_code:b", "t5", severity="medium", project="/work/beta")
    add(repo, "claude_code:c", "t6", severity="high", days_ago=9)      # outside the 7 days
    (f,) = RiskyCommandDetector().detect(repo, NOW)
    assert (f.detector, f.dedup_key, f.severity) == ("risky_command", "claude_code:a", "warning")
    assert f.title == "alpha: 3 high-risk commands in one session"
    assert "delete-recursive ×2" in f.message and "git-force-push ×1" in f.message
    assert f.metadata == {"session_id": "claude_code:a", "project": "alpha", "project_path": "/work/alpha",
                          "commands": 3, "rules": {"delete-recursive": 2, "git-force-push": 1}}


def test_a_single_command_reads_in_the_singular(repo):
    add(repo, "claude_code:a", "t1", severity="high")
    (f,) = RiskyCommandDetector().detect(repo, NOW)
    assert f.title == "alpha: 1 high-risk command in one session"


def test_a_secret_in_a_command_is_one_finding_per_session(repo):
    add(repo, "claude_code:a", "t1", secrets=["github-token"])
    add(repo, "claude_code:a", "t2", secrets=["assignment", "env-file"])
    add(repo, "claude_code:a", "t3", secrets=["env-file"])             # read a .env: noted, not a secret in the command
    add(repo, "claude_code:b", "t4", secrets=["env-file"], project="/work/beta")
    add(repo, "claude_code:c", "t5", secrets=["api-key"], days_ago=9)
    (f,) = SecretInCommandDetector().detect(repo, NOW)
    assert (f.detector, f.dedup_key, f.severity) == ("secret_in_command", "claude_code:a", "warning")
    assert f.title == "alpha: a secret appeared in 2 commands"
    assert "assignment, github-token" in f.message and "rotate" in f.message.lower()
    assert f.metadata["kinds"] == ["assignment", "github-token"] and f.metadata["session_id"] == "claude_code:a"


def test_nothing_in_the_ledger_means_no_findings(repo):
    assert RiskyCommandDetector().detect(repo, NOW) == [] and SecretInCommandDetector().detect(repo, NOW) == []
