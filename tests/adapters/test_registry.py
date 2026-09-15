"""Adapter registry mechanism — @register decorator and available_adapters()."""
from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from argus.adapters import registry
from argus.adapters.base import Adapter, AdapterIngestResult


class _FakeAdapter:
    """Minimal Adapter implementation for registry tests."""

    agent = "fake"
    _present = True

    def root_path(self) -> Path:
        return Path("/tmp/fake")

    def is_present(self) -> bool:
        return self._present

    def discover_session_files(self) -> list[Path]:
        return []

    def ingest_file(self, path, from_offset=0):
        raise NotImplementedError


@pytest.fixture(autouse=True)
def _isolate_registry(monkeypatch):
    """Each test runs against a fresh _REGISTRY snapshot so we don't leak fakes."""
    snapshot = dict(registry._REGISTRY)
    yield
    registry._REGISTRY.clear()
    registry._REGISTRY.update(snapshot)


def test_register_adds_class_to_registry():
    @registry.register
    class A(_FakeAdapter):
        agent = "test-a"

    assert "test-a" in registry.registered_adapter_names()


def test_available_adapters_filters_by_is_present():
    @registry.register
    class Present(_FakeAdapter):
        agent = "present"
        _present = True

    @registry.register
    class Absent(_FakeAdapter):
        agent = "absent"
        _present = False

    names = [a.agent for a in registry.available_adapters()]
    assert "present" in names
    assert "absent" not in names


def test_register_returns_the_class_unchanged():
    """The decorator must be transparent — used as @register before class body."""

    class Original(_FakeAdapter):
        agent = "rtn"

    returned = registry.register(Original)
    assert returned is Original


def test_claude_code_is_auto_registered():
    """Importing argus.adapters triggers claude_code self-registration."""
    import argus.adapters  # noqa: F401

    assert "claude_code" in registry.registered_adapter_names()


def test_codex_is_auto_registered():
    import argus.adapters  # noqa: F401

    assert "codex" in registry.registered_adapter_names()


def test_native_session_id_defaults_to_stem():
    """Protocol default: the native id is the file stem (Claude Code's layout)."""

    class Stemmed(_FakeAdapter):
        agent = "stemmed"

    assert Adapter.native_session_id(cast(Adapter, Stemmed()), Path("/x/abc-123.jsonl")) == "abc-123"
