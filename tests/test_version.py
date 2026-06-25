"""Verify package version matches project metadata."""

from importlib.metadata import version
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

PROJECT_VERSION = tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"]


def test_version_matches_pyproject():
    assert version("worthless") == PROJECT_VERSION
