"""Shared detector helpers — window offsets and project labels."""
from __future__ import annotations

import pytest

from argus.detectors.base import iso_at_offset, project_label


def test_iso_at_offset_keeps_the_stored_z_suffix():
    """Boundaries are compared as strings against DB timestamps."""
    assert iso_at_offset("2026-05-27T12:00:00Z", 7) == "2026-05-20T12:00:00Z"


def test_iso_at_offset_accepts_a_naive_timestamp():
    assert iso_at_offset("2026-05-27T12:00:00", 1) == "2026-05-26T12:00:00Z"


def test_iso_at_offset_handles_fractional_days():
    assert iso_at_offset("2026-05-27T12:00:00Z", 0.5) == "2026-05-27T00:00:00Z"


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/home/me/api", "api"),
        ("/home/me/api/", "api"),
        ("c:/users/me/api", "api"),
        (r"c:\users\me\api", "api"),
        ("api", "api"),
        ("/", "/"),
        ("", "(unknown project)"),
    ],
)
def test_project_label_is_the_last_path_segment(path, expected):
    assert project_label(path) == expected
