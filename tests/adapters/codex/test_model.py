from argus.adapters.codex.model import canonicalize_codex_model


def test_strips_provider_prefix_and_date_suffix():
    assert canonicalize_codex_model("openai/gpt-5.5") == "gpt-5.5"
    assert canonicalize_codex_model("gpt-5.3-codex-2026-03-01") == "gpt-5.3-codex"
    assert canonicalize_codex_model("gpt-5.5") == "gpt-5.5"
    assert canonicalize_codex_model("") == "unknown"
