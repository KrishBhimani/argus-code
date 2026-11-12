"""Opt-in: ingest a real Codex home and cross-check usage totals.

Run: ARGUS_REAL_CODEX_ROOT="$HOME/.codex" uv run pytest tests/adapters/codex/test_real_root.py
Expected per stored session (own turns, not rolled up): the sum of the
token_usage_record usage in its files (after each file's copied-history
cutoff) equals the stored turns' totals."""
from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path

import pytest

from argus.adapters.codex.adapter import CodexAdapter
from argus.adapters.codex.discover import peek_thread
from argus.collector.pipeline import ingest_file
from argus.pricing.load import load_pricing_table
from argus.store.db import open_db
from argus.store.repository import Repository

ROOT = os.environ.get("ARGUS_REAL_CODEX_ROOT")
pytestmark = pytest.mark.skipif(not ROOT, reason="set ARGUS_REAL_CODEX_ROOT to run")


def _expected_output_by_thread(root: Path) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for d in ("sessions", "archived_sessions"):
        for f in (root / d).rglob("*.jsonl"):
            info = peek_thread(f)
            if info is None:
                continue
            seen: set[str] = set()
            for line in f.open("rb"):
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(r, dict) or r.get("type") != "token_usage_record":
                    continue
                if info.history_start_ordinal is not None and (r.get("ordinal") or 0) < info.history_start_ordinal:
                    continue
                rid = r["payload"].get("response_id")
                if rid in seen:
                    continue
                seen.add(rid)
                out[info.thread_id] += int(r["payload"]["usage"].get("output_tokens") or 0)
    return dict(out)


def test_real_root_output_tokens_match(tmp_path: Path) -> None:
    root = Path(ROOT)
    a, table = CodexAdapter(root), load_pricing_table()
    repo = Repository(open_db(tmp_path / "argus.db"))
    for f in a.discover_session_files():
        ingest_file(a, f, repo, table)
    stored: dict[str, int] = {}
    for sid, total in repo.db.execute(
        "SELECT session_id, SUM(output_tokens) FROM turns WHERE session_id LIKE 'codex:%' GROUP BY session_id"
    ):
        stored[sid.split("/")[-1].removeprefix("codex:")] = total
    expected = {k: v for k, v in _expected_output_by_thread(root).items() if v}
    assert stored == expected
