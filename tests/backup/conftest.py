"""Shared fixtures for the backup-module (WOR-276) test suite.

Mirrors ``tests/safe_rewrite/conftest.py`` style: fixtures build real
files/dirs and compute sha256 baselines without importing the (not-yet-
existing) module under test. Collection must succeed even when
``worthless.cli.backup`` does not exist — the per-test imports then fail
with the correct red signal.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Bucket-path contract (locked in plan §3)
#
# Shared across every file in the backup suite — keeping one canonical copy
# here prevents the rotation regex from drifting out of sync with the backup
# writer, which happened once already when a stale ``Z`` suffix snuck in.
# ---------------------------------------------------------------------------


def _bucket_for(repo_root: Path) -> str:
    """Expected bucket name = sha256 hex of the resolved repo-root path."""
    return hashlib.sha256(str(repo_root.resolve()).encode("utf-8")).hexdigest()


def _bucket_dir(xdg: Path, repo_root: Path) -> Path:
    """Resolve the expected on-disk bucket directory for ``repo_root``."""
    return xdg / "worthless" / "backups" / _bucket_for(repo_root)


_BACKUP_NAME_RE = re.compile(
    r"^(?P<base>[^/]+?)"
    r"\.(?P<yy>\d{4})-(?P<mm>\d{2})-(?P<dd>\d{2})"
    r"T(?P<hh>\d{2}):(?P<mi>\d{2}):(?P<ss>\d{2})"
    r"\.(?P<ns>\d{9})"
    r"\.(?P<pid>\d+)"
    r"\.(?P<counter>\d+)"
    r"\.bak$"
)


# ---------------------------------------------------------------------------
# Repo scaffolding
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_repo(tmp_path) -> Path:
    """A ``tmp_path``-rooted fake repo with a ``.git`` marker.

    Mirrors ``in_fake_repo`` in the safe_rewrite suite; renamed here
    because the backup module takes an explicit ``repo_root`` parameter
    and the call sites read more naturally as ``tmp_repo``.
    """
    (tmp_path / ".git").mkdir()
    return tmp_path


# ---------------------------------------------------------------------------
# XDG env fake
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_xdg(tmp_path, monkeypatch) -> Path:
    """Point ``$XDG_DATA_HOME`` at a scratch dir under ``tmp_path``.

    Returns the scratch dir. Tests that want to assert the empty / unset
    fallback override this by calling ``monkeypatch.delenv`` /
    ``monkeypatch.setenv(..., "")`` themselves.
    """
    xdg = tmp_path / "xdg-data-home"
    xdg.mkdir()
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
    # HOME is consulted for the ``~/.local/share`` fallback; pin it at a
    # scratch path so any accidental fallback can't write into the real
    # user's home during test.
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    return xdg


# ---------------------------------------------------------------------------
# Deterministic time_ns for filename-component assertions
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_time_ns(monkeypatch) -> Callable[[int], None]:
    """Pin ``time.time_ns`` to a given value (factory).

    Usage::

        fake_time_ns(1_700_000_000_123_456_789)

    Call twice in sequence to change the value over the course of a test.
    """
    import time as _time

    def _set(value: int) -> None:
        monkeypatch.setattr(_time, "time_ns", lambda: value)

    return _set


# ---------------------------------------------------------------------------
# File + digest helpers (mirror safe_rewrite conftest naming)
# ---------------------------------------------------------------------------


@pytest.fixture
def make_env_file() -> Callable[..., Path]:
    """Factory: create a file with given content and mode."""

    def _make(path: Path, content: bytes = b"KEY=value\n", mode: int = 0o600) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        try:
            os.write(fd, content)
        finally:
            os.close(fd)
        path.chmod(mode)
        return path

    return _make


@pytest.fixture
def sha256_of() -> Callable[[Path], str]:
    """Return a function that computes sha256 of a file's bytes."""

    def _sha(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    return _sha


# ---------------------------------------------------------------------------
# Cross-platform fd-to-path resolver for fsync spies
# ---------------------------------------------------------------------------


@pytest.fixture
def fd_to_path() -> Callable[[int], str]:
    """Resolve an open fd to its filesystem path (Linux + macOS).

    Linux: ``os.readlink("/proc/self/fd/{fd}")``.
    macOS: ``fcntl.fcntl(fd, fcntl.F_GETPATH)`` — returns a NUL-terminated
    path buffer. We strip the NUL tail.

    Fails loudly (``pytest.fail``) on unsupported platforms or on errors
    from the primitive — callers must not silently fall through to an
    opaque ``fd:N`` string, because tests 8/9 would then become
    tautologies that pass whether or not the implementation fsync'd the
    correct fd. See CR on PR #86 (discussion thread on fsync-spy
    brittleness).
    """
    import fcntl

    def _resolve(fd: int) -> str:
        if sys.platform.startswith("linux"):
            try:
                return str(Path(f"/proc/self/fd/{fd}").readlink())
            except OSError as exc:
                pytest.fail(f"/proc/self/fd/{fd} resolution failed on Linux: {exc}")
        if sys.platform == "darwin":
            f_getpath = getattr(fcntl, "F_GETPATH", None)
            if f_getpath is None:
                pytest.fail("fcntl.F_GETPATH missing on Darwin — cannot resolve fd")
            try:
                buf = fcntl.fcntl(fd, f_getpath, b"\x00" * 1024)
            except OSError as exc:
                pytest.fail(f"F_GETPATH failed for fd={fd}: {exc}")
            return buf.rstrip(b"\x00").decode("utf-8", errors="surrogateescape")
        pytest.fail(f"fd_to_path unsupported on platform {sys.platform!r}")
        raise AssertionError("unreachable")  # for type-checkers

    return _resolve


# ---------------------------------------------------------------------------
# Guard: backup module is POSIX-only (same policy as safe_rewrite)
# ---------------------------------------------------------------------------


def pytest_collection_modifyitems(config, items) -> None:  # noqa: ANN001, D401
    """Skip the backup suite on Windows — product is macOS + Linux only.

    This conftest is scoped to ``tests/backup/``, so every collected item
    here belongs to the backup suite; no path-substring filter is needed
    (the previous ``"tests/backup" in str(item.fspath)`` check silently
    skipped nothing on Windows because path separators are backslashes).
    """
    if sys.platform == "win32":
        skip = pytest.mark.skip(reason="backup suite is macOS + Linux only")
        for item in items:
            item.add_marker(skip)
