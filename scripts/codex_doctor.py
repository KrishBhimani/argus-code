"""Diagnose why Codex sessions are (not) showing up in Argus.

Prints structure and counts only -- never prompt text, tool arguments, paths
inside rollouts, or model output -- so the result is safe to paste into an issue.

    uv run python scripts/codex_doctor.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from argus.adapters.codex.adapter import CodexAdapter  # noqa: E402
from argus.adapters.codex.discover import codex_root  # noqa: E402
from argus.adapters.codex.lines import open_rollout, zstd_available  # noqa: E402

INTERESTING = ("session_meta", "turn_context", "event_msg/token_count", "event_msg/user_message",
               "event_msg/item_completed", "event_msg/thread_settings_applied", "event_msg/task_started",
               "response_item/message", "response_item/function_call", "response_item/function_call_output",
               "response_item/custom_tool_call", "response_item/local_shell_call", "response_item/reasoning")


def _scan(path: Path) -> tuple[Counter, int, list[str], dict]:
    """Count record kinds; capture key names of the first token_count (keys only)."""
    kinds: Counter = Counter()
    bad = 0
    ordinals = 0
    tc_shape: dict = {}
    with open_rollout(path) as fh:
        if fh is None:
            return kinds, -1, ["no zstd decoder"], tc_shape
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except Exception:
                bad += 1
                continue
            if not isinstance(obj, dict):
                bad += 1
                continue
            if "ordinal" in obj:
                ordinals += 1
            t = obj.get("type")
            p = obj.get("payload")
            if t is None:
                kinds["legacy:" + ("header" if "id" in obj and "timestamp" in obj else "untyped")] += 1
                continue
            if not isinstance(p, dict):
                kinds[f"{t} (bare item)"] += 1
                continue
            pt = p.get("type")
            key = f"{t}/{pt}" if pt else t
            kinds[key] += 1
            if key == "event_msg/token_count" and not tc_shape:
                info = p.get("info")
                tc_shape = {
                    "payload_keys": sorted(p.keys()),
                    "info_keys": sorted(info.keys()) if isinstance(info, dict) else type(info).__name__,
                    "last_keys": sorted(info["last_token_usage"].keys()) if isinstance(info, dict) and isinstance(info.get("last_token_usage"), dict) else None,
                    "total_keys": sorted(info["total_token_usage"].keys()) if isinstance(info, dict) and isinstance(info.get("total_token_usage"), dict) else None,
                }
    return kinds, ordinals, ([f"{bad} unparseable lines"] if bad else []), tc_shape


def main() -> int:
    root = codex_root()
    print(f"python {sys.version.split()[0]}  sqlite {sqlite3.sqlite_version}  zstd decoder: {zstd_available()}")
    print(f"codex root: {root}")
    print(f"  sessions/ exists: {(root / 'sessions').is_dir()}   archived_sessions/ exists: {(root / 'archived_sessions').is_dir()}")
    hist = root / "history.jsonl"
    if hist.exists():
        with hist.open("rb") as fh:
            n = sum(1 for line in fh if line.strip())
        print(f"  history.jsonl: {n} lines")
    else:
        print("  history.jsonl: missing")

    adapter = CodexAdapter(root)
    print(f"adapter present: {adapter.is_present()}")
    if not adapter.is_present():
        print("-> Argus will not load the Codex adapter. Set CODEX_HOME if Codex lives elsewhere.")
        return 1

    roots = adapter.discover_session_files()
    all_files = sorted(p for d in ("sessions", "archived_sessions") if (root / d).is_dir() for p in (root / d).rglob("rollout-*.jsonl*"))
    print(f"rollouts on disk: {len(all_files)}   top-level threads: {len(roots)}   (children: {len(all_files) - len(roots)})")
    print()

    totals = Counter()
    for f in all_files:
        meta = adapter._index.meta_for(f)  # noqa: SLF001 (diagnostic)
        kinds, ordinals, problems, tc_shape = _scan(f)
        result, off = adapter.ingest_file(f, 0)
        turns = len(result.turns)
        tokens = sum(t.fresh_input_tokens + t.cache_read_tokens + t.output_tokens for t in result.turns)
        models = sorted({t.model for t in result.turns})
        totals["files"] += 1
        totals["turns"] += turns
        totals["tool_calls"] += len(result.tool_calls)
        totals["parse_errors"] += len(result.parse_errors)
        fmt = meta.format if meta else None
        child = bool(meta and meta.parent_thread_id)
        print(f"== {f.name[:19]}…{f.suffix if f.suffix != '.jsonl' else ''}  size={f.stat().st_size}  format={fmt}  child={child}  ordinals={ordinals}")
        shown = {k: v for k, v in kinds.items() if k in INTERESTING or k.startswith("legacy:")}
        other = sum(v for k, v in kinds.items() if k not in shown)
        print(f"   records: {dict(sorted(shown.items()))}" + (f"  +{other} other" if other else ""))
        print(f"   argus: turns={turns} tool_calls={len(result.tool_calls)} segments={len(result.segments)} tokens={tokens} models={models} consumed={off}/{f.stat().st_size}")
        if result.parse_errors:
            print(f"   parse_errors: {[e.reason[:80] for e in result.parse_errors[:3]]}")
        for p in problems:
            print(f"   problem: {p}")
        if kinds.get("event_msg/token_count", 0) and turns == 0:
            print(f"   !! token_count present but no turns extracted -- shape: {tc_shape}")
        if not kinds.get("event_msg/token_count") and fmt == "envelope":
            print("   (no token_count events: no model response recorded, so no session in Argus)")
    print()
    print("totals:", dict(totals))
    if totals["turns"] == 0:
        print("-> No Codex file on this machine contains a model response with usage; Argus shows no Codex sessions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
