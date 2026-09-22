"""cost_spike detector.

For each project, compares the trailing-7-day estimated cost (`window`)
against the weekly average of the 28 days immediately before that
(`baseline`). Fires a finding when:

  - window_cost >= 2 * baseline_weekly_cost
  - window_cost >= $5
  - baseline_weekly_cost >= $1

Severity ``warning`` for ratios in [2, 5), ``critical`` for >= 5 — the
same bands as ``tool_error_rate_spike``, so a severity means the same
thing wherever it shows up.

Two deliberate calls:

- **No zero-baseline case.** A project with no spend in the baseline is
  usually a project that simply started this week; "new work costs money"
  is not a behaviour change worth an alert. The ratio path needs a real
  baseline, so a project must have been active before it can spike.
- **The dollar floors are absolute, not relative.** Cost is an estimate
  from the bundled price table, and it is meaningless as *money* for
  Pro/Max users who pay a flat fee — but it still tracks how much work
  the agent did, which is what a spike is really reporting. The $5/$1
  floors keep small projects from firing on rounding.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .base import Finding, iso_at_offset, project_label
from .registry import register

if TYPE_CHECKING:
    from ..store.repository import Repository


_WINDOW_DAYS = 7
_BASELINE_DAYS = 28
_MIN_WINDOW_COST = 5.0
_MIN_BASELINE_WEEKLY_COST = 1.0
_WARNING_MULTIPLE = 2.0
_CRITICAL_MULTIPLE = 5.0
_BASELINE_WEEKS = _BASELINE_DAYS / _WINDOW_DAYS


@register
class CostSpikeDetector:
    name = "cost_spike"

    def detect(self, repo: "Repository", now_iso: str) -> list[Finding]:
        window_start = iso_at_offset(now_iso, _WINDOW_DAYS)
        baseline_start = iso_at_offset(now_iso, _WINDOW_DAYS + _BASELINE_DAYS)

        window_rows = repo.project_turn_stats_in_range(
            start_iso=window_start, end_iso=now_iso
        )
        baseline_rows = repo.project_turn_stats_in_range(
            start_iso=baseline_start, end_iso=window_start
        )
        baseline_by_project = {r["project_path"]: r for r in baseline_rows}

        findings: list[Finding] = []
        for w in window_rows:
            project = w["project_path"]
            window_cost = float(w["cost"])
            if window_cost < _MIN_WINDOW_COST:
                continue

            b = baseline_by_project.get(project)
            if b is None:
                continue
            baseline_cost = float(b["cost"])
            # The baseline is four times as long as the window, so compare
            # like with like: spend per week, not spend per window.
            baseline_weekly = baseline_cost / _BASELINE_WEEKS
            if baseline_weekly < _MIN_BASELINE_WEEKLY_COST:
                continue

            multiple = window_cost / baseline_weekly
            if multiple < _WARNING_MULTIPLE:
                continue

            label = project_label(project)
            severity = "critical" if multiple >= _CRITICAL_MULTIPLE else "warning"
            findings.append(
                Finding(
                    detector=self.name,
                    dedup_key=project,
                    severity=severity,
                    title=(
                        f"{label} cost {multiple:.1f}x its weekly baseline "
                        f"(${window_cost:,.2f} this week)"
                    ),
                    message=(
                        f"Last 7d: ${window_cost:,.2f} over "
                        f"{int(w['turns']):,} turns. Prior 28d: "
                        f"${baseline_weekly:,.2f}/week over "
                        f"{int(b['turns']):,} turns."
                    ),
                    metadata={
                        "project_path": project,
                        "project": label,
                        "window_cost": window_cost,
                        "baseline_weekly_cost": baseline_weekly,
                        "window_turns": int(w["turns"]),
                        "baseline_turns": int(b["turns"]),
                        "multiple": multiple,
                    },
                )
            )
        return findings
