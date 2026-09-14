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
from .pipeline import ingest_file

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
    _prepare_streamed_output_fix(repo)

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


def _streamed_fix_started_at(repo: Repository) -> str:
    """Return the cutoff for the streamed-output re-read, stamping it once.

    Called from ``run_first_pass_ingest`` *before* any ingest so sessions
    written by this (fixed) process land after the cutoff and aren't re-read.
    A fresh DB has nothing to correct, so the fix is marked done outright.
    The backfill also calls this as a fallback for direct callers.
    """
    key = STREAMED_OUTPUT_FIX_KEY + "_started_at"
    started_at = repo.get_app_meta(key)
    if started_at is None:
        started_at = _iso_now()
        repo.set_app_meta(key, started_at)
    return started_at


def _prepare_streamed_output_fix(repo: Repository) -> None:
    if repo.get_app_meta(STREAMED_OUTPUT_FIX_KEY) == "1":
        return
    if not repo.list_sessions(limit=1):
        repo.set_app_meta(STREAMED_OUTPUT_FIX_KEY, "1")
        return
    _streamed_fix_started_at(repo)


def _backfill_missing_derived_data(
    adapters: list[Adapter], repo: Repository, table: PricingTable
) -> None:
    """Re-ingest sessions missing tool_calls / segments after a slice upgrade."""
    missing_tools = repo.sessions_missing_tool_calls(200)
    ids: set[str] = {c["id"] for c in missing_tools}
    # Sessions whose segments predate the tool_use_id column also need their
    # sub-agent files re-read, not just the parent — track them separately.
    deep_reset: set[str] = set()
    if repo.is_search_indexing_enabled():
        for c in repo.sessions_missing_segments(200):
            # A sub-agent's transcript is only re-read when its parent is
            # re-ingested with the sub-agent file offsets reset (deep_reset).
            # The sub-agent id itself is skipped by the loop below ("/" guard),
            # so route to the parent (a top-level id maps to itself) and force a
            # deep reset — otherwise sub-agent segments never backfill and the
            # Sub-agents "Task given" stays empty after enabling indexing.
            parent = c["id"].split("/", 1)[0]
            ids.add(parent)
            deep_reset.add(parent)
        for c in repo.sessions_missing_tool_use_ids(200):
            ids.add(c["id"])
            deep_reset.add(c["id"])
    # Zero-cost turns whose model the current table prices: ingested before
    # the model was in the bundled table. Sub-agent turns need their files
    # re-read too, hence deep_reset.
    for c in repo.sessions_with_unpriced_turns(list(table.models.keys()), 200):
        ids.add(c["id"])
        deep_reset.add(c["id"])
    # One-shot: Agent calls ingested before the Task->Agent rename fix have
    # subagent_type NULL. Re-read their parents once; default-agent calls stay
    # NULL legitimately, so the flag stops this from re-running every start.
    agent_fix_key = "backfill_agent_subagent_type_v1"
    agent_fix_pending = repo.get_app_meta(agent_fix_key) != "1"
    if agent_fix_pending:
        for c in repo.sessions_with_untyped_agent_calls(200):
            ids.add(c["id"].split("/", 1)[0])
    # session_id "<agent>:<native id>" → file path lookup, via the adapter hook
    # (never the file stem: Codex rollouts are named rollout-<ts>-<uuid>.jsonl).
    file_by_basename: dict[str, tuple[Adapter, Path]] = {}
    for a in adapters:
        for f in a.discover_session_files():
            file_by_basename[a.native_session_id(f)] = (a, f)

    # One-shot: turns ingested before extract_turns learned to take
    # output_tokens from a message's final streamed line hold placeholder
    # counts. Nothing in the DB distinguishes them, so re-read every session
    # still on disk (sub-agents too) once. Sessions whose transcript Claude
    # Code has since deleted can't be corrected and must not block completion.
    streamed_fix_pending = repo.get_app_meta(STREAMED_OUTPUT_FIX_KEY) != "1"
    if streamed_fix_pending:
        started_at = _streamed_fix_started_at(repo)
        stale_on_disk = [
            c["id"]
            for c in repo.top_level_sessions_computed_before(started_at)
            if c["id"].split(":", 1)[-1] in file_by_basename
        ]
        ids.update(stale_on_disk)
        deep_reset.update(stale_on_disk)

    candidates = sorted(ids)[:200]
    if agent_fix_pending and len(candidates) < 200:
        repo.set_app_meta(agent_fix_key, "1")
    if streamed_fix_pending and len(candidates) < 200:
        repo.set_app_meta(STREAMED_OUTPUT_FIX_KEY, "1")
    if not candidates:
        return

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
