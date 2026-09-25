"""Detectors — one module per finding type.

Importing this module auto-imports every known detector so their
``@register`` decorators run and the registry is populated.
"""
from . import cache_hit_drop  # noqa: F401
from . import cost_spike  # noqa: F401
from . import tool_error_rate_spike  # noqa: F401
