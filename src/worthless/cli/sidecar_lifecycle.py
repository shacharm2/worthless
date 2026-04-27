"""Sidecar lifecycle helpers (WOR-384 Phases A + B).

Phase A: ``split_to_tmpfs`` plus the ``ShareFiles`` dataclass.
Phase B: ``spawn_sidecar`` plus the ``SidecarHandle`` dataclass — launches
``python -m worthless.sidecar`` with the env contract from
``src/worthless/sidecar/__main__.py`` and waits for ready.
Phases C (shutdown/zeroing), D (up integration), and E land in follow-up
tickets — keep this module narrow.

Security rules touched here:
- SR-01: shard buffers are ``bytearray`` (mutable, zeroable).
- SR-04: never log share bytes; only log the run-dir path.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from worthless.cli.errors import ErrorCode, WorthlessError
from worthless.crypto.splitter import split_key

_logger = logging.getLogger("worthless.cli.sidecar_lifecycle")

# On-disk layout under the Worthless home dir. Phases B (spawn) and C
# (shutdown) import these so the layout has a single source of truth.
_RUN_SUBDIR = "run"
_SHARE_A_NAME = "share_a.bin"
_SHARE_B_NAME = "share_b.bin"


@dataclass
class ShareFiles:
    """Handle returned by :func:`split_to_tmpfs`.

    The bytearrays remain in memory so Phase C (shutdown) can zero them
    after the sidecar terminates. Callers MUST NOT mutate ``shard_a`` or
    ``shard_b`` before shutdown — they back the live shares on disk and
    are also the buffers Phase C will overwrite with zeros.
    """

    share_a_path: Path
    share_b_path: Path
    shard_a: bytearray
    shard_b: bytearray
    run_dir: Path


def _write_share(path: Path, data: bytearray) -> None:
    """Atomically create *path* at mode 0o600 and write *data*.

    Uses ``O_EXCL`` so the file never exists at a wider mode, even
    transiently. Belt-and-braces ``fchmod`` defends against a permissive
    process umask masking the ``0o600`` mode bits passed to ``os.open``.
    """
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(fd, 0o600)
        view = memoryview(data)
        written = 0
        while written < len(view):
            written += os.write(fd, view[written:])
    finally:
        os.close(fd)


def split_to_tmpfs(fernet_key: bytearray, home_dir: Path) -> ShareFiles:
    """Split *fernet_key* and write the two shares to ``home_dir/run/<pid>/``.

    The run directory is created at mode ``0o700``. Each share file is
    created atomically at mode ``0o600`` via ``os.O_EXCL`` so the bytes
    never exist on disk at a wider permission. Both files and the run
    dir are owned by the current uid.

    Args:
        fernet_key: The 44-byte fernet key, as a mutable bytearray.
        home_dir: The Worthless home dir (typically ``~/.worthless``).

    Returns:
        A :class:`ShareFiles` handle whose bytearrays the caller is
        expected to keep alive until Phase-C shutdown zeroes them.
    """
    run_dir = home_dir / _RUN_SUBDIR / str(os.getpid())
    run_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    # ``mkdir(mode=...)`` is umask-masked on POSIX; pin explicitly.
    run_dir.chmod(0o700)

    result = split_key(bytes(fernet_key))
    shard_a = result.shard_a
    shard_b = result.shard_b

    share_a_path = run_dir / _SHARE_A_NAME
    share_b_path = run_dir / _SHARE_B_NAME
    _write_share(share_a_path, shard_a)
    _write_share(share_b_path, shard_b)

    # SR-04: log only the run dir path, never share bytes.
    _logger.debug("split_to_tmpfs: wrote shares under run_dir=%s", run_dir)

    return ShareFiles(
        share_a_path=share_a_path,
        share_b_path=share_b_path,
        shard_a=shard_a,
        shard_b=shard_b,
        run_dir=run_dir,
    )


# ---------------------------------------------------------------------------
# Phase B: spawn_sidecar + SidecarHandle (WRTLS-113 SIDECAR_NOT_READY)
# ---------------------------------------------------------------------------


_READY_POLL_INTERVAL_S = 0.05


@dataclass
class SidecarHandle:
    """Live handle to a spawned sidecar subprocess.

    The caller owns the lifecycle: Phase D's ``up`` command is responsible
    for terminating the process and zeroing the share bytes (Phase C). This
    dataclass deliberately does not implement ``__enter__``/``__exit__`` —
    cleanup ordering matters and will be wired explicitly by Phase D.
    """

    proc: subprocess.Popen[bytes]
    socket_path: Path
    shares: ShareFiles
    allowed_uid: int


def _wait_for_ready(
    proc: subprocess.Popen[bytes],
    socket_path: Path,
    deadline: float,
) -> bool:
    """Poll until ``socket_path`` exists or the child exits.

    Socket inode is the canonical ready signal — same as
    ``tests/ipc/conftest.py::subprocess_sidecar``. We deliberately do NOT
    parse stdout: PIPE buffers ~64 KB of kernel data, and ``readline()``
    blocks until newline-or-EOF, which would deadlock if the sidecar ever
    grew chatty before binding.
    """
    while time.monotonic() < deadline:
        if socket_path.exists():
            return True
        if proc.poll() is not None:
            return False
        time.sleep(_READY_POLL_INTERVAL_S)
    return False


def spawn_sidecar(
    socket_path: Path,
    shares: ShareFiles,
    allowed_uid: int,
    *,
    ready_timeout: float = 5.0,
    drain_timeout: float = 5.0,
) -> SidecarHandle:
    """Spawn ``python -m worthless.sidecar`` and wait for it to be ready.

    Returns the handle once the sidecar's Unix socket exists. Raises
    :class:`WorthlessError` with :attr:`ErrorCode.SIDECAR_NOT_READY`
    (WRTLS-113) if the socket does not appear within *ready_timeout*.

    Args:
        socket_path: Path the sidecar will bind. Must NOT already exist.
        shares: Share files written by :func:`split_to_tmpfs`.
        allowed_uid: Numeric uid permitted to connect to the sidecar.
        ready_timeout: Seconds to wait for the socket / ready line.
        drain_timeout: Forwarded to the sidecar via
            ``WORTHLESS_SIDECAR_DRAIN_TIMEOUT``.

    Raises:
        WorthlessError: WRTLS-113 if the sidecar does not become ready.
    """
    env = {
        **os.environ,
        "WORTHLESS_SIDECAR_SOCKET": str(socket_path),
        "WORTHLESS_SIDECAR_SHARE_A": str(shares.share_a_path),
        "WORTHLESS_SIDECAR_SHARE_B": str(shares.share_b_path),
        "WORTHLESS_SIDECAR_ALLOWED_UID": str(allowed_uid),
        "WORTHLESS_SIDECAR_DRAIN_TIMEOUT": str(drain_timeout),
        "WORTHLESS_LOG_LEVEL": "WARNING",
    }
    proc = subprocess.Popen(  # noqa: S603 — args are static, no shell
        [sys.executable, "-m", "worthless.sidecar"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    deadline = time.monotonic() + ready_timeout
    ready = _wait_for_ready(proc, socket_path, deadline)
    if not ready:
        # Failure path: kill the child, drain stdout+stderr with a deadline
        # (one call, no SIGPIPE risk), unlink any half-formed socket inode.
        if proc.poll() is None:
            proc.kill()
        try:
            proc.communicate(timeout=2.0)
        except subprocess.TimeoutExpired:
            pass
        try:
            if socket_path.exists():
                socket_path.unlink()
        except OSError:
            pass
        raise WorthlessError(
            ErrorCode.SIDECAR_NOT_READY,
            f"sidecar did not become ready within {ready_timeout}s",
        )

    _logger.debug("spawn_sidecar: pid=%d socket=%s", proc.pid, socket_path)
    return SidecarHandle(
        proc=proc,
        socket_path=socket_path,
        shares=shares,
        allowed_uid=allowed_uid,
    )
