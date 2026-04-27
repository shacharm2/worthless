"""Tests for ``worthless.cli.sidecar_lifecycle`` — WOR-384 Phase A."""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

import pytest

from worthless.cli.sidecar_lifecycle import ShareFiles, split_to_tmpfs


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """A fresh Worthless home dir under the test's tmp_path.

    Per-test rather than session-scoped so xdist parallel workers don't
    collide on the per-pid run subdir (each test creates a *fresh* home,
    so the same pid writing into two homes is fine).
    """
    h = tmp_path / ".worthless"
    h.mkdir()
    return h


@pytest.fixture
def key() -> bytearray:
    """A 44-byte placeholder fernet key. Uniform bytes are fine for tests
    that don't care about the XOR roundtrip — the dedicated XOR test uses
    non-uniform bytes to prove the split isn't degenerate."""
    return bytearray(b"A" * 44)


def test_split_to_tmpfs_creates_two_shares(home: Path, key: bytearray) -> None:
    shares = split_to_tmpfs(key, home)
    assert shares.share_a_path.exists()
    assert shares.share_b_path.exists()


def test_split_to_tmpfs_xor_yields_original_key(home: Path) -> None:
    # Non-uniform bytes so XOR roundtrip isn't degenerate over a single
    # repeated byte (which would pass even if shard_b were a constant).
    key = bytearray(b"fernet-key-44-bytes-urlsafe-base64-here-padd")
    assert len(key) == 44
    shares = split_to_tmpfs(key, home)
    a = shares.share_a_path.read_bytes()
    b = shares.share_b_path.read_bytes()
    assert len(a) == len(key)
    assert len(b) == len(key)
    reconstructed = bytes(x ^ y for x, y in zip(a, b, strict=True))
    assert reconstructed == bytes(key)


def test_share_files_have_0600_perms_and_owner_uid(home: Path, key: bytearray) -> None:
    shares = split_to_tmpfs(key, home)
    for p in (shares.share_a_path, shares.share_b_path):
        st = p.stat()
        assert stat.S_IMODE(st.st_mode) == 0o600, f"{p} mode={oct(st.st_mode)}"
        assert st.st_uid == os.getuid()


def test_share_dir_is_per_pid_under_home(home: Path, key: bytearray) -> None:
    shares = split_to_tmpfs(key, home)
    expected = home / "run" / str(os.getpid())
    assert shares.run_dir == expected
    assert shares.run_dir.exists()
    assert stat.S_IMODE(shares.run_dir.stat().st_mode) == 0o700


def test_split_to_tmpfs_does_not_log_share_bytes(
    home: Path, key: bytearray, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="worthless")
    shares: ShareFiles = split_to_tmpfs(key, home)
    a_hex = shares.share_a_path.read_bytes().hex()
    b_hex = shares.share_b_path.read_bytes().hex()
    for record in caplog.records:
        msg = record.getMessage()
        assert a_hex not in msg, f"share_a hex leaked: {msg!r}"
        assert b_hex not in msg, f"share_b hex leaked: {msg!r}"
        for arg in record.args or ():
            arg_str = repr(arg)
            assert a_hex not in arg_str
            assert b_hex not in arg_str
