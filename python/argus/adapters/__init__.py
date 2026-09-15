"""Adapters — one subpackage per coding-agent data source.

Importing this module auto-imports every known adapter so their
``@register`` decorators run and the registry is populated.
"""
# Side-effect imports — each adapter's package self-registers via @register.
from . import claude_code  # noqa: F401
from . import codex  # noqa: F401
