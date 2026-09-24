"""Day buckets follow the viewer's timezone; prior-window totals don't overlap (H8).

REGRESSION: the server bucketed days as ``substr(timestamp,1,10)`` (a UTC date)
while the dashboard indexed those maps with *local* date keys. In IST at 02:00
the chart asked for '2026-09-23' while the server only had '2026-09-22', so it
plotted 0 next to a Tokens tile of 91.6M. The "vs prior window" deltas summed
whole calendar days that overlapped the rolling current window.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from argus.collector.first_run import IngestStatus
from argus.pricing.types import PricingTable
from argus.server.app import ServerOpts, build_app
from tests.conftest import session_factory, turn_factory

LOOPBACK = "http://127.0.0.1"


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _client(repo, tmp_path: Path) -> TestClient:
    app = build_app(
        repo,
        ServerOpts(
            pricing_table_version="v1",
            ingest_status=lambda: IngestStatus(foreground_complete=True, pending=0, processed=0, total=0),
            dashboard_dir=tmp_path / "absent",
            port=0,
            adapters=[],
            pricing_table=PricingTable(version="v1", models={}),
        ),
    )
    return TestClient(app, base_url=LOOPBACK)


def test_overview_buckets_days_in_the_callers_timezone(repo, tmp_path: Path):
    # 20:40 UTC on the 22nd is 02:10 on the 23rd in IST (UTC+5:30 = +330 min).
    ts = "2026-09-22T20:40:00.000Z"
    repo.upsert_session(session_factory("a", ts))
    repo.upsert_turn(turn_factory("a:t1", "a", ts, fresh=100, output=0))
    client = _client(repo, tmp_path)

    assert client.get("/api/overview?window=all").json()["tokens_by_day"] == {"2026-09-22": 100}
    ist = client.get("/api/overview?window=all&tz=330").json()
    assert ist["tokens_by_day"] == {"2026-09-23": 100}
    assert ist["cost_by_day"].keys() == {"2026-09-23"}
    west = client.get("/api/overview?window=all&tz=-600").json()  # UTC-10
    assert west["tokens_by_day"] == {"2026-09-22": 100}


def test_trends_bucket_days_in_the_callers_timezone(repo, tmp_path: Path):
    ts = "2026-09-22T20:40:00.000Z"
    repo.upsert_session(session_factory("a", ts))
    repo.upsert_turn(turn_factory("a:t1", "a", ts))
    body = _client(repo, tmp_path).get("/api/trends?granularity=day&groupBy=agent&tz=330").json()
    assert [p["bucket"] for p in body["points"]] == ["2026-09-23"]


def test_timezone_is_part_of_the_cache_key(repo, tmp_path: Path):
    ts = "2026-09-22T20:40:00.000Z"
    repo.upsert_session(session_factory("a", ts))
    repo.upsert_turn(turn_factory("a:t1", "a", ts))
    client = _client(repo, tmp_path)
    assert list(client.get("/api/overview?window=all&tz=0").json()["tokens_by_day"]) == ["2026-09-22"]
    assert list(client.get("/api/overview?window=all&tz=330").json()["tokens_by_day"]) == ["2026-09-23"]


def test_out_of_range_timezone_is_rejected(repo, tmp_path: Path):
    # app.py maps request-validation errors to 400 (project contract).
    assert _client(repo, tmp_path).get("/api/overview?window=7d&tz=100000").status_code == 400


def test_prior_window_is_the_equal_length_range_just_before(repo, tmp_path: Path):
    now = datetime.now(timezone.utc)
    rows = {
        "cur": now - timedelta(days=2),          # inside the current 7d window
        "edge": now - timedelta(days=6, hours=23),  # current window, oldest day
        "prior": now - timedelta(days=9),        # the 7 days before it
        "old": now - timedelta(days=15),         # outside both
    }
    for sid, dt in rows.items():
        repo.upsert_session(session_factory(sid, _iso(dt)))
        repo.upsert_turn(turn_factory(f"{sid}:t", sid, _iso(dt), fresh=10, output=0, cost=1.0))
    body = _client(repo, tmp_path).get("/api/overview?window=7d").json()

    assert body["total_tokens"] == 20 and body["session_count"] == 2
    # Only the "prior" turn: no overlap with the current window's first day,
    # and sessions are counted the same way as session_count (turns in range).
    assert body["prior_window"] == {"tokens": 10, "cost_usd": 1.0, "sessions": 1}


def test_prior_window_is_null_for_all_time(repo, tmp_path: Path):
    assert _client(repo, tmp_path).get("/api/overview?window=all").json()["prior_window"] is None
