"""Bundled pricing-table load."""
from __future__ import annotations

import json

from argus.pricing.load import _latest_table_file, load_pricing_table


def test_loads_bundled_pricing_json():
    t = load_pricing_table()
    assert t.version == "2026-09-22"
    assert t.models["claude-opus-4-7"].input == 5
    assert t.models["gpt-5.3-codex"].output == 14


def test_unknown_model_lookup_returns_none():
    t = load_pricing_table()
    assert "fake-model" not in t.models


def test_bundled_table_prices_fable_and_opus_48():
    t = load_pricing_table()
    fable = t.models["claude-fable-5"]
    assert (fable.input, fable.output) == (10, 50)
    assert fable.cache_write_5m == 12.5
    assert fable.cache_write_1h == 20
    assert fable.cache_read == 1.0
    opus = t.models["claude-opus-4-8"]
    assert (opus.input, opus.output) == (5, 25)
    assert opus.cache_read == 0.50
    # Mythos 5 is the same model under a different id — same prices.
    assert t.models["claude-mythos-5"].input == 10


def test_latest_table_file_picks_newest_version(tmp_path):
    """`argus pricing refresh` writes {version}.json — the loader must pick
    the newest file, not a hardcoded name, or refreshes never take effect."""
    for name in ("2026-05-02.json", "2026-07-01.json", "2026-06-12.json"):
        (tmp_path / name).write_text(
            json.dumps({"version": name.removesuffix(".json"), "models": {}}),
            encoding="utf-8",
        )
    assert _latest_table_file(tmp_path).name == "2026-07-01.json"


def test_bundled_table_prices_current_claude_models():
    """REGRESSION (H3): claude-opus-5 / -5-5 / sonnet-5 / fable-5-1 were missing,
    so compute_turn_cost returned $0 for every turn on them (645 opus-5 turns
    on the maintainer's archive). Values per Anthropic's published per-MTok
    pricing (standard 1.25x / 2x cache-write multipliers)."""
    m = load_pricing_table().models
    expect = {
        "claude-opus-5": (5, 25, 6.25, 10, 0.50),
        "claude-opus-5-5": (4, 20, 5, 8, 0.20),
        "claude-sonnet-5": (2, 10, 2.5, 4, 0.20),
        "claude-fable-5-1": (10, 50, 12.5, 20, 0.25),
    }
    for model, (i, o, w5, w1, r) in expect.items():
        p = m[model]
        assert (p.input, p.output, p.cache_write_5m, p.cache_write_1h, p.cache_read) == (
            i, o, w5, w1, r
        ), model


def _table(d, version, models=None):
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{version}.json").write_text(
        json.dumps({"version": version, "models": models or {}}), encoding="utf-8"
    )


def test_user_dir_table_wins_when_newer(tmp_path):
    """REGRESSION (H3): `argus pricing refresh` writes to the user's data dir;
    the loader must pick the newest version across user + bundled tables."""
    _table(tmp_path / "pricing", "2099-01-01", {"x": {"input": 1, "output": 1, "cache_read": 0}})
    t = load_pricing_table(user_dir=tmp_path / "pricing")
    assert t.version == "2099-01-01"


def test_bundled_table_wins_when_user_table_is_older(tmp_path):
    """A refresh from last year must not shadow a newer table shipped by an upgrade."""
    _table(tmp_path / "pricing", "2020-01-01")
    t = load_pricing_table(user_dir=tmp_path / "pricing")
    assert t.version == load_pricing_table().version


def test_missing_user_dir_falls_back_to_bundled(tmp_path):
    assert load_pricing_table(user_dir=tmp_path / "nope").version == load_pricing_table().version
