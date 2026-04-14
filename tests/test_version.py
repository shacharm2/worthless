"""Verify package version is 0.2.0."""

from importlib.metadata import version


def test_version_is_0_2_0():
    assert version("worthless") == "0.2.0"
