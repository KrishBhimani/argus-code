"""Per-turn cost computation."""
from __future__ import annotations

from argus.pricing.compute import compute_turn_cost
from argus.pricing.types import ModelPricing, PricingTable


def _table() -> PricingTable:
    return PricingTable(
        version="2026-05-02",
        models={
            "claude-opus-4-7": ModelPricing(
                input=5, output=25, cache_write_5m=6.25, cache_write_1h=10, cache_read=0.50
            ),
            "gpt-5.3-codex": ModelPricing(
                input=1.75, output=14, cache_read=0.175
            ),
        },
    )


def test_anthropic_with_5m_and_1h_cache_writes():
    cost = compute_turn_cost(
        {
            "model": "claude-opus-4-7",
            "fresh_input_tokens": 1_000_000,
            "output_tokens": 1_000_000,
            "cache_read_tokens": 1_000_000,
            "cache_write_tokens": 1_000_000,
            "cache_write_5m_tokens": 700_000,
            "cache_write_1h_tokens": 300_000,
        },
        _table(),
    )
    expected = 5 + 25 + 0.50 + (0.7 * 6.25) + (0.3 * 10)
    assert abs(cost - expected) < 1e-4


def test_anthropic_without_tier_breakdown_uses_5m_rate():
    cost = compute_turn_cost(
        {
            "model": "claude-opus-4-7",
            "fresh_input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 1_000_000,
            "cache_write_5m_tokens": None,
            "cache_write_1h_tokens": None,
        },
        _table(),
    )
    assert abs(cost - 6.25) < 1e-4


def test_openai_with_cached_input_only():
    cost = compute_turn_cost(
        {
            "model": "gpt-5.3-codex",
            "fresh_input_tokens": 1_000_000,
            "output_tokens": 1_000_000,
            "cache_read_tokens": 500_000,
            "cache_write_tokens": 0,
            "cache_write_5m_tokens": None,
            "cache_write_1h_tokens": None,
        },
        _table(),
    )
    assert abs(cost - (1.75 + 14 + 0.5 * 0.175)) < 1e-4


def test_returns_zero_for_unknown_model():
    cost = compute_turn_cost(
        {
            "model": "fake",
            "fresh_input_tokens": 1,
            "output_tokens": 1,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "cache_write_5m_tokens": None,
            "cache_write_1h_tokens": None,
        },
        _table(),
    )
    assert cost == 0


def test_unknown_model_is_logged_once(caplog):
    """REGRESSION (H3): an unpriced model silently cost $0; now it is logged,
    once per model so a long session doesn't flood the log."""
    import logging

    from argus.pricing import compute
    from argus.pricing.compute import compute_turn_cost
    from argus.pricing.types import PricingTable

    compute._warned_unknown.discard("claude-imaginary-9")
    table = PricingTable(version="t", models={})
    turn = {"model": "claude-imaginary-9", "fresh_input_tokens": 10, "output_tokens": 5}
    with caplog.at_level(logging.WARNING, logger="argus.pricing"):
        assert compute_turn_cost(turn, table) == 0.0
        assert compute_turn_cost(turn, table) == 0.0
    msgs = [r.getMessage() for r in caplog.records if "claude-imaginary-9" in r.getMessage()]
    assert len(msgs) == 1
