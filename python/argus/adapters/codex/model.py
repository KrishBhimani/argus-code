"""Codex model-name canonicalization."""
from __future__ import annotations

import re

_DATE_SUFFIX_RE = re.compile(r"-\d{4}-\d{2}-\d{2}$")


def canonicalize_codex_model(raw: str) -> str:
    """Map a Codex model string to the pricing-table key.

    Strips a provider prefix (``openai/gpt-5.5``) and a trailing
    ``-YYYY-MM-DD`` snapshot date. Empty input is ``unknown`` -- never a
    fabricated default, so unpriced turns stay visibly unpriced.
    """
    s = (raw or "").strip()
    if not s:
        return "unknown"
    if "/" in s:
        s = s.rsplit("/", 1)[1]
    return _DATE_SUFFIX_RE.sub("", s)
