"""Map stored session ids back to their transcript files (for backfills)."""
from __future__ import annotations

from pathlib import Path

from ..adapters.base import Adapter, native_session_id_for


def files_by_session(adapters: list[Adapter]) -> dict[str, list[tuple[Adapter, Path]]]:
    """``"<agent>:<native id>"`` -> every top-level file that feeds it.

    Agent-qualified so two adapters can't collide, and a list because one
    Codex thread can be written across several rollout files."""
    out: dict[str, list[tuple[Adapter, Path]]] = {}
    for a in adapters:
        for f in a.discover_session_files():
            out.setdefault(f"{a.agent}:{native_session_id_for(a, f)}", []).append((a, f))
    return out
