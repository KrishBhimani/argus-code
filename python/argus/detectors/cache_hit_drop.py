"""cache_hit_drop detector.

For each project, compares the trailing-7-day prompt-cache hit rate
(`window`) against the 28 days immediately before that (`baseline`).
Fires a finding when:

  - baseline_rate - window_rate >= 15 percentage points
  - baseline_rate >= 50%
  - window_turns >= 50 and baseline_turns >= 50

Severity ``warning`` for a drop in [15, 30) points, ``critical`` for >= 30.

**Hit rate here is input-side:** ``cache_read / (cache_read + cache_write
+ fresh_input)``. Output tokens are deliberately excluded — the dashboard's
"cache read share" divides by *all* tokens, which sags whenever a session
simply writes more code, and that is not a caching regression. Cache
*writes* stay in the denominator because they are what a miss looks like:
a prefix that had to be re-cached. A project whose `CLAUDE.md`, MCP tool
definitions or system prompt churn re-creates the cache every turn, and
that shows up as writes displacing reads.

The drop is measured in **percentage points, not as a ratio**. Near the
top of the range a ratio is uninformative (96% -> 90% is "0.94x" but
doubles the uncached input), and the cost of a regression scales with the
points lost, not with their quotient.

The baseline floor is what makes the rule meaningful: a project that never
cached well has nothing to drop, and its rate wanders for reasons that
aren't worth an alert.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .base import Finding, iso_at_offset, project_label
from .registry import register

if TYPE_CHECKING:
    from ..store.repository import Repository


_WINDOW_DAYS = 7
_BASELINE_DAYS = 28
_MIN_TURNS = 50
_MIN_BASELINE_RATE = 0.50
_WARNING_DROP = 0.15  # 15 percentage points
_CRITICAL_DROP = 0.30  # 30 percentage points


def _hit_rate(row: dict) -> float | None:
    """Cache hits as a share of input tokens, or None when there are none."""
    total = (
        int(row["cache_read"]) + int(row["cache_write"]) + int(row["fresh_input"])
    )
    if total <= 0:
        return None
    return int(row["cache_read"]) / total


@register
class CacheHitDropDetector:
    name = "cache_hit_drop"

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
            window_turns = int(w["turns"])
            if window_turns < _MIN_TURNS:
                continue

            b = baseline_by_project.get(project)
            if b is None:
                continue
            baseline_turns = int(b["turns"])
            if baseline_turns < _MIN_TURNS:
                continue

            window_rate = _hit_rate(w)
            baseline_rate = _hit_rate(b)
            if window_rate is None or baseline_rate is None:
                continue
            if baseline_rate < _MIN_BASELINE_RATE:
                continue

            drop = baseline_rate - window_rate
            if drop < _WARNING_DROP:
                continue

            label = project_label(project)
            severity = "critical" if drop >= _CRITICAL_DROP else "warning"
            findings.append(
                Finding(
                    detector=self.name,
                    dedup_key=project,
                    severity=severity,
                    title=(
                        f"{label} cache hit rate fell to {window_rate * 100:.0f}% "
                        f"this week (baseline {baseline_rate * 100:.0f}%)"
                    ),
                    message=(
                        f"Last 7d: {window_rate * 100:.1f}% of input tokens served "
                        f"from cache over {window_turns:,} turns. Prior 28d: "
                        f"{baseline_rate * 100:.1f}% over {baseline_turns:,} turns. "
                        f"That is {drop * 100:.1f} points of input re-sent uncached."
                    ),
                    metadata={
                        "project_path": project,
                        "project": label,
                        "window_rate": window_rate,
                        "baseline_rate": baseline_rate,
                        "drop": drop,
                        "window_turns": window_turns,
                        "baseline_turns": baseline_turns,
                    },
                )
            )
        return findings
