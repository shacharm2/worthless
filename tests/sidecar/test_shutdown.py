"""Sidecar startup / shutdown hardening tests (WOR-308 slices 1-2).

These spawn the sidecar as a real subprocess via ``python -m worthless.sidecar``
so we exercise the full env-config → bind → signal-shutdown path, not just
library internals. Marked ``integration`` because they fork processes.

See ``docs/ipc-contract.md`` for the env contract and exit codes.
"""

from __future__ import annotations

import base64
import os
import secrets
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_SUN_PATH_MAX = 104
_READY_TIMEOUT_S = 5.0


def _write_shares(dir_: Path) -> tuple[Path, Path]:
    """Write two 44-byte XOR shares that reconstruct a valid Fernet key."""
    key = base64.urlsafe_b64encode(secrets.token_bytes(32))
    share_a = secrets.token_bytes(len(key))
    share_b = bytes(a ^ k for a, k in zip(share_a, key, strict=True))
    a_path = dir_ / "share_a"
    b_path = dir_ / "share_b"
    a_path.write_bytes(share_a)
    b_path.write_bytes(share_b)
    a_path.chmod(0o600)
    b_path.chmod(0o600)
    return a_path, b_path


@pytest.fixture
def sidecar_env() -> Iterator[tuple[Path, dict[str, str]]]:
    """Yield (socket_path, env) for spawning ``python -m worthless.sidecar``.

    Uses ``/tmp/w-*`` directly (not pytest's tmp_path) to stay inside the
    104-byte AF_UNIX ``sun_path`` limit on macOS, same rationale as
    ``tests/ipc/conftest.py::sidecar_socket_path``.
    """
    base = Path(tempfile.mkdtemp(prefix="w-", dir="/tmp"))
    sock = base / "s.sock"
    if len(str(sock)) >= _SUN_PATH_MAX:
        pytest.skip(f"tmp path too long for AF_UNIX: {sock}")
    a_path, b_path = _write_shares(base)
    env = {
        **os.environ,
        "WORTHLESS_SIDECAR_SOCKET": str(sock),
        "WORTHLESS_SIDECAR_SHARE_A": str(a_path),
        "WORTHLESS_SIDECAR_SHARE_B": str(b_path),
        "WORTHLESS_SIDECAR_ALLOWED_UID": str(os.getuid()),
    }
    try:
        yield sock, env
    finally:
        for p in (sock, a_path, b_path):
            try:
                p.unlink()
            except OSError:
                pass
        try:
            base.rmdir()
        except OSError:
            pass


def _spawn_sidecar(env: dict[str, str]) -> subprocess.Popen[str]:
    """Launch ``python -m worthless.sidecar`` with line-buffered text IO."""
    return subprocess.Popen(
        [sys.executable, "-m", "worthless.sidecar"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )


def _wait_for_ready(proc: subprocess.Popen[str], timeout: float = _READY_TIMEOUT_S) -> str:
    """Block until the sidecar prints its ``sidecar: ready …`` line or dies.

    Returns the ready line on success. On timeout or early exit, terminates
    the process and fails the test with the captured stderr so the failure
    mode is legible.
    """
    deadline = time.monotonic() + timeout
    assert proc.stdout is not None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            err = proc.stderr.read() if proc.stderr else ""
            pytest.fail(
                f"sidecar exited before ready (rc={proc.returncode})\n"
                f"stdout:\n{out}\nstderr:\n{err}"
            )
        line = proc.stdout.readline()
        if line.startswith("sidecar: ready"):
            return line
    proc.kill()
    proc.wait(timeout=2)
    pytest.fail(f"sidecar did not print ready line within {timeout}s")


def _terminate(proc: subprocess.Popen[str]) -> tuple[int, str, str]:
    """SIGTERM the process, wait, return (rc, stdout, stderr)."""
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
    try:
        out, err = proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate(timeout=2)
    return proc.returncode, out, err


def test_startup_removes_stale_socket_file_after_sigkill(
    sidecar_env: tuple[Path, dict[str, str]],
) -> None:
    """A sidecar killed mid-flight leaves a socket inode; the next start recovers.

    Scenario: operator restarts the container, but the prior process died from
    SIGKILL (OOM, docker stop --signal=KILL, kernel panic). The socket inode
    remains on the volume. A fresh bind would fail with EADDRINUSE. The
    hardened startup path probes the socket; since no server is listening
    (connect → ECONNREFUSED), it unlinks and rebinds.
    """
    sock, env = sidecar_env

    first = _spawn_sidecar(env)
    try:
        _wait_for_ready(first)
        assert sock.exists(), "socket inode should exist after ready line"
        first.send_signal(signal.SIGKILL)
        first.wait(timeout=5)
    finally:
        if first.poll() is None:
            first.kill()
            first.wait(timeout=2)

    assert sock.exists(), "SIGKILL must leave stale socket inode (preconditions)"

    second = _spawn_sidecar(env)
    try:
        _wait_for_ready(second)
        assert sock.exists()
        rc, out, err = _terminate(second)
        assert rc == 0, f"second sidecar should shut down cleanly.\nstdout:{out}\nstderr:{err}"
    finally:
        if second.poll() is None:
            second.kill()
            second.wait(timeout=2)


def test_startup_refuses_bind_when_live_sidecar_present(
    sidecar_env: tuple[Path, dict[str, str]],
) -> None:
    """Two simultaneous sidecars on the same socket: the second must fail fast.

    Contract: exit code ``2`` (bind failure) and a human-readable
    ``already running`` hint on stderr so supervisors can disambiguate a
    genuine port-clash from a config error.
    """
    sock, env = sidecar_env

    first = _spawn_sidecar(env)
    try:
        _wait_for_ready(first)
        second = _spawn_sidecar(env)
        try:
            rc = second.wait(timeout=5)
            out = second.stdout.read() if second.stdout else ""
            err = second.stderr.read() if second.stderr else ""
        finally:
            if second.poll() is None:
                second.kill()
                second.wait(timeout=2)

        assert rc == 2, f"expected rc=2 for duplicate bind, got {rc}\nstdout:{out}\nstderr:{err}"
        assert "already running" in err.lower(), (
            f"stderr should mention 'already running'.\nstderr:{err}"
        )
    finally:
        if first.poll() is None:
            first.send_signal(signal.SIGTERM)
            try:
                first.wait(timeout=5)
            except subprocess.TimeoutExpired:
                first.kill()
                first.wait(timeout=2)
