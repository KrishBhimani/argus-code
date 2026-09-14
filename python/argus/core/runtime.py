"""Shared core runtime: DB + repo + first-pass ingest + watcher + scheduler.

Extracted verbatim from the inline lifecycle that used to live in
``cli.py:start()`` (open_db → repo → adapters → first-pass → watcher →
scheduler, and the reverse on shutdown). Both ``argus start`` and the
``argusd`` daemon construct a CoreRuntime so neither duplicates the wiring.

``read_only=True`` opens the DB read-only and skips ALL ingestion (no
first-pass, no watcher, no scheduler) — used when the dashboard yields to a
live daemon. ``read_only=False`` reproduces today's in-process behavior
exactly.
"""
from __future__ import annotations

import logging
from pathlib import Path

from ..adapters.base import Adapter
from ..adapters.registry import available_adapters
from ..collector.first_run import IngestStatus, run_first_pass_ingest
from ..collector.scheduler import start_scheduler
from ..collector.watcher import start_watcher
from ..detectors.registry import available_detectors
from ..pricing.load import load_pricing_table
from ..pricing.types import PricingTable
from ..store.db import open_db
from ..store.repository import Repository

logger = logging.getLogger("argus")

# Static status reported in read-only mode (the dashboard's footer falls
# back to its own DB-derived sessionCount, so processed/total are 0).
_READ_ONLY_STATUS = IngestStatus(
    foreground_complete=True, pending=0, processed=0, total=0
)


class NoAdaptersError(RuntimeError):
    """Raised when no adapter's data is present on this machine."""


class CoreRuntime:
    def __init__(
        self,
        data_dir: Path,
        *,
        read_only: bool = False,
        recent_days: int = 30,
    ) -> None:
        self._data_dir = Path(data_dir)
        self.read_only = read_only
        self._recent_days = recent_days
        self._db = None
        self.repo: Repository | None = None
        self.adapters: list[Adapter] = []
        self.pricing_table: PricingTable | None = None
        self._first_run = None
        self._watcher = None
        self._scheduler = None

    def start(self, *, require_adapters: bool = True) -> None:
        """Open the DB and begin ingesting.

        ``require_adapters=False`` is for long-running services (argusd): a
        missing ``~/.claude`` is a "not yet" rather than an error, so we come up
        idle and let :meth:`try_activate` pick things up when the directory
        appears. The foreground ``argus start`` keeps the default, because
        failing immediately with a clear message is better feedback there.
        """
        self._db = open_db(self._data_dir / "argus.db", read_only=self.read_only)
        self.repo = Repository(self._db)
        self.pricing_table = load_pricing_table()

        self.adapters = available_adapters()
        if not self.adapters:
            if require_adapters:
                raise NoAdaptersError(
                    "No adapter data found. Argus expects ~/.claude/ (Claude Code) "
                    "or ~/.codex/sessions/ (Codex CLI) to be present."
                )
            logger.warning(
                "No agent data found (~/.claude/ is missing). Staying up and "
                "watching for it — ingest starts automatically once it appears."
            )
            return

        self._activate()

    def try_activate(self) -> bool:
        """Start ingesting if adapters have appeared since startup.

        Returns True only on the transition from "nothing to do" to "running",
        so callers can log it once. Cheap and idempotent — safe to poll.
        """
        if self.adapters or self.repo is None or self.read_only:
            return False
        found = available_adapters()
        if not found:
            return False
        self.adapters = found
        self._activate()
        return True

    def _activate(self) -> None:
        """Kick off first-pass ingest, the watcher, and the detector scheduler."""
        assert self.repo is not None and self.pricing_table is not None
        logger.info("Detected adapters: %s", ", ".join(a.agent for a in self.adapters))

        if self.read_only:
            logger.info("argusd active — dashboard read-only (no ingest/scheduler).")
            return

        logger.info("Argus: ingesting recent sessions...")
        self._first_run = run_first_pass_ingest(
            self.adapters, self.repo, self.pricing_table, recent_days=self._recent_days
        )
        self._first_run.wait_foreground()
        s = self._first_run.status()
        logger.info(
            "Argus: foreground ingest complete (%d/%d files), starting watcher...",
            s.processed,
            s.total,
        )

        self._watcher = start_watcher(self.adapters, self.repo, self.pricing_table)

        detectors = available_detectors()
        logger.info(
            "Loaded %d detectors: %s", len(detectors), [d.name for d in detectors]
        )
        self._scheduler = start_scheduler(detectors, self.repo)

    def ingest_status(self) -> IngestStatus:
        """Callable handed to the server. Static-complete in read-only mode."""
        if self._first_run is None:
            return _READ_ONLY_STATUS
        return self._first_run.status()

    def stop(self) -> None:
        if self._scheduler is not None:
            self._scheduler.stop()
            self._scheduler = None
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher = None
        if self._first_run is not None:
            # Let the background backfill thread finish so it can't write to a
            # closed DB. Returns instantly if already done; bounded so a huge
            # backfill can't hang shutdown indefinitely.
            #
            # join() rather than wait_backfill(): the event is set as the
            # thread's last statement, leaving a window where it is still
            # unwinding. Narrow, but the failure mode is a segfault, not an
            # exception — worth closing properly.
            self._first_run.join(timeout=10)
            self._first_run = None
        if self._db is not None:
            self._db.close()
            self._db = None
