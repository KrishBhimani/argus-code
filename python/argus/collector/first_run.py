"""First-pass ingest: walk every adapter's files, recent first.

Recent files run synchronously in the foreground so the dashboard is
useful immediately. Older files run in a background ThreadPoolExecutor.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..adapters.base import Adapter
from ..pricing.types import PricingTable
from ..store.repository import Repository
from .pipeline import ingest_file, recompute_stored_session

logger = logging.getLogger(__name__)


@dataclass
class IngestStatus:
    foreground_complete: bool
    pending: int
    processed: int
    total: int


#: Name of the background backfill thread. Public so shutdown paths (and the
#: test fixture that closes the DB) can find and join it without a handle.
FIRST_RUN_THREAD_NAME = "argus-firstrun-bg"

#: app_meta flag for the one-shot re-read that corrects output_tokens stored
#: from a message's first streamed line (placeholder usage). "1" once every
#: session that was still on disk has been re-ingested. The companion
#: ``…_started_at`` key pins the cutoff so the sweep converges under the
#: per-run cap: a re-ingest bumps ``sessions.computed_at`` past it.
STREAMED_OUTPUT_FIX_KEY = "backfill_streamed_output_tokens_v1"

#: app_meta flag for the one-shot re-read that restores tool-call ``is_error``
#: flags lost by pre-fix incremental ingest (a tool_result arriving in a later
#: tick than its tool_use was dropped). Same "re-read everything on disk once"
#: shape as the streamed-output sweep; the re-read also rewrites file-wide
#: turn sequences and the session's project from the file start.
TOOL_ERRORS_FIX_KEY = "backfill_tool_errors_v1"

#: One-shot in-DB repair of duration_sec / started_at_ms / ended_at_ms on rows
#: whose derived time columns a late ingest tick overwrote (no re-read needed).
SESSION_DURATION_REPAIR_KEY = "repair_session_duration_v1"

#: One-shot removal of turns/tool calls that pre-fix ingest stored under a
#: forked session although they were copies of its parent's lines.
FORK_DEDUP_REPAIR_KEY = "repair_fork_duplicates_v1"

#: "Re-read every session still on disk once" sweeps, oldest first.
_REREAD_ALL_SWEEPS = (STREAMED_OUTPUT_FIX_KEY, TOOL_ERRORS_FIX_KEY)

#: Per-run cap on backfill re-reads (see collector/AGENTS.md "Bounded per run").
BACKFILL_CAP = 200


def join_first_run_threads(timeout: float = 10.0) -> list[str]:
    """Wait for any live first-run background thread; return those still alive.

    The background phase writes to the SQLite connection. Closing that
    connection from another thread while it does is undefined behaviour — in
    CI it segfaulted the whole pytest process (exit 139) rather than failing a
    test. Anything that closes the DB must come through here first.

    Finds threads by name rather than requiring a handle, so a caller that
    dropped its ``FirstRunHandle`` is still covered.
    """
    deadline = time.monotonic() + timeout
    for t in [x for x in threading.enumerate() if x.name == FIRST_RUN_THREAD_NAME]:
        t.join(max(0.0, deadline - time.monotonic()))
    return [
        x.name
        for x in threading.enumerate()
        if x.name == FIRST_RUN_THREAD_NAME and x.is_alive()
    ]


class FirstRunHandle:
    """Returned by ``run_first_pass_ingest``; exposes foreground/backfill futures."""

    def __init__(self) -> None:
        self._processed = 0
        self._total = 0
        self._foreground_complete = False
        self._lock = threading.Lock()
        self._foreground_done = threading.Event()
        self._backfill_done = threading.Event()
        self._thread: threading.Thread | None = None

    def _inc(self) -> None:
        with self._lock:
            self._processed += 1

    def status(self) -> IngestStatus:
        with self._lock:
            return IngestStatus(
                foreground_complete=self._foreground_complete,
                pending=max(0, self._total - self._processed),
                processed=self._processed,
                total=self._total,
            )

    def wait_foreground(self, timeout: float | None = None) -> bool:
        return self._foreground_done.wait(timeout)

    def wait_backfill(self, timeout: float | None = None) -> bool:
        return self._backfill_done.wait(timeout)

    def join(self, timeout: float | None = None) -> bool:
        """Wait for the background thread to actually exit; True if it did.

        Stronger than ``wait_backfill``: that returns as soon as the *event* is
        set, leaving a brief window where the thread is still unwinding. Use
        this before closing the DB connection.
        """
        if self._thread is None:
            return True
        self._thread.join(timeout)
        return not self._thread.is_alive()


def run_first_pass_ingest(
    adapters: list[Adapter],
    repo: Repository,
    table: PricingTable,
    *,
    recent_days: int = 30,
) -> FirstRunHandle:
    """Kick off ingest. Recent files run inline; older files in a thread.

    Returns immediately with a handle whose ``status()`` is pollable, and
    whose ``wait_foreground()`` / ``wait_backfill()`` block until each
    phase finishes.
    """
    cutoff = time.time() - recent_days * 86_400
    handle = FirstRunHandle()
    _repair_session_durations_once(repo)
    for key in _REREAD_ALL_SWEEPS:
        _prepare_reread_sweep(repo, key)

    # Phase 1 (foreground, sync, in the calling thread).
    recent: list[tuple[Adapter, Path]] = []
    older: list[tuple[Adapter, Path]] = []
    for a in adapters:
        for f in a.discover_session_files():
            try:
                mtime = f.stat().st_mtime
            except OSError:
                continue
            (recent if mtime >= cutoff else older).append((a, f))

    with handle._lock:
        handle._total = len(recent) + len(older)

    for adapter, file in recent:
        try:
            ingest_file(adapter, file, repo, table)
        except Exception as e:  # noqa: BLE001
            repo.record_parse_error(
                {
                    "file": str(file),
                    "byte_offset": -1,
                    "reason": f"[ingest] {e}",
                    "raw_line_truncated": "",
                }
            )
        handle._inc()

    # Also ingest adapter-specific extras (e.g., history.jsonl) during the
    # foreground phase so they're available to the dashboard immediately.
    for a in adapters:
        for extra in a.extra_watch_paths():
            try:
                a.ingest_extra(extra, repo)
            except Exception as e:  # noqa: BLE001
                repo.record_parse_error(
                    {
                        "file": str(extra),
                        "byte_offset": -1,
                        "reason": f"[history] {e}",
                        "raw_line_truncated": "",
                    }
                )

    with handle._lock:
        handle._foreground_complete = True
    handle._foreground_done.set()

    # Phase 2 (background) — older files + missing-data backfill.
    def _background() -> None:
        for adapter, file in older:
            try:
                ingest_file(adapter, file, repo, table)
            except Exception as e:  # noqa: BLE001
                repo.record_parse_error(
                    {
                        "file": str(file),
                        "byte_offset": -1,
                        "reason": f"[ingest] {e}",
                        "raw_line_truncated": "",
                    }
                )
            handle._inc()
        _repair_fork_duplicates_once(adapters, repo, table)
        _backfill_missing_derived_data(adapters, repo, table)
        handle._backfill_done.set()

    thread = threading.Thread(
        target=_background, name=FIRST_RUN_THREAD_NAME, daemon=True
    )
    handle._thread = thread
    thread.start()
    return handle


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sweep_started_at(repo: Repository, key: str) -> str:
    """Return the cutoff for a re-read-everything sweep, stamping it once.

    Called from ``run_first_pass_ingest`` *before* any ingest so sessions
    written by this (fixed) process land after the cutoff and aren't re-read.
    A fresh DB has nothing to correct, so the fix is marked done outright.
    The backfill also calls this as a fallback for direct callers.
    """
    stamp_key = key + "_started_at"
    started_at = repo.get_app_meta(stamp_key)
    if started_at is None:
        started_at = _iso_now()
        repo.set_app_meta(stamp_key, started_at)
    return started_at


def _prepare_reread_sweep(repo: Repository, key: str) -> None:
    if repo.get_app_meta(key) == "1":
        return
    if not repo.list_sessions(limit=1):
        repo.set_app_meta(key, "1")
        return
    _sweep_started_at(repo, key)


def _repair_session_durations_once(repo: Repository) -> None:
    """Writer-path only: the read-only dashboard under argusd can't write."""
    if repo.get_app_meta(SESSION_DURATION_REPAIR_KEY) == "1":
        return
    fixed = repo.repair_session_time_columns()
    if fixed:
        logger.info("Repaired duration/start columns on %d sessions.", fixed)
    repo.set_app_meta(SESSION_DURATION_REPAIR_KEY, "1")


def _parse_whole_file(adapter: Adapter, file: Path) -> tuple[list, list]:
    """Parse every line of ``file`` (pure: no DB writes); (turns, tool_calls)."""
    turns: list = []
    calls: list = []
    offset = 0
    while True:
        result, new_offset = adapter.ingest_file(file, offset)
        turns.extend(result.turns)
        calls.extend(result.tool_calls)
        if new_offset <= offset:
            return turns, calls
        offset = new_offset


def _repair_fork_duplicates_once(
    adapters: list[Adapter], repo: Repository, table: PricingTable
) -> None:
    """Delete fork copies stored before fork de-duplication existed.

    Candidates are top-level sessions sharing a message id with another one.
    Each candidate's file is parsed (not ingested): a turn is a copy when its
    line claims an origin session that stores the same message — the same
    rule the live pipeline applies. Only those turns and their tool calls are
    deleted, then the session is recomputed from what remains. These are
    derived rows re-countable from the parent; no transcript data is lost.
    Sessions whose file is gone are left as they are (nothing to verify
    against). Idempotent: after one pass no candidate has a verified copy.
    """
    if repo.get_app_meta(FORK_DEDUP_REPAIR_KEY) == "1":
        return
    file_by_basename: dict[str, tuple[Adapter, Path]] = {}
    for a in adapters:
        for f in a.discover_session_files():
            file_by_basename[f.stem] = (a, f)
    fixed = 0
    for sid in repo.top_level_sessions_sharing_messages():
        match = file_by_basename.get(sid.split(":", 1)[-1])
        if match is None:
            continue
        adapter, file = match
        try:
            turns, calls = _parse_whole_file(adapter, file)
        except OSError:
            continue
        agent = sid.split(":", 1)[0]
        claims = {
            t.native_turn_id: t.metadata["origin_session_id"]
            for t in turns
            if t.metadata.get("origin_session_id")
            and f"{agent}:{t.metadata['origin_session_id']}" != sid
        }
        if not claims:
            continue
        stored = repo.existing_turn_ids([f"{agent}:{o}:{m}" for m, o in claims.items()])
        copied = sorted(m for m, o in claims.items() if f"{agent}:{o}:{m}" in stored)
        if not copied:
            continue
        tool_ids = [c.tool_use_id for c in calls if c.native_turn_id in set(copied)]
        repo.delete_duplicated_turns(sid, copied, tool_ids)
        recompute_stored_session(repo, sid, table.version)
        fixed += 1
    if fixed:
        logger.info("Removed copied parent turns from %d forked sessions.", fixed)
    repo.set_app_meta(FORK_DEDUP_REPAIR_KEY, "1")


def _stale_on_disk(
    repo: Repository, key: str, file_by_basename: dict[str, tuple[Adapter, Path]]
) -> list[str]:
    """Top-level sessions a pending sweep still has to re-read."""
    started_at = _sweep_started_at(repo, key)
    return [
        c["id"]
        for c in repo.top_level_sessions_computed_before(started_at)
        if c["id"].split(":", 1)[-1] in file_by_basename
    ]


def _backfill_missing_derived_data(
    adapters: list[Adapter], repo: Repository, table: PricingTable
) -> None:
    """Re-ingest sessions missing tool_calls / segments after a slice upgrade."""
    missing_tools = repo.sessions_missing_tool_calls(BACKFILL_CAP)
    ids: set[str] = {c["id"] for c in missing_tools}
    # Sessions whose segments predate the tool_use_id column also need their
    # sub-agent files re-read, not just the parent — track them separately.
    deep_reset: set[str] = set()
    if repo.is_search_indexing_enabled():
        for c in repo.sessions_missing_segments(BACKFILL_CAP):
            # A sub-agent's transcript is only re-read when its parent is
            # re-ingested with the sub-agent file offsets reset (deep_reset).
            # The sub-agent id itself is skipped by the loop below ("/" guard),
            # so route to the parent (a top-level id maps to itself) and force a
            # deep reset — otherwise sub-agent segments never backfill and the
            # Sub-agents "Task given" stays empty after enabling indexing.
            parent = c["id"].split("/", 1)[0]
            ids.add(parent)
            deep_reset.add(parent)
        for c in repo.sessions_missing_tool_use_ids(BACKFILL_CAP):
            ids.add(c["id"])
            deep_reset.add(c["id"])
    # Zero-cost turns whose model the current table prices: ingested before
    # the model was in the bundled table. Sub-agent turns need their files
    # re-read too, hence deep_reset.
    for c in repo.sessions_with_unpriced_turns(list(table.models.keys()), BACKFILL_CAP):
        ids.add(c["id"])
        deep_reset.add(c["id"])
    # One-shot: Agent calls ingested before the Task->Agent rename fix have
    # subagent_type NULL. Re-read their parents once; default-agent calls stay
    # NULL legitimately, so the flag stops this from re-running every start.
    agent_fix_key = "backfill_agent_subagent_type_v1"
    agent_fix_pending = repo.get_app_meta(agent_fix_key) != "1"
    if agent_fix_pending:
        for c in repo.sessions_with_untyped_agent_calls(BACKFILL_CAP):
            ids.add(c["id"].split("/", 1)[0])
    # session_id "claude_code:<basename>" → file path lookup.
    file_by_basename: dict[str, tuple[Adapter, Path]] = {}
    for a in adapters:
        for f in a.discover_session_files():
            file_by_basename[f.stem] = (a, f)

    # One-shot "re-read every session still on disk" sweeps. Nothing in the
    # DB distinguishes a pre-fix row (placeholder output_tokens from a
    # message's first streamed line; an is_error lost because the tool_result
    # arrived in a later tick), so each re-reads everything once, sub-agents
    # too. Sessions whose transcript Claude Code has since deleted can't be
    # corrected and must not block completion.
    pending_sweeps = [k for k in _REREAD_ALL_SWEEPS if repo.get_app_meta(k) != "1"]
    for key in pending_sweeps:
        stale = _stale_on_disk(repo, key, file_by_basename)
        ids.update(stale)
        deep_reset.update(stale)

    candidates = sorted(ids)[:BACKFILL_CAP]
    if agent_fix_pending and len(candidates) < BACKFILL_CAP:
        repo.set_app_meta(agent_fix_key, "1")

    _reread(candidates, deep_reset, file_by_basename, repo, table)

    # A sweep is done only when nothing it still needs remains — checked
    # AFTER the work, so a capped run can't mark it done early. A re-ingest
    # bumps computed_at past the stamp; a session whose re-read failed was
    # attempted and would fail again, so it doesn't hold the flag hostage.
    attempted = set(candidates)
    for key in pending_sweeps:
        if not set(_stale_on_disk(repo, key, file_by_basename)) - attempted:
            repo.set_app_meta(key, "1")


def _reread(
    candidates: list[str],
    deep_reset: set[str],
    file_by_basename: dict[str, tuple[Adapter, Path]],
    repo: Repository,
    table: PricingTable,
) -> None:
    """Reset offsets to 0 and re-ingest each candidate's file."""
    for id_ in candidates:
        if "/" in id_:  # sub-agent rollup ids — walked via parents
            continue
        colon = id_.find(":")
        if colon < 0:
            continue
        native = id_[colon + 1 :]
        match = file_by_basename.get(native)
        if match is None:
            continue
        adapter, file = match
        repo.set_file_offset(str(file), 0)
        if id_ in deep_reset:
            for sub in adapter.sub_session_files_for(file):
                repo.set_file_offset(str(sub), 0)
        try:
            ingest_file(adapter, file, repo, table)
        except Exception as e:  # noqa: BLE001
            repo.record_parse_error(
                {
                    "file": str(file),
                    "byte_offset": -1,
                    "reason": f"[backfill-tools] {e}",
                    "raw_line_truncated": "",
                }
            )
