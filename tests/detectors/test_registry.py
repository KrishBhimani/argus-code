"""Detector registry mechanism — @register decorator and available_detectors()."""
from __future__ import annotations

import pytest

from argus.detectors import registry
from argus.detectors.base import Finding


class _FakeDetector:
    name = "fake"

    def detect(self, repo, now_iso: str) -> list[Finding]:
        return []


@pytest.fixture(autouse=True)
def _isolate_registry():
    snapshot = dict(registry._REGISTRY)
    yield
    registry._REGISTRY.clear()
    registry._REGISTRY.update(snapshot)


def test_register_adds_class_to_registry():
    @registry.register
    class A(_FakeDetector):
        name = "test-a"

    assert "test-a" in registry.registered_detector_names()


def test_available_detectors_instantiates_classes():
    @registry.register
    class A(_FakeDetector):
        name = "a"

    instances = registry.available_detectors()
    assert any(d.name == "a" for d in instances)


def test_register_returns_class_unchanged():
    class Original(_FakeDetector):
        name = "rtn"

    returned = registry.register(Original)
    assert returned is Original


@pytest.mark.parametrize(
    "name", ["tool_error_rate_spike", "cost_spike", "cache_hit_drop"]
)
def test_shipped_detectors_are_auto_registered(name):
    """Importing argus.detectors triggers every detector's registration."""
    import argus.detectors  # noqa: F401

    assert name in registry.registered_detector_names()
