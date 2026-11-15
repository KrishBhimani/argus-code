"""secret_in_command detector.

One finding per session (sub-agents included) in which a shell command carried a secret in
the trailing 7 days: a provider-shaped key, an auth header, a password in a URL, a
`--token` style flag, or a literal assigned to a name that says secret.

Argus stores the value masked, so the alert is not about the archive. It is about the
secret itself: it is still in the agent's own transcript and was sent to the model, so the
advice is to rotate it.

Reading a `.env` file is noted on the command but is not a finding: the command holds no
secret, and agents read env files constantly.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .base import Finding, iso_at_offset, project_label
from .registry import register

if TYPE_CHECKING:
    from ..store.repository import Repository

_WINDOW_DAYS = 7
_NOT_A_SECRET = "env-file"


@register
class SecretInCommandDetector:
    name = "secret_in_command"

    def detect(self, repo: "Repository", now_iso: str) -> list[Finding]:
        by_session: dict[str, dict] = {}
        for c in repo.notable_commands_in_range(start_iso=iso_at_offset(now_iso, _WINDOW_DAYS), end_iso=now_iso):
            kinds = [k for k in c["secrets"] if k != _NOT_A_SECRET]
            if not kinds:
                continue
            s = by_session.setdefault(c["session_id"], {"project_path": c["project_path"], "commands": 0, "kinds": set()})
            s["commands"] += 1
            s["kinds"].update(kinds)
        findings: list[Finding] = []
        for sid, s in by_session.items():
            label, n, kinds = project_label(s["project_path"]), s["commands"], sorted(s["kinds"])
            findings.append(Finding(
                detector=self.name, dedup_key=sid, severity="warning",
                title=f"{label}: a secret appeared in {n} command{'' if n == 1 else 's'}",
                message=(f"Kinds: {', '.join(kinds)}. Argus stores the value masked, but it is still in the "
                         "agent's transcript and was sent to the model: rotate it."),
                metadata={"session_id": sid, "project": label, "project_path": s["project_path"],
                          "commands": n, "kinds": kinds},
            ))
        return findings
