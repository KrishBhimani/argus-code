"""cache_hit_drop detector tests."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from argus.detectors.cache_hit_drop import CacheHitDropDetector
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
    cache_read: int,
    fresh: int = 0,
    cache_write: int = 0,
) -> str:
    """One synthetic session in `project` with `turns` identical turns."""
    sid = f"claude_code:{project.strip('/').replace('/', '_')}-{days_ago}"
    repo.upsert_session(
        session_factory(sid, _ts(days_ago), project_path=project)
    )
    for i in range(turns):
        repo.upsert_turn(
            turn_factory(
                f"{sid}:{i}",
                sid,
                _ts(days_ago),
                cost=0.0,
                fresh=fresh,
                output=0,
                cache_read=cache_read,
                cache_write=cache_write,
            )
        )
    return sid


@pytest.fixture
def detector():
    return CacheHitDropDetector()


def test_empty_repo_returns_no_findings(repo, detector):
    assert detector.detect(repo, NOW) == []


def test_below_window_turn_floor_returns_nothing(repo, detector):
    _seed(repo, "/a", days_ago=2, turns=40, cache_read=10, fresh=90)
    _seed(repo, "/a", days_ago=20, turns=60, cache_read=90, fresh=10)
    assert detector.detect(repo, NOW) == []


def test_below_baseline_turn_floor_returns_nothing(repo, detector):
    _seed(repo, "/a", days_ago=2, turns=60, cache_read=10, fresh=90)
    _seed(repo, "/a", days_ago=20, turns=40, cache_read=90, fresh=10)
    assert detector.detect(repo, NOW) == []


def test_project_without_a_baseline_returns_nothing(repo, detector):
    _seed(repo, "/a", days_ago=2, turns=60, cache_read=10, fresh=90)
    assert detector.detect(repo, NOW) == []


def test_weak_baseline_cannot_drop(repo, detector):
    """A 40% baseline never cached well enough for a drop to mean anything."""
    _seed(repo, "/a", days_ago=2, turns=60, cache_read=10, fresh=90)   # 10%
    _seed(repo, "/a", days_ago=20, turns=60, cache_read=40, fresh=60)  # 40%
    assert detector.detect(repo, NOW) == []


def test_drop_below_15_points_returns_nothing(repo, detector):
    _seed(repo, "/a", days_ago=2, turns=60, cache_read=80, fresh=20)   # 80%
    _seed(repo, "/a", days_ago=20, turns=60, cache_read=90, fresh=10)  # 90%
    assert detector.detect(repo, NOW) == []


def test_warning_severity_for_a_15_to_30_point_drop(repo, detector):
    _seed(repo, "/home/me/api", days_ago=2, turns=60, cache_read=70, fresh=30)
    _seed(repo, "/home/me/api", days_ago=20, turns=60, cache_read=90, fresh=10)
    findings = detector.detect(repo, NOW)
    assert len(findings) == 1
    f = findings[0]
    assert f.detector == "cache_hit_drop"
    assert f.dedup_key == "/home/me/api"
    assert f.severity == "warning"
    assert f.metadata["project"] == "api"  # title uses the last path segment
    assert "api" in f.title
    assert pytest.approx(f.metadata["window_rate"], rel=1e-6) == 0.70
    assert pytest.approx(f.metadata["baseline_rate"], rel=1e-6) == 0.90
    assert pytest.approx(f.metadata["drop"], rel=1e-6) == 0.20
    assert f.metadata["window_turns"] == 60
    assert f.metadata["baseline_turns"] == 60


def test_critical_severity_for_a_30_point_plus_drop(repo, detector):
    _seed(repo, "/a", days_ago=2, turns=60, cache_read=50, fresh=50)   # 50%
    _seed(repo, "/a", days_ago=20, turns=60, cache_read=90, fresh=10)  # 90%
    assert detector.detect(repo, NOW)[0].severity == "critical"


def test_cache_writes_count_as_misses(repo, detector):
    """Re-created cache (writes) is the signal — fresh input need not move."""
    _seed(repo, "/a", days_ago=2, turns=60, cache_read=50, fresh=10, cache_write=40)
    _seed(repo, "/a", days_ago=20, turns=60, cache_read=90, fresh=10)
    findings = detector.detect(repo, NOW)
    assert len(findings) == 1
    assert pytest.approx(findings[0].metadata["window_rate"], rel=1e-6) == 0.50
    assert findings[0].severity == "critical"


def test_turns_without_input_tokens_are_ignored(repo, detector):
    """An all-zero window has no rate to compare — skip, don't divide by zero."""
    _seed(repo, "/a", days_ago=2, turns=60, cache_read=0, fresh=0)
    _seed(repo, "/a", days_ago=20, turns=60, cache_read=90, fresh=10)
    assert detector.detect(repo, NOW) == []


def test_projects_are_independent(repo, detector):
    _seed(repo, "/broken", days_ago=2, turns=60, cache_read=50, fresh=50)
    _seed(repo, "/broken", days_ago=20, turns=60, cache_read=90, fresh=10)
    _seed(repo, "/fine", days_ago=2, turns=60, cache_read=88, fresh=12)
    _seed(repo, "/fine", days_ago=20, turns=60, cache_read=90, fresh=10)
    keys = {f.dedup_key for f in detector.detect(repo, NOW)}
    assert keys == {"/broken"}


def test_baseline_window_does_not_overlap_window(repo, detector):
    """Turns at day -5 are inside the window, never in the baseline."""
    _seed(repo, "/a", days_ago=5, turns=60, cache_read=50, fresh=50)
    _seed(repo, "/a", days_ago=20, turns=60, cache_read=90, fresh=10)
    findings = detector.detect(repo, NOW)
    assert pytest.approx(findings[0].metadata["window_rate"], rel=1e-6) == 0.50
    assert pytest.approx(findings[0].metadata["baseline_rate"], rel=1e-6) == 0.90


def test_detector_is_pure_no_writes(repo, detector):
    _seed(repo, "/a", days_ago=2, turns=60, cache_read=50, fresh=50)
    _seed(repo, "/a", days_ago=20, turns=60, cache_read=90, fresh=10)
    detector.detect(repo, NOW)
    assert repo.list_alerts(limit=10) == []
