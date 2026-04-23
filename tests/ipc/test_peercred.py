"""Tests for ``worthless.ipc.peercred`` — peer-uid authentication.

Contract: docs/ipc-contract.md §Transport (auth).

Cross-platform: exercises SO_PEERCRED on Linux, getpeereid() via ctypes
on macOS. Windows is unsupported (no AF_UNIX in the way we need).
"""

from __future__ import annotations

import os
import socket
import sys

import pytest

from worthless.ipc.peercred import (
    PeerCredentials,
    PeerCredError,
    UnauthorizedPeerError,
    UnsupportedPlatformError,
    get_peer_credentials,
    require_peer_uid,
)

# All tests in this module need AF_UNIX.
pytestmark = pytest.mark.skipif(
    sys.platform not in ("linux", "darwin"),
    reason="peer-uid auth only supported on Linux and macOS",
)


@pytest.fixture
def sock_pair():
    """Yield a connected AF_UNIX stream socket pair. Both ends run as us."""
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        yield a, b
    finally:
        a.close()
        b.close()


class TestGetPeerCredentials:
    def test_returns_current_uid_and_gid(self, sock_pair) -> None:
        a, _b = sock_pair
        creds = get_peer_credentials(a)
        assert isinstance(creds, PeerCredentials)
        assert creds.uid == os.getuid()
        assert creds.gid == os.getgid()

    def test_pid_populated_on_linux(self, sock_pair) -> None:
        if sys.platform != "linux":
            pytest.skip("Linux-specific — pid is None on macOS")
        a, _b = sock_pair
        creds = get_peer_credentials(a)
        # Socket pair: both ends are this process.
        assert creds.pid == os.getpid()

    def test_pid_is_none_on_macos(self, sock_pair) -> None:
        if sys.platform != "darwin":
            pytest.skip("macOS-specific — pid is populated on Linux")
        a, _b = sock_pair
        creds = get_peer_credentials(a)
        # getpeereid returns only uid+gid; pid requires a separate syscall
        # we're not making in v1.1. Logged as None.
        assert creds.pid is None

    def test_raises_on_non_unix_socket(self) -> None:
        # TCP socket → not a Unix socket → no peer creds meaningful.
        tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(PeerCredError):
                get_peer_credentials(tcp)
        finally:
            tcp.close()


class TestRequirePeerUid:
    def test_allowed_uid_returns_credentials(self, sock_pair) -> None:
        a, _b = sock_pair
        creds = require_peer_uid(a, allowed_uids=[os.getuid()])
        assert creds.uid == os.getuid()

    def test_disallowed_uid_raises(self, sock_pair) -> None:
        a, _b = sock_pair
        # Pick a uid we are definitely NOT running as.
        bogus = os.getuid() + 99999
        with pytest.raises(UnauthorizedPeerError) as exc_info:
            require_peer_uid(a, allowed_uids=[bogus])
        # Error message should include observed uid for debugging.
        assert str(os.getuid()) in str(exc_info.value)

    def test_empty_allowlist_always_rejects(self, sock_pair) -> None:
        a, _b = sock_pair
        with pytest.raises(UnauthorizedPeerError):
            require_peer_uid(a, allowed_uids=[])

    def test_multi_uid_allowlist(self, sock_pair) -> None:
        """Two authorized uids (e.g. worthless-proxy + root for admin)."""
        a, _b = sock_pair
        creds = require_peer_uid(a, allowed_uids=[0, os.getuid()])
        assert creds.uid == os.getuid()


class TestPlatformDispatch:
    def test_supported_platforms_do_not_raise_import(self) -> None:
        # Just reaching this test means `from worthless.ipc.peercred import ...`
        # succeeded on this platform. Sanity check.
        assert sys.platform in ("linux", "darwin")

    def test_unsupported_platform_error_class_exists(self) -> None:
        # The error type exists even on supported platforms so callers can
        # handle it defensively.
        assert issubclass(UnsupportedPlatformError, PeerCredError)
