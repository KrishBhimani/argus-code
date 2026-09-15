"""Opt-in smoke test against a real ~/.codex (set ARGUS_REAL_CODEX_ROOT).

Every rollout must ingest without parse errors and with non-negative usage;
child threads reachable from a parent must too. Nothing about content is
asserted or printed.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from argus.adapters.codex.adapter import CodexAdapter

ROOT = os.environ.get("ARGUS_REAL_CODEX_ROOT")
pytestmark = pytest.mark.skipif(not ROOT, reason="ARGUS_REAL_CODEX_ROOT not set")


def test_every_real_rollout_ingests_cleanly():
    a = CodexAdapter(Path(ROOT))
    files = a.discover_session_files()
    assert files, "no rollouts found"
    for f in files:
        result, off = a.ingest_file(f, 0)
        assert off >= 0
        for t in result.turns:
            assert t.fresh_input_tokens >= 0 and t.output_tokens >= 0 and t.cache_read_tokens >= 0
        assert not result.parse_errors, f"{f.name}: {[e.reason for e in result.parse_errors]}"
        for sub in a.sub_session_files_for(f):
            sres, _ = a.ingest_file(sub, 0)
            assert not sres.parse_errors, f"{sub.name}: {[e.reason for e in sres.parse_errors]}"
