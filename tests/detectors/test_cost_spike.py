"""cost_spike detector tests."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from argus.detectors.cost_spike import CostSpikeDetector
from tests.conftest import session_factory, turn_factory


NOW = "2026-05-27T12:00:00Z"


def _ts(days_ago: float) -> str:
    dt = datetime.fromisoformat(NOW.replace("Z", "+00:00")) - timedelta(days=days_ago)
    return dt.isoformat().replace("+00:00", "Z")


def _seed(
    repo,
    project: str,
    *,
    days_ago: float,
    turns: int,
    cost: float,
    sid: str | None = None,
    established: bool = True,
) -> str:
    """One synthetic session in `project` with `turns` turns of `cost` each.

    ``established`` (baseline seeds only) adds a $0 turn 40 days back, before
    the baseline starts, so the project has a full 4-week history and the
    baseline is its spend / 4 weeks. Pass False for a project that is younger
    than the baseline window.
    """
    sid = sid or f"claude_code:{project.strip('/').replace('/', '_')}-{days_ago}"
    repo.upsert_session(
        session_factory(sid, _ts(days_ago), project_path=project)
    )
    for i in range(turns):
        repo.upsert_turn(
            turn_factory(f"{sid}:{i}", sid, _ts(days_ago), cost=cost, output=0)
        )
    if established and days_ago > 7:
        repo.upsert_turn(turn_factory(f"{sid}:origin", sid, _ts(40), cost=0.0, output=0))
    return sid


@pytest.fixture
def detector():
    return CostSpikeDetector()


def test_empty_repo_returns_no_findings(repo, detector):
    assert detector.detect(repo, NOW) == []


def test_below_window_cost_floor_returns_nothing(repo, detector):
    _seed(repo, "/a", days_ago=2, turns=4, cost=1.0)      # $4 < $5 floor
    _seed(repo, "/a", days_ago=20, turns=4, cost=0.5)     # $2 → $0.50/wk
    assert detector.detect(repo, NOW) == []


def test_below_baseline_weekly_floor_returns_nothing(repo, detector):
    _seed(repo, "/a", days_ago=2, turns=10, cost=5.0)     # $50
    _seed(repo, "/a", days_ago=20, turns=4, cost=0.5)     # $2 → $0.50/wk
    assert detector.detect(repo, NOW) == []


def test_project_without_a_baseline_returns_nothing(repo, detector):
    """A project that only started this week is new work, not a spike."""
    _seed(repo, "/a", days_ago=2, turns=10, cost=10.0)
    assert detector.detect(repo, NOW) == []


def test_window_below_2x_weekly_baseline_returns_nothing(repo, detector):
    _seed(repo, "/a", days_ago=2, turns=10, cost=2.0)     # $20
    _seed(repo, "/a", days_ago=20, turns=10, cost=6.0)    # $60 → $15/wk → 1.3x
    assert detector.detect(repo, NOW) == []


def test_warning_severity_when_window_is_2x_to_5x_baseline(repo, detector):
    _seed(repo, "/home/me/api", days_ago=2, turns=10, cost=4.0)    # $40
    _seed(repo, "/home/me/api", days_ago=20, turns=10, cost=6.0)   # $15/wk → 2.67x
    findings = detector.detect(repo, NOW)
    assert len(findings) == 1
    f = findings[0]
    assert f.detector == "cost_spike"
    assert f.dedup_key == "/home/me/api"
    assert f.severity == "warning"
    assert f.metadata["project"] == "api"  # title uses the last path segment
    assert "api" in f.title
    assert pytest.approx(f.metadata["multiple"], rel=1e-2) == 2.67
    assert pytest.approx(f.metadata["window_cost"], rel=1e-6) == 40.0
    assert pytest.approx(f.metadata["baseline_weekly_cost"], rel=1e-6) == 15.0
    assert f.metadata["window_turns"] == 10
    assert f.metadata["baseline_turns"] == 10


def test_critical_severity_when_window_is_5x_plus_baseline(repo, detector):
    _seed(repo, "/a", days_ago=2, turns=10, cost=10.0)    # $100
    _seed(repo, "/a", days_ago=20, turns=10, cost=6.0)    # $15/wk → 6.7x
    assert detector.detect(repo, NOW)[0].severity == "critical"


def test_baseline_is_compared_per_week_not_per_window(repo, detector):
    """$60 over 28d is $15/wk — a $40 week is a spike even though 40 < 60."""
    _seed(repo, "/a", days_ago=2, turns=10, cost=4.0)
    _seed(repo, "/a", days_ago=20, turns=10, cost=6.0)
    assert len(detector.detect(repo, NOW)) == 1


def test_projects_are_independent(repo, detector):
    _seed(repo, "/hot", days_ago=2, turns=10, cost=10.0)
    _seed(repo, "/hot", days_ago=20, turns=10, cost=6.0)
    _seed(repo, "/steady", days_ago=2, turns=10, cost=1.5)
    _seed(repo, "/steady", days_ago=20, turns=10, cost=6.0)
    keys = {f.dedup_key for f in detector.detect(repo, NOW)}
    assert keys == {"/hot"}


def test_baseline_window_does_not_overlap_window(repo, detector):
    """Spend at day -5 is inside the window, never in the baseline."""
    _seed(repo, "/a", days_ago=5, turns=10, cost=10.0)
    _seed(repo, "/a", days_ago=20, turns=10, cost=6.0)
    findings = detector.detect(repo, NOW)
    assert pytest.approx(findings[0].metadata["window_cost"], rel=1e-6) == 100.0
    assert pytest.approx(findings[0].metadata["baseline_weekly_cost"], rel=1e-6) == 15.0


def test_subagent_spend_counts_toward_its_parent_project(repo, detector):
    """Sub-agent turns (`parent/sub` ids) roll up to the parent's project.

    The sub-session is given a different `project_path` on purpose: only the
    collapse-to-parent can put its $30 on `/a`, so the assertion can't pass
    by accident.
    """
    parent = _seed(repo, "/a", days_ago=2, turns=10, cost=1.0)     # $10
    repo.upsert_session(
        session_factory(f"{parent}/sub", _ts(2), project_path="/elsewhere")
    )
    for i in range(10):
        repo.upsert_turn(
            turn_factory(f"{parent}/sub:{i}", f"{parent}/sub", _ts(2),
                         cost=3.0, output=0)
        )
    _seed(repo, "/a", days_ago=20, turns=10, cost=6.0)             # $15/wk
    findings = detector.detect(repo, NOW)
    assert [f.dedup_key for f in findings] == ["/a"]
    assert pytest.approx(findings[0].metadata["window_cost"], rel=1e-6) == 40.0


def test_detector_is_pure_no_writes(repo, detector):
    _seed(repo, "/a", days_ago=2, turns=10, cost=10.0)
    _seed(repo, "/a", days_ago=20, turns=10, cost=6.0)
    detector.detect(repo, NOW)
    assert repo.list_alerts(limit=10) == []


# ─── Review follow-up: a baseline the project only partly existed for ─────


def test_project_younger_than_the_baseline_is_measured_over_its_own_weeks(repo, detector):
    """REGRESSION: the baseline was spend / 4 weeks even for a project that
    existed for only part of those 4 weeks. A steady $10/day project in its
    second week read as $30/4 = $7.50/wk and fired a critical "9.3x" spike.
    On a real archive 77 of 137 backtested alerts were this artifact."""
    for d in range(1, 11):  # $10/day, every day for the last 10 days
        _seed(repo, "/new", days_ago=d, turns=1, cost=10.0, established=False)
    assert detector.detect(repo, NOW) == []


def test_project_with_less_than_a_week_of_history_is_skipped(repo, detector):
    """Two days of baseline is too thin to call anything a spike."""
    _seed(repo, "/thin", days_ago=8, turns=1, cost=20.0, established=False)  # flat /4: $5/wk -> "20x"
    _seed(repo, "/thin", days_ago=2, turns=10, cost=10.0)
    assert detector.detect(repo, NOW) == []


def test_young_project_spike_uses_its_active_weeks(repo, detector):
    """Three weeks of $30/wk, then $210 this week: 7x of its own weekly rate
    (a flat /4 would have said 9.3x)."""
    _seed(repo, "/mid", days_ago=28, turns=1, cost=0.0, established=False)  # born 28d ago
    _seed(repo, "/mid", days_ago=20, turns=9, cost=10.0, established=False)  # $90 over 3 weeks
    _seed(repo, "/mid", days_ago=2, turns=21, cost=10.0)                      # $210
    (f,) = detector.detect(repo, NOW)
    assert f.severity == "critical"
    assert abs(f.metadata["multiple"] - 7.0) < 1e-9
    assert f.metadata["baseline_days"] == 21
    assert "Prior 21d" in f.message
