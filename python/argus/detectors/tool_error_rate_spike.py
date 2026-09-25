"""tool_error_rate_spike detector.

For each tool, compares the trailing-7-day error rate (`window`) against
the 28 days immediately before that (`baseline`). Fires a finding when:

  - window_rate >= 2 * baseline_rate
  - window_calls >= 20
  - baseline_calls >= 20

Severity ``warning`` for ratios in [2, 5), ``critical`` for >= 5. The
20-call floor in each window suppresses the worst of the low-volume
noise; deliberate decision to *not* add an absolute-rate floor on the
ratio path for v1.

Special case: a tool that had zero errors across the baseline but starts
failing in the window (>= 5%) fires ``critical`` with a distinct title.
That's the most informative behavior change and the pure-ratio rule can't
express it (division by zero).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .base import Finding, iso_at_offset
from .registry import register

if TYPE_CHECKING:
    from ..store.repository import Repository


_WINDOW_DAYS = 7
_BASELINE_DAYS = 28
_MIN_CALLS = 20
_WARNING_MULTIPLE = 2.0
_CRITICAL_MULTIPLE = 5.0
_ZERO_BASELINE_MIN_WINDOW_RATE = 0.05  # 5%


@register
class ToolErrorRateSpikeDetector:
    name = "tool_error_rate_spike"

    def detect(self, repo: "Repository", now_iso: str) -> list[Finding]:
        window_start = iso_at_offset(now_iso, _WINDOW_DAYS)
        baseline_start = iso_at_offset(now_iso, _WINDOW_DAYS + _BASELINE_DAYS)

        window_rows = repo.tool_call_stats_in_range(
            start_iso=window_start, end_iso=now_iso
        )
        baseline_rows = repo.tool_call_stats_in_range(
            start_iso=baseline_start, end_iso=window_start
        )
        baseline_by_tool = {r["tool_name"]: r for r in baseline_rows}

        findings: list[Finding] = []
        for w in window_rows:
            tool = w["tool_name"]
            window_calls = int(w["calls"])
            window_errors = int(w["errors"])
            if window_calls < _MIN_CALLS:
                continue

            b = baseline_by_tool.get(tool)
            if b is None:
                continue
            baseline_calls = int(b["calls"])
            baseline_errors = int(b["errors"])
            if baseline_calls < _MIN_CALLS:
                continue

            window_rate = window_errors / window_calls
            baseline_rate = baseline_errors / baseline_calls

            if baseline_rate == 0:
                # "Broke from zero" — a previously-clean tool starting to
                # fail. Fire critical when window_rate clears the noise
                # floor; the call-count gates above already guarantee
                # statistical relevance.
                if window_rate < _ZERO_BASELINE_MIN_WINDOW_RATE:
                    continue
                findings.append(
                    Finding(
                        detector=self.name,
                        dedup_key=tool,
                        severity="critical",
                        title=f"{tool} started failing this week (no prior errors)",
                        message=(
                            f"Last 7d: {window_rate * 100:.1f}% over {window_calls} calls. "
                            f"Prior 28d: 0 errors over {baseline_calls} calls."
                        ),
                        metadata={
                            "tool_name": tool,
                            "window_rate": window_rate,
                            "baseline_rate": 0.0,
                            "window_calls": window_calls,
                            "baseline_calls": baseline_calls,
                        },
                    )
                )
                continue

            multiple = window_rate / baseline_rate
            if multiple < _WARNING_MULTIPLE:
                continue

            severity = "critical" if multiple >= _CRITICAL_MULTIPLE else "warning"
            findings.append(
                Finding(
                    detector=self.name,
                    dedup_key=tool,
                    severity=severity,
                    title=(
                        f"{tool} error rate jumped to {window_rate * 100:.1f}% "
                        f"this week (baseline {baseline_rate * 100:.1f}%)"
                    ),
                    message=(
                        f"Last 7d: {window_rate * 100:.1f}% over {window_calls} calls. "
                        f"Prior 28d: {baseline_rate * 100:.1f}% over {baseline_calls} calls."
                    ),
                    metadata={
                        "tool_name": tool,
                        "window_rate": window_rate,
                        "baseline_rate": baseline_rate,
                        "window_calls": window_calls,
                        "baseline_calls": baseline_calls,
                        "multiple": multiple,
                    },
                )
            )
        return findings
