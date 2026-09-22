"""Repository — all SQL lives here.

Direct port of src/store/repository.ts. Methods keep the same names so
test files port over verbatim. Named placeholders use SQLite's ``:name``
syntax instead of better-sqlite3's ``@name``.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..schema.types import (
    Alert,
    Prompt,
    Session,
    ToolCall,
    TranscriptSegment,
    Turn,
)


def normalize_project_path(p: str) -> str:
    """Same project_path normalization used by session ingest and history ingest.

    Replaces backslashes with forward slashes; on Windows additionally
    lowercases everything. Trailing slash stripped. Empty input preserved.
    The history.jsonl and session-ingest sides MUST agree byte-for-byte
    so the prompt → session linkage join hits.
    """
    if not p:
        return ""
    s = p.replace("\\", "/").rstrip("/")
    if sys.platform == "win32":
        s = s.lower()
    return s


def _iso_to_ms(iso: str | None) -> int | None:
    """Convert an ISO-8601 timestamp string to ms since epoch, or None."""
    if not iso:
        return None
    try:
        # fromisoformat handles trailing 'Z' from Python 3.11+
        s = iso.replace("Z", "+00:00") if iso.endswith("Z") else iso
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def _row_to_session(row: sqlite3.Row) -> Session:
    """SQLite row → Session, dropping the denormalized *_at_ms columns."""
    d = dict(row)
    d.pop("started_at_ms", None)
    d.pop("ended_at_ms", None)
    d["metadata"] = json.loads(d["metadata"])
    return Session.model_validate(d)


def _row_to_turn(row: sqlite3.Row) -> Turn:
    d = dict(row)
    d["metadata"] = json.loads(d["metadata"])
    return Turn.model_validate(d)


def _row_to_alert(row: sqlite3.Row) -> Alert:
    d = dict(row)
    d["metadata"] = json.loads(d["metadata"])
    return Alert.model_validate(d)


class Repository:
    """All SQL is encapsulated here. Methods are mostly direct ports."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db

    # ─── Sessions ──────────────────────────────────────────────────────

    def upsert_session(self, s: Session) -> None:
        started_ms = _iso_to_ms(s.started_at)
        ended_ms = _iso_to_ms(s.ended_at)
        params = {
            "id": s.id,
            "agent": s.agent,
            "agent_version": s.agent_version,
            "project_path": normalize_project_path(s.project_path),
            "started_at": s.started_at,
            "ended_at": s.ended_at,
            "duration_sec": s.duration_sec,
            "total_fresh_input_tokens": s.total_fresh_input_tokens,
            "total_output_tokens": s.total_output_tokens,
            "total_cache_read_tokens": s.total_cache_read_tokens,
            "total_cache_write_tokens": s.total_cache_write_tokens,
            "total_cost_usd": s.total_cost_usd,
            "primary_model": s.primary_model,
            "turn_count": s.turn_count,
            "pricing_table_version": s.pricing_table_version,
            "computed_at": s.computed_at,
            "agent_reported_cost_usd": s.agent_reported_cost_usd,
            "metadata": json.dumps(s.metadata),
            "started_at_ms": started_ms,
            "ended_at_ms": ended_ms,
        }
        self.db.execute(
            """
            INSERT INTO sessions (id, agent, agent_version, project_path, started_at, ended_at, duration_sec,
              total_fresh_input_tokens, total_output_tokens, total_cache_read_tokens, total_cache_write_tokens,
              total_cost_usd, primary_model, turn_count, pricing_table_version, computed_at,
              agent_reported_cost_usd, metadata, started_at_ms, ended_at_ms)
            VALUES (:id, :agent, :agent_version, :project_path, :started_at, :ended_at, :duration_sec,
              :total_fresh_input_tokens, :total_output_tokens, :total_cache_read_tokens, :total_cache_write_tokens,
              :total_cost_usd, :primary_model, :turn_count, :pricing_table_version, :computed_at,
              :agent_reported_cost_usd, :metadata, :started_at_ms, :ended_at_ms)
            ON CONFLICT(id) DO UPDATE SET
              agent_version=excluded.agent_version, project_path=excluded.project_path,
              ended_at=excluded.ended_at, duration_sec=excluded.duration_sec,
              total_fresh_input_tokens=excluded.total_fresh_input_tokens,
              total_output_tokens=excluded.total_output_tokens,
              total_cache_read_tokens=excluded.total_cache_read_tokens,
              total_cache_write_tokens=excluded.total_cache_write_tokens,
              total_cost_usd=excluded.total_cost_usd, primary_model=excluded.primary_model,
              turn_count=excluded.turn_count, pricing_table_version=excluded.pricing_table_version,
              computed_at=excluded.computed_at, agent_reported_cost_usd=excluded.agent_reported_cost_usd,
              metadata=excluded.metadata,
              started_at_ms=excluded.started_at_ms, ended_at_ms=excluded.ended_at_ms
            """,
            params,
        )

    def get_session(self, session_id: str) -> Session | None:
        row = self.db.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return _row_to_session(row) if row else None

    def list_sessions(
        self, *, limit: int, offset: int = 0, agent: str | None = None
    ) -> list[Session]:
        if agent:
            rows = self.db.execute(
                "SELECT * FROM sessions WHERE agent = ? ORDER BY started_at DESC LIMIT ? OFFSET ?",
                (agent, limit, offset),
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [_row_to_session(r) for r in rows]

    # ─── Turns ─────────────────────────────────────────────────────────

    def upsert_turn(self, t: Turn) -> None:
        params = {
            "id": t.id,
            "session_id": t.session_id,
            "sequence": t.sequence,
            "timestamp": t.timestamp,
            "model": t.model,
            "model_raw": t.model_raw,
            "fresh_input_tokens": t.fresh_input_tokens,
            "output_tokens": t.output_tokens,
            "cache_read_tokens": t.cache_read_tokens,
            "cache_write_tokens": t.cache_write_tokens,
            "cache_write_5m_tokens": t.cache_write_5m_tokens,
            "cache_write_1h_tokens": t.cache_write_1h_tokens,
            "tool_calls_count": t.tool_calls_count,
            "cost_usd": t.cost_usd,
            "metadata": json.dumps(t.metadata),
        }
        self.db.execute(
            """
            INSERT INTO turns (id, session_id, sequence, timestamp, model, model_raw,
              fresh_input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
              cache_write_5m_tokens, cache_write_1h_tokens, tool_calls_count, cost_usd, metadata)
            VALUES (:id, :session_id, :sequence, :timestamp, :model, :model_raw,
              :fresh_input_tokens, :output_tokens, :cache_read_tokens, :cache_write_tokens,
              :cache_write_5m_tokens, :cache_write_1h_tokens, :tool_calls_count, :cost_usd, :metadata)
            ON CONFLICT(id) DO UPDATE SET
              sequence=excluded.sequence, timestamp=excluded.timestamp, model=excluded.model,
              model_raw=excluded.model_raw, fresh_input_tokens=excluded.fresh_input_tokens,
              output_tokens=excluded.output_tokens, cache_read_tokens=excluded.cache_read_tokens,
              cache_write_tokens=excluded.cache_write_tokens,
              cache_write_5m_tokens=excluded.cache_write_5m_tokens,
              cache_write_1h_tokens=excluded.cache_write_1h_tokens,
              tool_calls_count=excluded.tool_calls_count, cost_usd=excluded.cost_usd,
              metadata=excluded.metadata
            """,
            params,
        )

    def get_turn(self, turn_id: str) -> Turn | None:
        row = self.db.execute("SELECT * FROM turns WHERE id = ?", (turn_id,)).fetchone()
        return _row_to_turn(row) if row else None

    def get_turns_for_session(self, session_id: str) -> list[Turn]:
        rows = self.db.execute(
            "SELECT * FROM turns WHERE session_id = ? ORDER BY sequence",
            (session_id,),
        ).fetchall()
        return [_row_to_turn(r) for r in rows]

    def session_timeline(self, session_id: str) -> list[dict[str, Any]]:
        """Turns with their tool calls nested; error text attached to failed
        calls when search indexing is on and a linked tool_result segment
        exists. Powers GET /api/sessions/{id}/timeline."""
        turns = self.get_turns_for_session(session_id)
        call_rows = self.db.execute(
            """
            SELECT id, turn_index, tool_name, is_error, input_size, subagent_type, timestamp
            FROM tool_calls WHERE session_id = ?
            ORDER BY turn_index, timestamp, id
            """,
            (session_id,),
        ).fetchall()

        # tool_calls.id is f"{session_id}:{tool_use_id}" (pipeline._to_tool_call).
        prefix = f"{session_id}:"

        error_text: dict[str, str] = {}
        if self.is_search_indexing_enabled():
            failed_ids = [
                r["id"][len(prefix):]
                for r in call_rows
                if r["is_error"] and r["id"].startswith(prefix)
            ]
            if failed_ids:
                ph = ",".join("?" for _ in failed_ids)
                seg_rows = self.db.execute(
                    f"""
                    SELECT tool_use_id, text FROM transcript_segments
                    WHERE session_id = ? AND role = 'tool_result'
                      AND tool_use_id IN ({ph})
                    """,
                    [session_id, *failed_ids],
                ).fetchall()
                error_text = {r["tool_use_id"]: r["text"] for r in seg_rows}

        # Rows written before sequences became file byte offsets restarted
        # `sequence` / `turn_index` at 0 on every ingest tick, so turn_index
        # alone is ambiguous on them: a session ingested in 15 ticks has 15
        # turns numbered 0. Attaching by turn_index multiplied every tool call
        # (78k "calls" for a session with 351). Attach each
        # call to exactly one turn: among the turns sharing its turn_index, the
        # latest whose timestamp is not after the call's (calls carry their
        # assistant message's time); if none precede it, the earliest.
        turns_by_seq: dict[int, list[int]] = {}
        for i, t in enumerate(turns):
            turns_by_seq.setdefault(t.sequence, []).append(i)
        for idxs in turns_by_seq.values():
            idxs.sort(key=lambda i: turns[i].timestamp)

        calls_by_turn: dict[int, list[dict[str, Any]]] = {}
        for r in call_rows:
            cands = turns_by_seq.get(r["turn_index"])
            if not cands:
                continue
            ts = r["timestamp"] or ""
            pick = cands[0]
            for i in cands:
                if turns[i].timestamp <= ts:
                    pick = i
                else:
                    break
            tu_id = r["id"][len(prefix):] if r["id"].startswith(prefix) else r["id"]
            calls_by_turn.setdefault(pick, []).append(
                {
                    "tool_name": r["tool_name"],
                    "tool_use_id": tu_id,
                    "is_error": r["is_error"],
                    "input_size": r["input_size"],
                    "subagent_type": r["subagent_type"],
                    "error_text": error_text.get(tu_id) if r["is_error"] else None,
                }
            )

        return [
            {
                "sequence": t.sequence,
                "timestamp": t.timestamp,
                "model": t.model,
                "fresh_input_tokens": t.fresh_input_tokens,
                "cache_read_tokens": t.cache_read_tokens,
                "cache_write_tokens": t.cache_write_tokens,
                "output_tokens": t.output_tokens,
                "cost_usd": t.cost_usd,
                "tool_calls": calls_by_turn.get(i, []),
            }
            for i, t in enumerate(turns)
        ]

    # ─── File offsets / parse errors ───────────────────────────────────

    def set_file_offset(self, path: str, offset: int) -> None:
        self.db.execute(
            """
            INSERT INTO file_offsets (path, byte_offset, last_seen) VALUES (?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET byte_offset=excluded.byte_offset, last_seen=excluded.last_seen
            """,
            (path, offset, datetime.now(timezone.utc).isoformat()),
        )

    def get_file_offset(self, path: str) -> int:
        row = self.db.execute(
            "SELECT byte_offset FROM file_offsets WHERE path = ?", (path,)
        ).fetchone()
        return row["byte_offset"] if row else 0

    def record_parse_error(self, e: dict[str, Any]) -> None:
        self.db.execute(
            """
            INSERT INTO parse_errors (file, byte_offset, reason, raw_line_truncated, occurred_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                e["file"],
                e["byte_offset"],
                e["reason"],
                e["raw_line_truncated"],
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    def recent_parse_errors(self, limit: int) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT file, byte_offset, reason, raw_line_truncated FROM parse_errors ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ─── Tool calls ────────────────────────────────────────────────────

    def upsert_tool_calls(self, calls: list[ToolCall]) -> None:
        if not calls:
            return
        rows = [
            {
                "id": c.id,
                "session_id": c.session_id,
                "turn_index": c.turn_index,
                "tool_name": c.tool_name,
                "is_error": c.is_error,
                "input_size": c.input_size,
                "subagent_type": c.subagent_type,
                "timestamp": c.timestamp,
            }
            for c in calls
        ]
        with self.db:
            self.db.executemany(
                """
                INSERT INTO tool_calls (id, session_id, turn_index, tool_name, is_error, input_size, subagent_type, timestamp)
                VALUES (:id, :session_id, :turn_index, :tool_name, :is_error, :input_size, :subagent_type, :timestamp)
                ON CONFLICT(id) DO UPDATE SET
                  turn_index=excluded.turn_index, tool_name=excluded.tool_name,
                  is_error=MAX(tool_calls.is_error, excluded.is_error),
                  input_size=excluded.input_size,
                  subagent_type=excluded.subagent_type, timestamp=excluded.timestamp
                """,
                rows,
            )

    def existing_tool_call_ids(self, ids: list[str]) -> set[str]:
        """Subset of ``ids`` already stored in tool_calls."""
        found: set[str] = set()
        for i in range(0, len(ids), 500):  # stay under SQLite's variable limit
            chunk = ids[i : i + 500]
            ph = ",".join("?" for _ in chunk)
            found.update(
                r["id"]
                for r in self.db.execute(f"SELECT id FROM tool_calls WHERE id IN ({ph})", chunk)
            )
        return found

    def mark_tool_calls_errored(self, session_id: str, tool_use_ids: list[str]) -> None:
        """Flag calls whose tool_result reported an error, by id.

        The result usually lands in a later ingest tick than its tool_use, so
        this runs against whatever call rows exist — stored this tick or any
        earlier one. ``is_error`` only ever goes 0 → 1 (``upsert_tool_calls``
        keeps the MAX), so a re-read that sees a call without its result can't
        clear the flag. Ids with no stored call yet are a no-op.
        """
        if not tool_use_ids:
            return
        self.db.executemany(
            "UPDATE tool_calls SET is_error = 1 WHERE id = ? AND is_error = 0",
            [(f"{session_id}:{t}",) for t in tool_use_ids],
        )

    def repair_session_time_columns(self) -> int:
        """Make ``duration_sec`` / ``started_at_ms`` / ``ended_at_ms`` agree with
        the stored ``started_at`` / ``ended_at`` strings; return rows changed.

        Pre-fix incremental ingest recomputed the start from each tick's first
        line. ``upsert_session`` kept the original ``started_at`` (only written
        on insert, by the tick that first saw turns) but overwrote the derived
        columns, leaving rows like "3 s" for a 29-day session. The ISO strings
        are trustworthy, so this is a pure in-DB recompute — no file re-read,
        no row deleted, and a second run changes nothing.
        """
        rows = self.db.execute(
            "SELECT id, started_at, ended_at, duration_sec, started_at_ms, ended_at_ms"
            " FROM sessions"
        ).fetchall()
        changed = 0
        for r in rows:
            s_ms = _iso_to_ms(r["started_at"])
            e_ms = _iso_to_ms(r["ended_at"])
            dur = r["duration_sec"]
            if s_ms is not None and e_ms is not None:
                dur = max(0, (e_ms - s_ms) // 1000)
            if (s_ms, e_ms, dur) == (r["started_at_ms"], r["ended_at_ms"], r["duration_sec"]):
                continue
            self.db.execute(
                "UPDATE sessions SET duration_sec = ?, started_at_ms = ?, ended_at_ms = ?"
                " WHERE id = ?",
                (dur, s_ms, e_ms, r["id"]),
            )
            changed += 1
        return changed

    def count_tool_calls_for_session(self, session_id: str) -> int:
        row = self.db.execute(
            "SELECT COUNT(*) AS n FROM tool_calls WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return row["n"] if row else 0

    def tool_leaderboard(
        self, cutoff_iso: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        rows = self.db.execute(
            """
            SELECT tool_name AS name, COUNT(*) AS calls, SUM(is_error) AS errors
            FROM tool_calls
            WHERE timestamp >= ?
            GROUP BY tool_name
            ORDER BY calls DESC
            LIMIT ?
            """,
            (cutoff_iso, limit),
        ).fetchall()
        return [
            {"name": r["name"], "calls": r["calls"], "errors": r["errors"] or 0}
            for r in rows
        ]

    def tool_call_stats_in_range(
        self, *, start_iso: str, end_iso: str
    ) -> list[dict[str, Any]]:
        """Per-tool ``(calls, errors)`` over a half-open ``[start, end)`` window."""
        rows = self.db.execute(
            """
            SELECT tool_name, COUNT(*) AS calls,
                   COALESCE(SUM(is_error), 0) AS errors
            FROM tool_calls
            WHERE timestamp >= ? AND timestamp < ?
            GROUP BY tool_name
            """,
            (start_iso, end_iso),
        ).fetchall()
        return [dict(r) for r in rows]

    def tool_calls_total(self, cutoff_iso: str) -> dict[str, int]:
        row = self.db.execute(
            """
            SELECT COUNT(*) AS total, COALESCE(SUM(is_error), 0) AS errors
            FROM tool_calls WHERE timestamp >= ?
            """,
            (cutoff_iso,),
        ).fetchone()
        return {
            "total": row["total"] if row else 0,
            "errors": row["errors"] if row else 0,
        }

    def aggregate_turns_by_day(self, cutoff_iso: str) -> list[dict[str, Any]]:
        """Per-turn aggregation for windowed views.

        Returns one row per (day, model, session_id) inside the cutoff.
        Sub-agent rollup ids look like ``parent/sub``; their turns are
        attributed to the parent id (the part before the first ``/``) so
        windowed totals match the session detail page, whose stored totals
        already include the sub-agent rollup.
        """
        rows = self.db.execute(
            """
            WITH t AS (
              SELECT
                substr(timestamp, 1, 10) AS day,
                model,
                CASE
                  WHEN instr(session_id, '/') > 0
                  THEN substr(session_id, 1, instr(session_id, '/') - 1)
                  ELSE session_id
                END AS session_id,
                cost_usd, fresh_input_tokens, output_tokens,
                cache_read_tokens, cache_write_tokens
              FROM turns
              WHERE timestamp >= ?
            )
            SELECT
              day,
              model,
              session_id,
              COALESCE(SUM(cost_usd), 0) AS cost,
              COALESCE(SUM(fresh_input_tokens), 0) AS fresh_input,
              COALESCE(SUM(output_tokens), 0) AS output,
              COALESCE(SUM(cache_read_tokens), 0) AS cache_read,
              COALESCE(SUM(cache_write_tokens), 0) AS cache_write
            FROM t
            GROUP BY day, model, session_id
            ORDER BY day ASC
            """,
            (cutoff_iso,),
        ).fetchall()
        return [dict(r) for r in rows]

    def mcp_tool_calls(self, cutoff_iso: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            r"""
            SELECT tool_name, COUNT(*) AS calls, SUM(is_error) AS errors
            FROM tool_calls
            WHERE timestamp >= ? AND tool_name LIKE 'mcp\_\_%' ESCAPE '\'
            GROUP BY tool_name
            """,
            (cutoff_iso,),
        ).fetchall()
        return [dict(r) for r in rows]

    def subagent_calls(self, cutoff_iso: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            """
            SELECT subagent_type AS type, COUNT(*) AS calls, COALESCE(SUM(is_error), 0) AS errors
            FROM tool_calls
            WHERE timestamp >= ? AND subagent_type IS NOT NULL AND subagent_type <> ''
            GROUP BY subagent_type
            ORDER BY calls DESC
            """,
            (cutoff_iso,),
        ).fetchall()
        return [dict(r) for r in rows]

    def subagent_summaries(self, parent_id: str) -> list[dict[str, Any]]:
        """Flat per-sub-agent summary for the Sub-agents tab. One dict per
        existing direct child listed in the parent's
        metadata.sub_agent_session_ids. Missing child rows are skipped."""
        parent = self.get_session(parent_id)
        if parent is None:
            return []
        ids = parent.metadata.get("sub_agent_session_ids", []) or []
        out: list[dict[str, Any]] = []
        for sid in ids:
            s = self.get_session(sid)
            if s is None:
                continue
            tools = self._tool_summary(sid)
            errors = sum(t["errors"] for t in tools)
            out.append(
                {
                    "id": s.id,
                    "model": s.primary_model,
                    "status": "error" if errors else "ok",
                    "turns": s.turn_count,
                    "tool_calls": sum(t["count"] for t in tools),
                    "errors": errors,
                    "tokens": {
                        "fresh_input": s.total_fresh_input_tokens,
                        "output": s.total_output_tokens,
                        "cache_read": s.total_cache_read_tokens,
                        "cache_write": s.total_cache_write_tokens,
                    },
                    "total_tokens": (
                        s.total_fresh_input_tokens + s.total_output_tokens
                        + s.total_cache_read_tokens + s.total_cache_write_tokens
                    ),
                    "cost_usd": s.total_cost_usd,
                    "duration_sec": s.duration_sec,
                    "task_given": self._first_user_text(sid),
                    "tools": tools,
                }
            )
        return out

    def _tool_summary(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            """
            SELECT tool_name AS name, COUNT(*) AS count,
                   COALESCE(SUM(is_error), 0) AS errors
            FROM tool_calls WHERE session_id = ?
            GROUP BY tool_name
            ORDER BY count DESC, tool_name
            """,
            (session_id,),
        ).fetchall()
        return [
            {"name": r["name"], "count": r["count"], "errors": r["errors"]}
            for r in rows
        ]

    def _first_user_text(self, session_id: str) -> str | None:
        row = self.db.execute(
            """
            SELECT text FROM transcript_segments
            WHERE session_id = ? AND role = 'user'
            ORDER BY rowid LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        return row["text"] if row else None

    def sessions_missing_tool_calls(self, limit: int) -> list[dict[str, Any]]:
        rows = self.db.execute(
            """
            SELECT s.id FROM sessions s
            LEFT JOIN (SELECT DISTINCT session_id FROM tool_calls) t ON t.session_id = s.id
            WHERE t.session_id IS NULL
            ORDER BY s.started_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [{"id": r["id"]} for r in rows]

    def sessions_with_untyped_agent_calls(self, limit: int) -> list[dict[str, Any]]:
        """Sessions holding `Agent` tool calls ingested before the adapter learned
        the tool's current name, so their subagent_type is NULL. A default-agent
        call legitimately has no subagent_type, so callers run this once and
        record completion in app_meta rather than re-deriving every start."""
        rows = self.db.execute(
            """
            SELECT DISTINCT session_id AS id FROM tool_calls
            WHERE tool_name = 'Agent' AND subagent_type IS NULL
            ORDER BY session_id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [{"id": r["id"]} for r in rows]

    def top_level_sessions_computed_before(self, iso_ts: str) -> list[dict[str, Any]]:
        """Top-level sessions last recomputed before ``iso_ts``. Feeds one-shot
        "re-read everything once" backfills: a re-ingest bumps ``computed_at``,
        so a session drops out of this set as soon as it has been redone.
        Sub-agent ids (containing '/') are excluded — they're re-read via their
        parent. Unbounded on purpose: the caller filters to files still on
        disk before applying the per-run cap, so deleted sessions can't
        crowd out live ones."""
        rows = self.db.execute(
            """
            SELECT id FROM sessions
            WHERE computed_at < ? AND instr(id, '/') = 0
            ORDER BY id
            """,
            (iso_ts,),
        ).fetchall()
        return [{"id": r["id"]} for r in rows]

    def get_app_meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM app_meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def set_app_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)", (key, value))
        self.db.commit()

    # ─── Prompts ───────────────────────────────────────────────────────

    def insert_prompts(self, rows: list[Prompt]) -> None:
        if not rows:
            return
        params = [
            {
                "timestamp_ms": r.timestamp_ms,
                "project_path": r.project_path,
                "display": r.display,
                "pasted_chars": r.pasted_chars,
                "is_slash": r.is_slash,
            }
            for r in rows
        ]
        with self.db:
            self.db.executemany(
                """
                INSERT INTO prompts (timestamp_ms, project_path, display, pasted_chars, is_slash)
                VALUES (:timestamp_ms, :project_path, :display, :pasted_chars, :is_slash)
                """,
                params,
            )

    def search_prompts(
        self,
        *,
        q: str | None = None,
        limit: int,
        project: str | None = None,
        include_slash: bool = False,
    ) -> dict[str, Any]:
        slash_clause = "1=1" if include_slash else "is_slash = 0"
        project_clause = "AND p.project_path = ?" if project else ""
        project_params: tuple = (project,) if project else ()

        if q and q.strip():
            fts = q.strip()
            sql = f"""
                SELECT p.*, snippet(prompts_fts, 0, '<mark>', '</mark>', '…', 16) AS snippet
                FROM prompts_fts
                JOIN prompts p ON p.id = prompts_fts.rowid
                WHERE prompts_fts MATCH ? AND {slash_clause} {project_clause}
                ORDER BY bm25(prompts_fts)
                LIMIT ?
            """
            count_sql = f"""
                SELECT COUNT(*) AS n FROM prompts_fts
                JOIN prompts p ON p.id = prompts_fts.rowid
                WHERE prompts_fts MATCH ? AND {slash_clause} {project_clause}
            """
            params = (fts, *project_params)
            rows = [dict(r) for r in self.db.execute(sql, (*params, limit)).fetchall()]
            total_row = self.db.execute(count_sql, params).fetchone()
            return {"total": total_row["n"] if total_row else 0, "rows": rows}

        sql = f"""
            SELECT p.*, p.display AS snippet
            FROM prompts p
            WHERE {slash_clause} {project_clause}
            ORDER BY p.timestamp_ms DESC
            LIMIT ?
        """
        count_sql = f"""
            SELECT COUNT(*) AS n FROM prompts p
            WHERE {slash_clause} {project_clause}
        """
        rows = [dict(r) for r in self.db.execute(sql, (*project_params, limit)).fetchall()]
        total_row = self.db.execute(count_sql, project_params).fetchone()
        return {"total": total_row["n"] if total_row else 0, "rows": rows}

    def prompt_stats(self) -> dict[str, Any]:
        row = self.db.execute(
            """
            SELECT COUNT(*) AS total,
                   COUNT(DISTINCT project_path) AS projects,
                   MIN(timestamp_ms) AS oldest_ms
            FROM prompts
            """
        ).fetchone()
        return {
            "total": row["total"] if row else 0,
            "projects": row["projects"] if row else 0,
            "oldest_ms": row["oldest_ms"] if row else None,
        }

    def prompt_projects(self) -> list[str]:
        rows = self.db.execute(
            "SELECT DISTINCT project_path FROM prompts ORDER BY project_path"
        ).fetchall()
        return [r["project_path"] for r in rows]

    def link_prompt_to_session(self, project_path: str, timestamp_ms: int) -> str | None:
        row = self.db.execute(
            """
            SELECT id FROM sessions
            WHERE project_path = ?
              AND started_at_ms IS NOT NULL
              AND started_at_ms <= ?
              AND (ended_at_ms IS NULL OR ended_at_ms >= ?)
            ORDER BY ABS(started_at_ms - ?)
            LIMIT 1
            """,
            (project_path, timestamp_ms, timestamp_ms, timestamp_ms),
        ).fetchone()
        return row["id"] if row else None

    # ─── Transcript segments ───────────────────────────────────────────

    def upsert_transcript_segments(self, rows: list[TranscriptSegment]) -> None:
        if not rows:
            return
        params = [
            {
                "uid": r.uid,
                "session_id": r.session_id,
                "timestamp": r.timestamp,
                "role": r.role,
                "text": r.text,
                "tool_use_id": r.tool_use_id,
            }
            for r in rows
        ]
        with self.db:
            self.db.executemany(
                """
                INSERT INTO transcript_segments (uid, session_id, timestamp, role, text, tool_use_id)
                VALUES (:uid, :session_id, :timestamp, :role, :text, :tool_use_id)
                ON CONFLICT(uid) DO UPDATE SET
                  session_id=excluded.session_id, timestamp=excluded.timestamp,
                  role=excluded.role, text=excluded.text,
                  tool_use_id=excluded.tool_use_id
                """,
                params,
            )

    def count_segments_for_session(self, session_id: str) -> int:
        row = self.db.execute(
            "SELECT COUNT(*) AS n FROM transcript_segments WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return row["n"] if row else 0

    def sessions_with_unpriced_turns(
        self, priced_models: list[str], limit: int
    ) -> list[dict[str, Any]]:
        """Sessions with zero-cost turns whose model the CURRENT pricing
        table prices — i.e. turns ingested before the model was in the
        bundled table. Restricting to now-priced models keeps still-unknown
        models from re-ingesting on every startup. Sub-agent ids collapse
        to their parent so the backfill re-ingests the parent file tree."""
        if not priced_models:
            return []
        ph = ",".join("?" for _ in priced_models)
        rows = self.db.execute(
            f"""
            SELECT DISTINCT
              CASE WHEN instr(t.session_id, '/') > 0
                   THEN substr(t.session_id, 1, instr(t.session_id, '/') - 1)
                   ELSE t.session_id END AS id
            FROM turns t
            WHERE t.cost_usd = 0
              AND t.model IN ({ph})
              AND (t.fresh_input_tokens + t.output_tokens
                   + t.cache_read_tokens + t.cache_write_tokens) > 0
            ORDER BY id
            LIMIT ?
            """,
            [*priced_models, limit],
        ).fetchall()
        return [{"id": r["id"]} for r in rows]

    def tool_output_for(self, session_id: str, tool_use_id: str) -> str | None:
        """Indexed tool_result text for one call, or None if not indexed."""
        row = self.db.execute(
            """
            SELECT text FROM transcript_segments
            WHERE session_id = ? AND tool_use_id = ? AND role = 'tool_result'
            LIMIT 1
            """,
            (session_id, tool_use_id),
        ).fetchone()
        return row["text"] if row else None

    def sessions_missing_tool_use_ids(self, limit: int) -> list[dict[str, Any]]:
        """Sessions whose indexed tool_result segments predate the
        tool_use_id column (NULL linkage). Sub-agent session ids collapse to
        their parent so the backfill can re-ingest the parent file tree."""
        rows = self.db.execute(
            """
            SELECT DISTINCT
              CASE WHEN instr(ts.session_id, '/') > 0
                   THEN substr(ts.session_id, 1, instr(ts.session_id, '/') - 1)
                   ELSE ts.session_id END AS id
            FROM transcript_segments ts
            WHERE ts.role = 'tool_result' AND ts.tool_use_id IS NULL
            ORDER BY id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [{"id": r["id"]} for r in rows]

    def sessions_missing_segments(self, limit: int) -> list[dict[str, Any]]:
        rows = self.db.execute(
            """
            SELECT s.id FROM sessions s
            LEFT JOIN (SELECT DISTINCT session_id FROM transcript_segments) t ON t.session_id = s.id
            WHERE t.session_id IS NULL
            ORDER BY s.started_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [{"id": r["id"]} for r in rows]

    def search_transcripts(
        self,
        *,
        q: str,
        limit: int,
        project: str | None = None,
        session_id: str | None = None,
        roles: list[str] | None = None,
    ) -> dict[str, Any]:
        # Filters shared by both the FTS-search and the empty-query browse path.
        extra: list[str] = []
        extra_params: list[Any] = []
        if project:
            extra.append("s.project_path = ?")
            extra_params.append(project)
        if session_id:
            extra.append("seg.session_id = ?")
            extra_params.append(session_id)
        if roles:
            extra.append(f"seg.role IN ({','.join('?' for _ in roles)})")
            extra_params.extend(roles)

        if q and q.strip():
            where = "WHERE transcript_fts MATCH ?" + "".join(f" AND {c}" for c in extra)
            sql = f"""
                SELECT seg.uid, seg.session_id, seg.timestamp, seg.role, seg.text,
                       snippet(transcript_fts, 0, '<mark>', '</mark>', '…', 16) AS snippet,
                       s.project_path AS project_path
                FROM transcript_fts
                JOIN transcript_segments seg ON seg.rowid = transcript_fts.rowid
                LEFT JOIN sessions s ON s.id = seg.session_id
                {where}
                ORDER BY bm25(transcript_fts)
                LIMIT ?
            """
            count_sql = f"""
                SELECT COUNT(*) AS n FROM transcript_fts
                JOIN transcript_segments seg ON seg.rowid = transcript_fts.rowid
                LEFT JOIN sessions s ON s.id = seg.session_id
                {where}
            """
            params = [q.strip(), *extra_params]
            rows = [dict(r) for r in self.db.execute(sql, (*params, limit)).fetchall()]
            total_row = self.db.execute(count_sql, params).fetchone()
            return {"total": total_row["n"] if total_row else 0, "rows": rows}

        # Empty query → browse the most recent segments (no FTS MATCH). Mirrors
        # search_prompts' browse path so the role toggles work without typing.
        where = ("WHERE " + " AND ".join(extra)) if extra else ""
        sql = f"""
            SELECT seg.uid, seg.session_id, seg.timestamp, seg.role, seg.text,
                   substr(seg.text, 1, 200) AS snippet,
                   s.project_path AS project_path
            FROM transcript_segments seg
            LEFT JOIN sessions s ON s.id = seg.session_id
            {where}
            ORDER BY seg.timestamp DESC
            LIMIT ?
        """
        count_sql = f"""
            SELECT COUNT(*) AS n FROM transcript_segments seg
            LEFT JOIN sessions s ON s.id = seg.session_id
            {where}
        """
        rows = [dict(r) for r in self.db.execute(sql, (*extra_params, limit)).fetchall()]
        total_row = self.db.execute(count_sql, extra_params).fetchone()
        return {"total": total_row["n"] if total_row else 0, "rows": rows}

    def segment_projects(self) -> list[str]:
        rows = self.db.execute(
            """
            SELECT DISTINCT s.project_path
            FROM transcript_segments seg
            JOIN sessions s ON s.id = seg.session_id
            WHERE s.project_path IS NOT NULL AND s.project_path <> ''
            ORDER BY s.project_path
            """
        ).fetchall()
        return [r["project_path"] for r in rows]

    def segment_stats(self) -> dict[str, int]:
        row = self.db.execute(
            """
            SELECT COUNT(*) AS total, COUNT(DISTINCT session_id) AS sessions
            FROM transcript_segments
            """
        ).fetchone()
        return {
            "total": row["total"] if row else 0,
            "sessions": row["sessions"] if row else 0,
        }

    def clear_all_segments(self) -> None:
        self.db.execute("DELETE FROM transcript_segments")

    def db_size_bytes(self) -> int:
        """Sum of the main db + -wal + -shm files (or page_count * page_size for :memory:)."""
        # sqlite3.Connection doesn't expose .name like better-sqlite3; we
        # fetch the main file from PRAGMA database_list.
        cur = self.db.execute("PRAGMA database_list")
        path = None
        for row in cur.fetchall():
            if row["name"] == "main":
                path = row["file"]
                break
        if not path:
            row = self.db.execute(
                """
                SELECT (SELECT page_count FROM pragma_page_count()) *
                       (SELECT page_size  FROM pragma_page_size()) AS bytes
                """
            ).fetchone()
            return row["bytes"] if row else 0
        total = 0
        for suffix in ("", "-wal", "-shm"):
            try:
                total += os.stat(path + suffix).st_size
            except OSError:
                pass
        return total

    def vacuum(self) -> None:
        # VACUUM cannot run on a connection that has any statement or
        # transaction in progress — and this connection is shared across the
        # watcher, scheduler, and request threads, so something is often
        # mid-flight. Run it on a dedicated short-lived connection to the same
        # file instead (busy_timeout covers cross-connection write locks).
        cur = self.db.execute("PRAGMA database_list")
        path = None
        for row in cur.fetchall():
            if row["name"] == "main":
                path = row["file"]
                break
        if not path:
            return  # :memory: or unknown — nothing to reclaim on disk
        conn = sqlite3.connect(path, isolation_level=None)
        try:
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("VACUUM")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()

    # ─── Alerts ────────────────────────────────────────────────────────

    def upsert_alert(self, a: Alert) -> int:
        """Insert or update an alert keyed on (detector, dedup_key).

        Same-severity upserts keep ``first_seen_at`` and ``seen_at`` as-is.
        Severity changes reset ``seen_at`` to NULL so the dashboard re-pings
        a state that has escalated (e.g., warning → critical).

        Single statement with ``RETURNING id`` so there's no transaction
        wrapper, no implicit cursor reuse, and no second round trip — the
        whole thing is one prepared statement we can run from any thread.
        """
        params = {
            "detector": a.detector,
            "dedup_key": a.dedup_key,
            "severity": a.severity,
            "title": a.title,
            "message": a.message,
            "metadata": json.dumps(a.metadata),
            "first_seen_at": a.first_seen_at,
            "last_seen_at": a.last_seen_at,
        }
        cur = self.db.execute(
            """
            INSERT INTO alerts (detector, dedup_key, severity, title, message, metadata,
                                first_seen_at, last_seen_at)
            VALUES (:detector, :dedup_key, :severity, :title, :message, :metadata,
                    :first_seen_at, :last_seen_at)
            ON CONFLICT(detector, dedup_key) DO UPDATE SET
              title = excluded.title,
              message = excluded.message,
              metadata = excluded.metadata,
              last_seen_at = excluded.last_seen_at,
              seen_at = CASE
                WHEN resolved_at IS NOT NULL THEN NULL
                WHEN severity = excluded.severity THEN seen_at
                ELSE NULL
              END,
              resolved_at = NULL,
              severity = excluded.severity
            RETURNING id
            """,
            params,
        )
        row = cur.fetchone()
        return int(row["id"])

    def list_alerts(self, *, limit: int = 50) -> list[Alert]:
        rows = self.db.execute(
            "SELECT * FROM alerts WHERE resolved_at IS NULL "
            "ORDER BY last_seen_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_alert(r) for r in rows]

    def list_unseen_alerts(self, *, severity: str | None = None) -> list[Alert]:
        if severity:
            rows = self.db.execute(
                "SELECT * FROM alerts WHERE seen_at IS NULL AND resolved_at IS NULL "
                "AND severity = ? ORDER BY last_seen_at DESC",
                (severity,),
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM alerts WHERE seen_at IS NULL AND resolved_at IS NULL "
                "ORDER BY last_seen_at DESC"
            ).fetchall()
        return [_row_to_alert(r) for r in rows]

    def resolve_stale_alerts(
        self, *, detector: str, active_dedup_keys: list[str]
    ) -> int:
        """Mark resolved_at = now() for unresolved alerts under ``detector``
        whose dedup_key is NOT in ``active_dedup_keys``.

        Empty ``active_dedup_keys`` resolves every unresolved row under that
        detector — correct when a detector emitted no findings this tick.
        """
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        if not active_dedup_keys:
            cur = self.db.execute(
                "UPDATE alerts SET resolved_at = ? "
                "WHERE detector = ? AND resolved_at IS NULL",
                (now, detector),
            )
        else:
            placeholders = ",".join("?" * len(active_dedup_keys))
            cur = self.db.execute(
                f"UPDATE alerts SET resolved_at = ? "
                f"WHERE detector = ? AND resolved_at IS NULL "
                f"AND dedup_key NOT IN ({placeholders})",
                (now, detector, *active_dedup_keys),
            )
        return cur.rowcount

    def mark_alert_seen(self, alert_id: int) -> bool:
        cur = self.db.execute(
            "UPDATE alerts SET seen_at = ? WHERE id = ? AND seen_at IS NULL",
            (datetime.now(timezone.utc).isoformat(), alert_id),
        )
        if cur.rowcount > 0:
            return True
        # Already-seen rows hit rowcount=0 but the row exists — treat as success.
        row = self.db.execute(
            "SELECT 1 FROM alerts WHERE id = ?", (alert_id,)
        ).fetchone()
        return row is not None

    # ─── App-meta-backed settings ──────────────────────────────────────

    def is_search_indexing_enabled(self) -> bool:
        row = self.db.execute(
            "SELECT value FROM app_meta WHERE key = 'enable_transcript_search'"
        ).fetchone()
        if row:
            return row["value"] == "1"
        # Migration default: ON if segments already exist, OFF otherwise.
        has_data = self.segment_stats()["total"] > 0
        self.set_search_indexing_enabled(has_data)
        return has_data

    def set_search_indexing_enabled(self, enabled: bool) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO app_meta (key, value) VALUES ('enable_transcript_search', ?)",
            ("1" if enabled else "0",),
        )
