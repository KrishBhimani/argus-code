"""/api/work/* (read-only). Mounted only when the trial is on."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from . import queries
from .db import get_meta, open_work_db

TZ = Query(0, ge=-14 * 60, le=14 * 60)
DAYS = Query(30, ge=0, le=3660)  # 0 = all time


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def build_work_router(data_dir: Path) -> APIRouter:
    r = APIRouter(prefix="/api/work")
    conn = open_work_db(data_dir)

    def _range(repo_id: int, frm: str | None, to: str | None, days: int) -> tuple[str, str]:
        """Explicit from/to win; otherwise the last `days` days, or everything since the first activity when 0."""
        end = to or _iso(_now())
        if frm:
            return frm, end
        first = queries.first_activity(conn, repo_id) if days == 0 else None
        return first or _iso(datetime.fromisoformat(end.replace("Z", "+00:00")) - timedelta(days=days or 30)), end

    def _repo(repo_id: int) -> None:
        if conn.execute("SELECT 1 FROM repos WHERE id = ?", (repo_id,)).fetchone() is None:
            raise HTTPException(404, "unknown project")

    @r.get("/status")
    def status() -> dict:
        errors = {row["display_name"]: row["last_error"]
                  for row in conn.execute("SELECT display_name, last_error FROM repos WHERE last_error IS NOT NULL")}
        if get_meta(conn, "facts_error"):
            errors["transcripts"] = get_meta(conn, "facts_error")
        return {"enabled": True, "last_scan_at": get_meta(conn, "last_scan_at"),
                "repos": conn.execute("SELECT COUNT(*) FROM repos").fetchone()[0], "errors": errors}

    @r.get("/projects")
    def projects(tz: int = TZ) -> dict:
        return {"projects": queries.projects(conn, _iso(_now()), tz)}

    @r.get("/projects/{repo_id}/overview")
    def overview(repo_id: int, from_: str | None = Query(None, alias="from"), to: str | None = None,
                 scope: Literal["mine", "all"] = "mine", tz: int = TZ, days: int = DAYS) -> dict:
        _repo(repo_id)
        return queries.overview(conn, repo_id, *_range(repo_id, from_, to, days), scope=scope, tz=tz)

    @r.get("/projects/{repo_id}/timeline")
    def timeline(repo_id: int, from_: str | None = Query(None, alias="from"), to: str | None = None,
                 kind: Literal["all", "sessions", "commits"] = "all", branch: str | None = None,
                 scope: Literal["mine", "all"] = "mine", tz: int = TZ, days: int = DAYS) -> dict:
        _repo(repo_id)
        return queries.timeline(conn, repo_id, *_range(repo_id, from_, to, days), kind=kind, branch=branch, scope=scope, tz=tz)

    return r
