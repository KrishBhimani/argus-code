"""Detector contract, plus the handful of helpers every detector shares.

Detectors are pure: they read from a Repository and return findings.
Writes are performed by the scheduler, not the detector. This split keeps
detectors trivially unit-testable and the alerts table single-writer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from ..schema.types import AlertSeverity

if TYPE_CHECKING:
    from ..store.repository import Repository


@dataclass(frozen=True)
class Finding:
    """One alert-shaped result returned by a detector."""

    detector: str
    dedup_key: str
    severity: AlertSeverity
    title: str
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Detector(Protocol):
    name: str

    def detect(self, repo: "Repository", now_iso: str) -> list[Finding]: ...


def iso_at_offset(now_iso: str, days_ago: float) -> str:
    """``now_iso`` minus ``days_ago`` days, as a Z-suffixed ISO timestamp.

    Window boundaries are compared as strings against the DB's ISO
    timestamps, so the suffix has to match the stored format exactly.
    """
    base = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return (base - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


def project_label(project_path: str) -> str:
    """Short, human-readable name for a project path (its last segment).

    Alert titles are one line in a narrow panel, so a full path would push
    the number off-screen; the whole path stays in the finding metadata.
    """
    trimmed = project_path.replace("\\", "/").rstrip("/")
    if not trimmed:
        return project_path or "(unknown project)"
    return trimmed.rsplit("/", 1)[-1] or trimmed
