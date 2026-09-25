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
