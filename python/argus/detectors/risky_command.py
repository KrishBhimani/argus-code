"""risky_command detector.

One finding per session (sub-agents included) that ran a high-risk shell command in the
trailing 7 days: a recursive forced delete aimed at root, home or a drive, a force push, a
download piped into a shell, a destructive SQL statement, a raw disk write.

Deliberate calls:

- **Only `high`.** `medium` and `low` (deleting a build folder, `git reset --hard`, publishing)
  are routine and stay on the Commands page; alerting on them would bury the rest.
- **`warning`, never `critical`.** The ledger's rules match a command's text and do not
  understand it (see `argus.commands.classify`): the alert says "look at this session",
  not "harm was done".
- **Keyed by session**, so ten risky commands in one session are one alert, and it resolves
  on its own once the session leaves the window.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .base import Finding, iso_at_offset, project_label
from .registry import register

if TYPE_CHECKING:
    from ..store.repository import Repository

_WINDOW_DAYS = 7


@register
class RiskyCommandDetector:
    name = "risky_command"

    def detect(self, repo: "Repository", now_iso: str) -> list[Finding]:
        by_session: dict[str, dict] = {}
        for c in repo.notable_commands_in_range(start_iso=iso_at_offset(now_iso, _WINDOW_DAYS), end_iso=now_iso):
            if c["severity"] != "high":
                continue
            s = by_session.setdefault(c["session_id"], {"project_path": c["project_path"], "commands": 0, "rules": {}})
            s["commands"] += 1
            for r in c["risks"]:
                if r.get("severity") == "high":
                    s["rules"][r["rule"]] = s["rules"].get(r["rule"], 0) + 1
        findings: list[Finding] = []
        for sid, s in by_session.items():
            label, n = project_label(s["project_path"]), s["commands"]
            rules = ", ".join(f"{rule} ×{k}" for rule, k in sorted(s["rules"].items()))
            findings.append(Finding(
                detector=self.name, dedup_key=sid, severity="warning",
                title=f"{label}: {n} high-risk command{'' if n == 1 else 's'} in one session",
                message=f"{rules}. The rules match a command's text; open the session to see what ran.",
                metadata={"session_id": sid, "project": label, "project_path": s["project_path"],
                          "commands": n, "rules": dict(sorted(s["rules"].items()))},
            ))
        return findings
