"""Rollout line reader (completed in the next task)."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import IO, Iterator


@contextmanager
def open_rollout(path: Path) -> Iterator[IO[bytes] | None]:
    """Binary stream over a rollout; yields None when a .zst decoder is missing."""
    if path.name.endswith(".zst"):
        yield None
        return
    with open(path, "rb") as fh:
        yield fh
