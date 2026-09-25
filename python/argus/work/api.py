"""/api/work/* (read-only). Mounted only when the trial is on."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from . import queries
from .db import get_meta, open_work_db

TZ = Query(0, ge=-14 * 60, le=14 * 60)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def build_work_router(data_dir: Path) -> APIRouter:
    r = APIRouter(prefix="/api/work")
    conn = open_work_db(data_dir)

    def _range(frm: str | None, to: str | None) -> tuple[str, str]:
        end = to or _iso(_now())
        start = frm or _iso(datetime.fromisoformat(end.replace("Z", "+00:00")) - timedelta(days=30))
        return start, end

    def _repo(repo_id: int) -> None:
        if conn.execute("SELECT 1 FROM repos WHERE id = ?", (repo_id,)).fetchone() is None:
            raise HTTPException(404, "unknown project")

    @r.get("/status")
    def status() -> dict:
        errors = {row["display_name"]: row["last_error"]
                  for row in conn.execute("SELECT display_name, last_error FROM repos WHERE last_error IS NOT NULL")}
        return {"enabled": True, "last_scan_at": get_meta(conn, "last_scan_at"),
                "repos": conn.execute("SELECT COUNT(*) FROM repos").fetchone()[0], "errors": errors}

    @r.get("/projects")
    def projects(tz: int = TZ) -> dict:
        return {"projects": queries.projects(conn, _iso(_now()), tz)}

    @r.get("/projects/{repo_id}/overview")
    def overview(repo_id: int, from_: str | None = Query(None, alias="from"), to: str | None = None,
                 scope: Literal["mine", "all"] = "mine", tz: int = TZ) -> dict:
        _repo(repo_id)
        return queries.overview(conn, repo_id, *_range(from_, to), scope=scope, tz=tz)

    @r.get("/projects/{repo_id}/timeline")
    def timeline(repo_id: int, from_: str | None = Query(None, alias="from"), to: str | None = None,
                 kind: Literal["all", "sessions", "commits"] = "all", branch: str | None = None,
                 scope: Literal["mine", "all"] = "mine", tz: int = TZ) -> dict:
        _repo(repo_id)
        return queries.timeline(conn, repo_id, *_range(from_, to), kind=kind, branch=branch, scope=scope, tz=tz)

    return r
