from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from argus.collector.first_run import IngestStatus
from argus.pricing.types import PricingTable
from argus.server.app import ServerOpts, build_app
from argus.store.repository import Repository
from argus.store.db import open_db
from tests.work.test_queries import _world

LOOPBACK = "http://127.0.0.1"


def _client(tmp_path: Path, work: bool) -> TestClient:
    repo = Repository(open_db(tmp_path / "argus.db", read_only=True))
    app = build_app(repo, ServerOpts(
        pricing_table_version="v1",
        ingest_status=lambda: IngestStatus(foreground_complete=True, pending=0, processed=0, total=0),
        dashboard_dir=tmp_path / "absent", port=0, adapters=[],
        pricing_table=PricingTable(version="v1", models={}),
        work_data_dir=tmp_path if work else None))
    return TestClient(app, base_url=LOOPBACK)


def test_routes_absent_when_trial_off(tmp_path):
    _world(tmp_path).close()
    c = _client(tmp_path, work=False)
    assert c.get("/api/work/status").status_code == 404
    assert c.get("/api/work/projects").status_code == 404


def test_routes_when_trial_on(tmp_path):
    _world(tmp_path).close()
    c = _client(tmp_path, work=True)
    s = c.get("/api/work/status").json()
    assert s["enabled"] is True and s["repos"] == 1
    assert c.get("/api/work/projects").json()["projects"][0]["display_name"] == "proj"
    o = c.get("/api/work/projects/1/overview?from=2026-09-01T00:00:00Z&to=2026-09-25T00:00:00Z").json()
    assert o["tiles"]["commits"] == 1
    t = c.get("/api/work/projects/1/timeline?from=2026-09-01T00:00:00Z&to=2026-09-25T00:00:00Z&scope=all").json()
    assert t["days"]
    assert c.get("/api/work/projects/999/overview").status_code == 404
    assert c.get("/api/work/projects/1/overview?tz=9999").status_code == 400
    assert c.get("/api/work/projects/1/timeline?kind=bogus").status_code == 400


def test_period_reaches_back_to_the_first_activity(tmp_path):
    """The default window is 30 days; `days=0` must reach the project's first commit or session."""
    conn = _world(tmp_path)
    conn.execute("INSERT INTO commits VALUES (1, 'old0001', 'Me', 'me@x', '2026-05-02T10:00:00Z', '2026-05-02T10:00:00Z', 'chore: scaffold', 1, 0, 3, 0, 1)")
    conn.commit()
    conn.close()
    c = _client(tmp_path, work=True)
    to = "to=2026-09-25T00:00:00Z"
    shas = lambda t: {i.get("sha") for d in t["days"] for i in d["items"]}  # noqa: E731
    assert "old0001" not in shas(c.get(f"/api/work/projects/1/timeline?{to}").json())
    assert "old0001" in shas(c.get(f"/api/work/projects/1/timeline?{to}&days=0").json())
    assert "old0001" in shas(c.get(f"/api/work/projects/1/timeline?to=2026-09-25T00:00:00Z&days=200").json())
    o = c.get(f"/api/work/projects/1/overview?{to}&days=0").json()
    assert o["daily"]["days"][0] == "2026-05-02" and o["tiles"]["commits"] == 2
    assert c.get(f"/api/work/projects/1/overview?{to}&days=-1").status_code == 400


def test_projects_take_a_period(tmp_path):
    _world(tmp_path).close()
    c = _client(tmp_path, work=True)
    assert c.get("/api/work/projects?days=0").status_code == 200
    assert c.get("/api/work/projects?days=-1").status_code == 400
