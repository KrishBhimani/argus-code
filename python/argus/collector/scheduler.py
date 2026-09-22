"""Periodic detector loop. Daemon thread inside the same process as the
server; runs each detector once at boot, then every ``interval_sec``
(default 600) until ``.stop()`` is called.

Detectors are pure: they read the repo and return Findings. The scheduler
is the only thing that writes alerts (``repo.upsert_alert``). This keeps
the "detectors are pure" rule a structural property rather than a
convention.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from ..detectors.base import Detector, Finding
from ..schema.types import Alert
from ..store.repository import Repository

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _finding_to_alert(f: Finding, now_iso: str) -> Alert:
    return Alert(
        detector=f.detector,
        dedup_key=f.dedup_key,
        severity=f.severity,
        title=f.title,
        message=f.message,
        metadata=dict(f.metadata),
        first_seen_at=now_iso,
        last_seen_at=now_iso,
        seen_at=None,
    )


def _run_once(detectors: list[Detector], repo: Repository) -> None:
    now = _now_iso()
    for detector in detectors:
        try:
            findings = detector.detect(repo, now)
        except Exception:  # noqa: BLE001
            logger.exception("Detector %s crashed", getattr(detector, "name", "?"))
            continue
        active_keys: list[str] = []
        for f in findings:
            try:
                repo.upsert_alert(_finding_to_alert(f, now))
                active_keys.append(f.dedup_key)
            except Exception:  # noqa: BLE001
                logger.exception("Failed to write alert from %s", detector.name)
        try:
            repo.resolve_stale_alerts(
                detector=detector.name, active_dedup_keys=active_keys
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to reconcile alerts for %s", detector.name)


class SchedulerHandle:
    def __init__(self, thread: threading.Thread, stop_event: threading.Event) -> None:
        self._thread = thread
        self._stop = stop_event

    def stop(self, timeout: float = 10.0) -> bool:
        """Stop the loop; True once its thread has exited.

        A detector tick writes alerts through the shared DB connection, so
        callers must not close the connection while this returns False.
        """
        self._stop.set()
        self._thread.join(timeout=timeout)
        return not self._thread.is_alive()


def start_scheduler(
    detectors: list[Detector],
    repo: Repository,
    *,
    interval_sec: int = 600,
) -> SchedulerHandle:
    stop_event = threading.Event()

    def loop() -> None:
        _run_once(detectors, repo)  # startup tick
        while not stop_event.wait(interval_sec):
            _run_once(detectors, repo)

    t = threading.Thread(target=loop, name="argus-scheduler", daemon=True)
    t.start()
    return SchedulerHandle(t, stop_event)
