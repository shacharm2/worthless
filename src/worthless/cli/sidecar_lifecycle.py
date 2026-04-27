"""Sidecar lifecycle helpers (WOR-384 Phases A + B + C).

Phase A: ``split_to_tmpfs`` plus the ``ShareFiles`` dataclass.
Phase B: ``spawn_sidecar`` plus the ``SidecarHandle`` dataclass — launches
``python -m worthless.sidecar`` with the env contract from
``src/worthless/sidecar/__main__.py`` and waits for ready.
Phase C: ``shutdown_sidecar`` — symmetric teardown that signals, unlinks
on-disk artifacts, and zeros share bytearrays (SR-02).
Phase D (``worthless up`` wiring + WRTLS-112 SIDECAR_CRASHED) and Phase E
(refactor + docs) follow on the same branch.

Security rules touched here:
- SR-01: shard buffers are ``bytearray`` (mutable, zeroable).
- SR-02: ``shutdown_sidecar`` zeros shard buffers via ``zero_buf``.
- SR-04: never log share bytes; only log the run-dir path.
"""

from __future__ import annotations

import logging
import os
import subprocess  # nosec B404 — required for sidecar subprocess lifecycle
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from worthless.cli.errors import ErrorCode, WorthlessError
from worthless.crypto.splitter import split_key
from worthless.crypto.types import zero_buf

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

    result = split_key(fernet_key)
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

    ``drain_timeout`` is the value passed to ``spawn_sidecar`` and forwarded
    to the sidecar via ``WORTHLESS_SIDECAR_DRAIN_TIMEOUT``. Phase C reads it
    back to size the SIGTERM grace window so shutdown matches the drain
    budget the sidecar was actually configured with.
    """

    proc: subprocess.Popen[bytes]
    socket_path: Path
    shares: ShareFiles
    allowed_uid: int
    drain_timeout: float


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
    proc = subprocess.Popen(  # noqa: S603  # nosec B603 — args are static, no shell
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
        drain_timeout=drain_timeout,
    )


# ---------------------------------------------------------------------------
# Phase C: shutdown_sidecar — graceful teardown + SR-02 zeroing
# ---------------------------------------------------------------------------


# SIGTERM grace = the sidecar's actual drain budget (read from the handle so
# a non-default ``drain_timeout`` to ``spawn_sidecar`` survives to teardown).
# SIGKILL follow-up only needs long enough for the kernel to reap.
_SHUTDOWN_KILL_GRACE_S = 2.0


def shutdown_sidecar(handle: SidecarHandle) -> None:
    """Terminate the sidecar and clean up its on-disk state.

    Steps, in order:

    1. SIGTERM the process; wait up to ``handle.drain_timeout`` seconds for
       a graceful exit (matches the ``WORTHLESS_SIDECAR_DRAIN_TIMEOUT`` value
       Phase B forwarded to the sidecar — so a custom drain budget survives
       to teardown).
    2. SIGKILL if still alive after the grace window; wait
       ``_SHUTDOWN_KILL_GRACE_S`` for the kernel to reap.
    3. Best-effort unlink of ``share_a.bin``, ``share_b.bin``, and the
       sidecar socket inode.
    4. Best-effort ``rmdir`` of the per-pid run dir.
    5. Zero the ``shard_a`` and ``shard_b`` bytearrays in memory (SR-02).

    Idempotent: every step tolerates the "already done" state — a second
    call after a successful first call MUST NOT raise.

    Args:
        handle: The :class:`SidecarHandle` returned by :func:`spawn_sidecar`.
    """
    proc = handle.proc

    # 1+2. Stop the process. Guard each step on liveness so the second
    # invocation (idempotency) skips signaling a corpse.
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=handle.drain_timeout)
        except subprocess.TimeoutExpired:
            # Drain budget exhausted — escalate.
            proc.kill()
            try:
                proc.wait(timeout=_SHUTDOWN_KILL_GRACE_S)
            except subprocess.TimeoutExpired:
                # Kernel didn't reap in time; nothing more we can do here.
                # Phase D's polling loop will surface the runaway via WRTLS-112.
                pass

    # 3. Unlink on-disk artifacts. ``missing_ok=True`` handles the second
    # call gracefully; a stray OSError (e.g., EBUSY) is logged-and-swallowed
    # so the in-memory zeroing in step 5 still runs.
    for path in (
        handle.shares.share_a_path,
        handle.shares.share_b_path,
        handle.socket_path,
    ):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # User state still on disk after teardown — visibility matters.
            _logger.warning("shutdown_sidecar: could not unlink %s", path)

    # 4. Remove the per-pid run dir. ``rmdir`` only succeeds when empty,
    # which is the expected state after step 3.
    try:
        handle.shares.run_dir.rmdir()
    except FileNotFoundError:
        # Idempotent re-call — already cleaned up. Silent.
        pass
    except OSError:
        # Not empty (something dropped a file in there) → user state on disk.
        _logger.warning(
            "shutdown_sidecar: could not rmdir %s (run dir not empty)",
            handle.shares.run_dir,
        )

    # 5. SR-02: zero the in-memory shard buffers. ``zero_buf`` is idempotent
    # (writing zeros over already-zero bytes is a no-op).
    zero_buf(handle.shares.shard_a)
    zero_buf(handle.shares.shard_b)
