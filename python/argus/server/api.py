"""All ``/api/*`` route handlers.

Direct port of src/server/api.ts. Same response shapes (the dashboard
consumes JSON keys verbatim).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from ..adapters.base import Adapter
from ..collector.first_run import IngestStatus
from ..collector.search_backfill import (
    get_search_backfill_status,
    run_segment_backfill,
)
from ..pricing.types import PricingTable
from ..schema.types import Session
from ..store.repository import Repository

_WINDOWS: dict[str, int | None] = {"today": 1, "7d": 7, "30d": 30, "all": None}


def _is_top_level(sid: str) -> bool:
    return "/" not in sid


def _is_meaningful(s: Session) -> bool:
    return (
        s.turn_count > 0
        or s.total_cost_usd > 0
        or (
            s.total_fresh_input_tokens
            + s.total_output_tokens
            + s.total_cache_read_tokens
            + s.total_cache_write_tokens
            > 0
        )
    )


def _week_of(iso: str) -> str:
    """Match the TS weekOf() byte-for-byte.

    Two subtleties relative to a naive Python port of the original JS:

    1. Day-of-week numbering. Python's ``datetime.weekday()`` numbers days
       as Mon=0..Sun=6, but JS's ``Date.getUTCDay()`` is Sun=0..Sat=6.
       Using ``weekday()`` directly would silently shift the week number
       by one for any year whose Jan 1 isn't aligned in a specific way
       (e.g., 2024-01-07 falls in week 1 instead of week 2). The
       ``(weekday() + 1) % 7`` adjustment converts to JS's numbering.

    2. Input shape. Callers pass either a full ISO timestamp ending in
       ``Z`` (when this helper is exercised in tests) or a date-only
       string like ``"2026-05-22"`` (the production path — that's what
       ``substr(timestamp, 1, 10)`` returns from
       ``aggregate_turns_by_day``). The date-only form parses as a
       naive ``datetime``; we promote it to UTC before subtracting from
       the aware ``start`` so the math doesn't raise ``TypeError:
       can't subtract offset-naive and offset-aware datetimes``.
    """
    d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    start = datetime(d.year, 1, 1, tzinfo=timezone.utc)
    diff = (d - start).total_seconds() / 86_400
    js_day_of_week = (start.weekday() + 1) % 7
    return f"{int((diff + js_day_of_week) // 7) + 1:02d}"


def _iso_z(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _cutoff_iso_for_window(window: str) -> str:
    days = _WINDOWS.get(window)
    if days is None:
        return ""
    return _iso_z(datetime.now(timezone.utc) - timedelta(days=days))


def _window_bounds(window: str) -> tuple[str, str | None]:
    """(cutoff, prior_start) for a rolling window, from one ``now``.

    The prior window is the equal-length range right before the cutoff —
    ``[now - 2n, now - n)`` — so it never overlaps the current one (summing
    whole calendar days did, double-counting the current window's first day).
    ``prior_start`` is None for "all".
    """
    days = _WINDOWS.get(window)
    if days is None:
        return "", None
    now = datetime.now(timezone.utc)
    return _iso_z(now - timedelta(days=days)), _iso_z(now - timedelta(days=2 * days))


#: Viewer's UTC offset in minutes east (JS: -Date#getTimezoneOffset()).
#: Real offsets span UTC-12..UTC+14.
TZ_QUERY = Query(0, ge=-14 * 60, le=14 * 60)


def _mcp_server_name(tool_name: str) -> str | None:
    if not tool_name.startswith("mcp__"):
        return None
    rest = tool_name[len("mcp__") :]
    sep = rest.find("__")
    if sep < 0:
        return None
    return rest[:sep]


# Caller-supplied bounds. Generous on purpose: the shipped dashboard requests
# /api/sessions?limit=100000 (models.astro:52). The point is to stop negative
# values (SQLite reads LIMIT -1 as *unlimited*) and absurd ones, not to be tidy.
MAX_ROWS = 1_000_000   # whole-corpus listings the dashboard genuinely pages over
MAX_PAGE = 1_000       # search / prompts / alerts / transcript result pages


def allowed_origins(port: int) -> frozenset[str]:
    """The exact origins the dashboard itself can be served from."""
    hosts = ("localhost", "127.0.0.1", "[::1]")
    out = {f"http://{h}:{port}" for h in hosts}
    if port == 80:  # browsers omit the default port from Origin
        out |= {f"http://{h}" for h in hosts}
    return frozenset(out)


def is_allowed_origin(origin: str | None, port: int) -> bool:
    """True only for argus's *own* origin.

    Exact match, not a prefix test. Trusting all of loopback would mean any
    other local web app -- a dev server on :3000, a notebook on :8888, anything
    with a reflected XSS -- could drive state-changing requests here; loopback
    is a neighbourhood of mutually untrusting web origins, not one principal.
    Exact comparison also removes the unanchored-``startswith`` foot-gun that
    let ``http://localhost:4242.evil.com`` through.
    """
    if not origin:
        return False
    return origin.strip().lower() in allowed_origins(port)


@dataclass
class ApiDeps:
    pricing_table_version: str
    ingest_status: Callable[[], IngestStatus]
    adapters: list[Adapter]
    pricing_table: PricingTable
    daemon: bool = False


class _ReadCache:
    """Memoises expensive read-only aggregates (overview, trends, tools) per
    request key until the underlying data changes.

    The fingerprint is a handful of O(1)-ish SQLite lookups (row counts and
    max rowids of the tables those endpoints read) plus the current UTC hour. Ingest appends rows and
    upserts sessions, so any new data moves at least one component; a
    pricing refresh bumps ``computed_at``. Nothing else is read by the cached
    endpoints, so a stable fingerprint means a byte-identical answer.
    """

    def __init__(self, repo: Repository) -> None:
        self._repo = repo
        self._entries: dict[tuple[Any, ...], tuple[tuple[Any, ...], Any]] = {}

    def fingerprint(self) -> tuple[Any, ...]:
        row = self._repo.db.execute(
            """
            SELECT (SELECT COUNT(*) FROM sessions),
                   (SELECT MAX(computed_at) FROM sessions),
                   (SELECT MAX(rowid) FROM turns),
                   (SELECT COUNT(*) FROM turns),
                   (SELECT MAX(rowid) FROM tool_calls),
                   (SELECT COUNT(*) FROM tool_calls)
            """
        ).fetchone()
        # Window cutoffs ("today", "7d", ...) are relative to *now*, so time is an
        # input too; bucketing it to the hour bounds staleness at the window edge
        # without defeating the cache.
        hour = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
        return (*row, hour)

    def get(self, key: tuple[Any, ...], compute: Callable[[], Any]) -> Any:
        fp = self.fingerprint()
        hit = self._entries.get(key)
        if hit is not None and hit[0] == fp:
            return hit[1]
        value = compute()
        self._entries[key] = (fp, value)
        if len(self._entries) > 64:  # bounded: a handful of window/granularity combos
            self._entries.pop(next(iter(self._entries)))
        return value


def build_api(repo: Repository, deps: ApiDeps) -> APIRouter:
    cache = _ReadCache(repo)
    """Build the ``/api/*`` router."""
    api = APIRouter()

    def _require_writable() -> None:
        """Reject state-changing requests when the dashboard is read-only.

        When yielded to a live argusd, the DB connection is opened mode=ro;
        without this guard a write would surface as a 500 ("attempt to write
        a readonly database"). Return a clean 409 instead.
        """
        if deps.daemon:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Argus is read-only because the argusd daemon is running. "
                    "Manage indexing from the CLI (e.g. `argus indexing enable`) "
                    "or stop the daemon with `argus daemon stop`."
                ),
            )

    @api.get("/api/sessions")
    def list_sessions(
        limit: int = Query(100, ge=0, le=MAX_ROWS),
        offset: int = Query(0, ge=0, le=MAX_ROWS),
        agent: str | None = None, includeSub: bool = False,
    ) -> dict[str, Any]:
        sessions = [
            s.model_dump()
            for s in repo.list_sessions(limit=limit, offset=offset, agent=agent)
            if (includeSub or _is_top_level(s.id)) and _is_meaningful(s)
        ]
        return {"sessions": sessions}

    @api.get("/api/sessions/{session_id:path}/timeline")
    def session_timeline(session_id: str) -> dict[str, Any]:
        if repo.get_session(session_id) is None:
            raise HTTPException(status_code=404, detail="not found")
        return {
            "search_enabled": repo.is_search_indexing_enabled(),
            "turns": repo.session_timeline(session_id),
        }

    @api.get("/api/sessions/{session_id:path}/tool-output/{tool_use_id}")
    def session_tool_output(session_id: str, tool_use_id: str) -> dict[str, Any]:
        if repo.get_session(session_id) is None:
            raise HTTPException(status_code=404, detail="not found")
        if not repo.is_search_indexing_enabled():
            return {"search_enabled": False, "found": False, "text": None}
        text = repo.tool_output_for(session_id, tool_use_id)
        return {"search_enabled": True, "found": text is not None, "text": text}

    @api.get("/api/overview")
    def get_overview(window: str = "7d", tz: int = TZ_QUERY) -> dict[str, Any]:
        return cache.get(("overview", window, tz), lambda: _overview(window, tz))

    def _overview(window: str, tz: int = 0) -> dict[str, Any]:
        cutoff, prior_start = _window_bounds(window)
        rows = repo.aggregate_turns_by_day(cutoff, tz)
        prior_window = (
            repo.turn_totals_between(prior_start, cutoff) if prior_start is not None else None
        )
        session_meta: dict[str, Session] = {
            s.id: s for s in repo.list_sessions(limit=100_000)
        }

        total_cost = 0.0
        total_tokens = 0
        by_day: dict[str, float] = {}
        cost_by_model: dict[str, float] = {}
        tokens_by_day: dict[str, int] = {}
        tokens_by_model: dict[str, int] = {}
        split: dict[str, dict[str, Any]] = {}
        sessions_per_agent: dict[str, set[str]] = {}
        active_sessions: set[str] = set()
        session_window: dict[str, dict[str, Any]] = {}

        for r in rows:
            tokens = r["fresh_input"] + r["output"] + r["cache_read"] + r["cache_write"]
            total_cost += r["cost"]
            total_tokens += tokens
            by_day[r["day"]] = by_day.get(r["day"], 0.0) + r["cost"]
            cost_by_model[r["model"]] = cost_by_model.get(r["model"], 0.0) + r["cost"]
            tokens_by_day[r["day"]] = tokens_by_day.get(r["day"], 0) + tokens
            tokens_by_model[r["model"]] = tokens_by_model.get(r["model"], 0) + tokens
            active_sessions.add(r["session_id"])
            agent = session_meta.get(r["session_id"]).agent if r["session_id"] in session_meta else "unknown"
            split.setdefault(agent, {"cost": 0.0, "sessions": 0, "tokens": 0})
            split[agent]["cost"] += r["cost"]
            split[agent]["tokens"] += tokens
            sessions_per_agent.setdefault(agent, set()).add(r["session_id"])

            sw = session_window.setdefault(
                r["session_id"], {"cost": 0.0, "tokens": 0, "days": set()}
            )
            sw["cost"] += r["cost"]
            sw["tokens"] += tokens
            sw["days"].add(r["day"])

        for agent, ids in sessions_per_agent.items():
            split[agent]["sessions"] = len(ids)

        top_sessions: list[dict[str, Any]] = []
        for sid, w in session_window.items():
            meta = session_meta.get(sid)
            if meta is None:
                continue
            top_sessions.append(
                {
                    "id": sid,
                    "started_at": meta.started_at,
                    "project_path": meta.project_path,
                    "primary_model": meta.primary_model,
                    "window_cost_usd": w["cost"],
                    "window_tokens": w["tokens"],
                    "days_active": len(w["days"]),
                }
            )
        top_sessions.sort(key=lambda x: x["window_tokens"], reverse=True)
        top_sessions = top_sessions[:20]

        return {
            "window": window,
            "total_cost_usd": total_cost,
            "total_tokens": total_tokens,
            "session_count": len(active_sessions),
            "agent_split": split,
            "cost_by_day": by_day,
            "cost_by_model": cost_by_model,
            "tokens_by_day": tokens_by_day,
            "tokens_by_model": tokens_by_model,
            "top_sessions": top_sessions,
            # Same metrics over the equal-length range just before the window
            # (null for "all") — the dashboard's "vs prior window" deltas.
            "prior_window": prior_window,
        }

    @api.get("/api/trends")
    def get_trends(
        granularity: str = "day", groupBy: str = "agent", tz: int = TZ_QUERY  # noqa: N803
    ) -> dict[str, Any]:
        return cache.get(
            ("trends", granularity, groupBy, tz), lambda: _trends(granularity, groupBy, tz)
        )

    def _trends(granularity: str, groupBy: str, tz: int = 0) -> dict[str, Any]:  # noqa: N803
        rows = repo.aggregate_turns_by_day("", tz)
        session_agent: dict[str, str] = {
            s.id: s.agent for s in repo.list_sessions(limit=100_000)
        }

        def rebucket(day: str) -> str:
            if granularity == "day":
                return day
            if granularity == "week":
                return f"{day[:4]}-W{_week_of(day)}"
            return day[:7]

        points: dict[str, dict[str, dict[str, Any]]] = {}
        for r in rows:
            b = rebucket(r["day"])
            k = (
                session_agent.get(r["session_id"], "unknown")
                if groupBy == "agent"
                else r["model"]
            )
            bucket = points.setdefault(b, {})
            group = bucket.setdefault(
                k, {"cost": 0.0, "tokens": 0, "sessions": 0, "_session_set": set()}
            )
            group["cost"] += r["cost"]
            group["tokens"] += r["fresh_input"] + r["output"]
            group["_session_set"].add(r["session_id"])

        for groups in points.values():
            for k in list(groups.keys()):
                groups[k]["sessions"] = len(groups[k]["_session_set"])
                del groups[k]["_session_set"]

        return {
            "granularity": granularity,
            "groupBy": groupBy,
            "points": [
                {"bucket": b, "groups": g} for b, g in sorted(points.items())
            ],
        }

    @api.get("/api/tools/overview")
    def tools_overview(window: str = "7d") -> dict[str, Any]:
        return cache.get(("tools", window), lambda: _tools_overview(window))

    def _tools_overview(window: str) -> dict[str, Any]:
        cutoff = _cutoff_iso_for_window(window)
        totals = repo.tool_calls_total(cutoff)
        leaderboard_raw = repo.tool_leaderboard(cutoff, 20)
        leaderboard = [
            {
                "name": r["name"],
                "calls": r["calls"],
                "errors": r["errors"] or 0,
                "error_rate": (
                    round(((r["errors"] or 0) / r["calls"]) * 100) / 100
                    if r["calls"] > 0
                    else 0
                ),
            }
            for r in leaderboard_raw
        ]

        mcp_rows = repo.mcp_tool_calls(cutoff)
        mcp_agg: dict[str, dict[str, Any]] = {}
        for row in mcp_rows:
            server = _mcp_server_name(row["tool_name"])
            if server is None:
                continue
            cur = mcp_agg.setdefault(
                server, {"calls": 0, "errors": 0, "tools": set()}
            )
            cur["calls"] += row["calls"]
            cur["errors"] += row["errors"] or 0
            cur["tools"].add(row["tool_name"])
        mcp_servers = [
            {
                "server": server,
                "calls": v["calls"],
                "errors": v["errors"],
                "tools_used": len(v["tools"]),
            }
            for server, v in mcp_agg.items()
        ]
        mcp_servers.sort(key=lambda x: x["calls"], reverse=True)

        subagents = [
            {"type": r["type"], "calls": r["calls"], "errors": r["errors"] or 0}
            for r in repo.subagent_calls(cutoff)
        ]

        return {
            "window": window,
            "total_calls": totals["total"],
            "total_errors": totals["errors"],
            "tool_leaderboard": leaderboard,
            "mcp_servers": mcp_servers,
            "subagents": subagents,
        }

    @api.get("/api/alerts")
    def list_alerts(limit: int = Query(50, ge=0, le=MAX_PAGE)) -> dict[str, Any]:
        alerts = repo.list_alerts(limit=min(limit, 200))
        return {"alerts": [a.model_dump() for a in alerts]}

    @api.get("/api/alerts/unseen")
    def list_unseen_alerts(severity: str | None = None) -> dict[str, Any]:
        if severity is not None and severity not in ("info", "warning", "critical"):
            raise HTTPException(status_code=400, detail="invalid severity")
        alerts = repo.list_unseen_alerts(severity=severity)
        return {"alerts": [a.model_dump() for a in alerts]}

    @api.post("/api/alerts/{alert_id}/seen")
    def mark_alert_seen(alert_id: int) -> dict[str, Any]:
        _require_writable()
        if not repo.mark_alert_seen(alert_id):
            raise HTTPException(status_code=404, detail="not found")
        return {"ok": True}

    @api.get("/api/prompts")
    def prompts(
        q: str = "",
        limit: int = Query(50, ge=0, le=MAX_PAGE),
        project: str | None = None,
        include_slash: str = "0",
    ) -> dict[str, Any]:
        limit = min(limit, 200)
        if not repo.is_search_indexing_enabled():
            return {"total": 0, "prompts": []}

        try:
            result = repo.search_prompts(
                q=q.strip(),
                limit=limit,
                project=project,
                include_slash=(include_slash == "1"),
            )
        except Exception:
            escaped = q.replace('"', '""')
            try:
                result = repo.search_prompts(
                    q=f'"{escaped}"',
                    limit=limit,
                    project=project,
                    include_slash=(include_slash == "1"),
                )
            except Exception:
                result = {"total": 0, "rows": []}

        prompts_out = []
        for r in result["rows"]:
            prompts_out.append(
                {
                    "id": r["id"],
                    "timestamp_ms": r["timestamp_ms"],
                    "project_path": r["project_path"],
                    "display": r["display"],
                    "snippet": r.get("snippet") or r["display"],
                    "pasted_chars": r["pasted_chars"],
                    "session_id": repo.link_prompt_to_session(
                        r["project_path"], r["timestamp_ms"]
                    ),
                }
            )
        return {"total": result["total"], "prompts": prompts_out}

    @api.get("/api/prompts/stats")
    def prompts_stats() -> dict[str, Any]:
        if not repo.is_search_indexing_enabled():
            return {"total": 0, "projects": 0, "oldest_ms": None}
        return repo.prompt_stats()

    @api.get("/api/prompts/projects")
    def prompts_projects() -> dict[str, Any]:
        if not repo.is_search_indexing_enabled():
            return {"projects": []}
        return {"projects": repo.prompt_projects()}

    @api.get("/api/search")
    def search(
        q: str = "",
        limit: int = Query(50, ge=0, le=MAX_PAGE),
        project: str | None = None,
        include_slash: str = "0",
        roles: str | None = None,
    ) -> dict[str, Any]:
        limit = min(limit, 200)
        wanted_roles = [r for r in (roles.split(",") if roles else []) if r] or None
        include_prompts = wanted_roles is None or "prompt" in wanted_roles
        transcript_roles = (
            [r for r in wanted_roles if r != "prompt"]
            if wanted_roles
            else ["user", "assistant", "thinking", "tool_result"]
        )
        search_enabled = repo.is_search_indexing_enabled()

        if not search_enabled:
            return {
                "total": 0,
                "prompt_total": 0,
                "transcript_total": 0,
                "search_indexing_enabled": False,
                "results": [],
            }

        results: list[dict[str, Any]] = []
        prompt_total = 0
        transcript_total = 0

        if include_prompts:
            try:
                pr = repo.search_prompts(
                    q=q.strip(),
                    limit=limit,
                    project=project,
                    include_slash=(include_slash == "1"),
                )
            except Exception:
                escaped = q.replace('"', '""')
                try:
                    pr = repo.search_prompts(
                        q=f'"{escaped}"',
                        limit=limit,
                        project=project,
                        include_slash=(include_slash == "1"),
                    )
                except Exception:
                    pr = {"total": 0, "rows": []}
            prompt_total = pr["total"]
            for r in pr["rows"]:
                results.append(
                    {
                        "kind": "prompt",
                        "role": "prompt",
                        "timestamp_ms": r["timestamp_ms"],
                        "project_path": r["project_path"],
                        "text": r["display"],
                        "snippet": r.get("snippet") or r["display"],
                        "pasted_chars": r["pasted_chars"],
                        "session_id": repo.link_prompt_to_session(
                            r["project_path"], r["timestamp_ms"]
                        ),
                    }
                )

        if transcript_roles:
            try:
                tr = repo.search_transcripts(
                    q=q.strip(), limit=limit, project=project, roles=transcript_roles
                )
            except Exception:
                escaped = q.replace('"', '""')
                try:
                    tr = repo.search_transcripts(
                        q=f'"{escaped}"',
                        limit=limit,
                        project=project,
                        roles=transcript_roles,
                    )
                except Exception:
                    tr = {"total": 0, "rows": []}
            transcript_total = tr["total"]
            for r in tr["rows"]:
                try:
                    ts = int(
                        datetime.fromisoformat(
                            r["timestamp"].replace("Z", "+00:00")
                        ).timestamp()
                        * 1000
                    )
                except (ValueError, TypeError):
                    ts = 0
                results.append(
                    {
                        "kind": "transcript",
                        "role": r["role"],
                        "timestamp_ms": ts,
                        "project_path": r["project_path"],
                        "text": r["text"],
                        "snippet": r["snippet"],
                        "pasted_chars": 0,
                        "session_id": r["session_id"],
                    }
                )

        if not q:
            results.sort(key=lambda x: x["timestamp_ms"], reverse=True)

        return {
            "total": prompt_total + transcript_total,
            "prompt_total": prompt_total,
            "transcript_total": transcript_total,
            "search_indexing_enabled": search_enabled,
            "results": results[:limit],
        }

    @api.get("/api/sessions/{session_id:path}/transcript")
    def session_transcript(
        session_id: str, q: str = "", limit: int = Query(100, ge=0, le=MAX_PAGE)
    ) -> dict[str, Any]:
        limit = min(limit, 500)
        q = q.strip()
        if not q:
            return {
                "total": 0,
                "segments": [],
                "search_indexing_enabled": repo.is_search_indexing_enabled(),
            }
        if not repo.is_search_indexing_enabled():
            return {"total": 0, "segments": [], "search_indexing_enabled": False}
        try:
            r = repo.search_transcripts(q=q, limit=limit, session_id=session_id)
        except Exception:
            escaped = q.replace('"', '""')
            try:
                r = repo.search_transcripts(
                    q=f'"{escaped}"', limit=limit, session_id=session_id
                )
            except Exception:
                r = {"total": 0, "rows": []}
        segments = [
            {
                "uid": row["uid"],
                "timestamp": row["timestamp"],
                "role": row["role"],
                "text": row["text"],
                "snippet": row["snippet"],
            }
            for row in r["rows"]
        ]
        return {"total": r["total"], "segments": segments}

    @api.get("/api/sessions/{session_id:path}/subagents")
    def session_subagents(session_id: str) -> dict[str, Any]:
        if repo.get_session(session_id) is None:
            raise HTTPException(status_code=404, detail="not found")
        return {"subagents": repo.subagent_summaries(session_id)}

    # Registered LAST among /api/sessions/* routes: the greedy {session_id:path}
    # converter would otherwise shadow the suffixed routes (/timeline, etc.).
    @api.get("/api/sessions/{session_id:path}")
    def get_session(session_id: str) -> dict[str, Any]:
        session = repo.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="not found")
        turns = [t.model_dump() for t in repo.get_turns_for_session(session_id)]
        return {"session": session.model_dump(), "turns": turns}

    @api.get("/api/search-index/status")
    def search_index_status() -> dict[str, Any]:
        enabled = repo.is_search_indexing_enabled()
        segs = repo.segment_stats()
        bf = get_search_backfill_status()
        return {
            "enabled": enabled,
            "segment_count": segs["total"],
            "indexed_sessions": segs["sessions"],
            "backfill": {
                "in_progress": bf.in_progress,
                "processed": bf.processed,
                "total": bf.total,
                "started_at_ms": bf.started_at_ms,
                "finished_at_ms": bf.finished_at_ms,
            },
        }

    @api.post("/api/search-index/enable")
    def search_index_enable() -> dict[str, Any]:
        _require_writable()
        repo.set_search_indexing_enabled(True)
        try:
            run_segment_backfill(deps.adapters, repo, deps.pricing_table)
        except Exception:  # noqa: BLE001
            pass
        bf = get_search_backfill_status()
        return {
            "enabled": True,
            "backfill": {
                "in_progress": bf.in_progress,
                "processed": bf.processed,
                "total": bf.total,
                "started_at_ms": bf.started_at_ms,
                "finished_at_ms": bf.finished_at_ms,
            },
        }

    @api.post("/api/search-index/disable")
    def search_index_disable() -> dict[str, Any]:
        _require_writable()
        repo.set_search_indexing_enabled(False)
        return {"enabled": False}

    @api.post("/api/search-index/clear")
    def search_index_clear() -> dict[str, Any]:
        _require_writable()
        before_bytes = repo.db_size_bytes()
        repo.clear_all_segments()
        repo.set_search_indexing_enabled(False)
        repo.vacuum()
        after_bytes = repo.db_size_bytes()
        return {
            "enabled": False,
            "cleared": True,
            "freed_bytes": max(0, before_bytes - after_bytes),
            "db_size_bytes": after_bytes,
        }

    @api.get("/api/ingest/status")
    def ingest_status_route() -> dict[str, Any]:
        session_count = sum(
            1
            for s in repo.list_sessions(limit=100_000)
            if _is_top_level(s.id) and _is_meaningful(s)
        )
        status = deps.ingest_status()
        return {
            "foregroundComplete": status.foreground_complete,
            "pending": status.pending,
            "processed": status.processed,
            "total": status.total,
            "sessionCount": session_count,
            "daemon": deps.daemon,
        }

    @api.get("/api/pricing")
    def pricing() -> dict[str, Any]:
        return {"version": deps.pricing_table_version}

    @api.get("/api/export.json")
    def export_json() -> dict[str, Any]:
        sessions = [
            s.model_dump()
            for s in repo.list_sessions(limit=1_000_000)
            if _is_top_level(s.id) and _is_meaningful(s)
        ]
        return {"sessions": sessions}

    @api.get("/api/export.csv")
    def export_csv() -> Response:
        rows = [
            s
            for s in repo.list_sessions(limit=1_000_000)
            if _is_top_level(s.id) and _is_meaningful(s)
        ]
        header = (
            "id,agent,started_at,ended_at,project_path,primary_model,"
            "total_cost_usd,total_fresh_input_tokens,total_output_tokens,turn_count\n"
        )

        def esc(v: Any) -> str:
            return '"' + str(v if v is not None else "").replace('"', '""') + '"'

        body = "\n".join(
            ",".join(
                [
                    esc(s.id),
                    esc(s.agent),
                    esc(s.started_at),
                    esc(s.ended_at or ""),
                    esc(s.project_path),
                    esc(s.primary_model),
                    esc(s.total_cost_usd),
                    esc(s.total_fresh_input_tokens),
                    esc(s.total_output_tokens),
                    esc(s.turn_count),
                ]
            )
            for s in rows
        )
        return PlainTextResponse(header + body, media_type="text/csv")

    @api.get("/api/parse-errors")
    def parse_errors() -> dict[str, Any]:
        return {"errors": repo.recent_parse_errors(100)}

    return api
