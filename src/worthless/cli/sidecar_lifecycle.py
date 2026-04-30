"""Sidecar lifecycle: split the Fernet key to per-PID share files, spawn the
sidecar subprocess with an env contract, and tear it all down (zeroing
share bytearrays) on any exit path.

Security rules: SR-01 (bytearray, not bytes), SR-02 (explicit zero_buf on
shard buffers at shutdown and on every failure path), SR-04 (never log
share bytes — only the run-dir path).
"""

from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess  # nosec B404 — required for sidecar subprocess lifecycle
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from worthless.cli.errors import ErrorCode, WorthlessError
from worthless.crypto.splitter import split_key
from worthless.crypto.types import zero_buf

_logger = logging.getLogger("worthless.cli.sidecar_lifecycle")

_RUN_SUBDIR = "run"
_SHARE_A_NAME = "share_a.bin"
_SHARE_B_NAME = "share_b.bin"


@dataclass
class ShareFiles:
    """Handle returned by :func:`split_to_tmpfs`.

    The bytearrays stay in memory so :func:`shutdown_sidecar` can zero them
    after the sidecar terminates. Callers MUST NOT mutate ``shard_a`` or
    ``shard_b`` before shutdown — they back the live shares on disk.
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
        A :class:`ShareFiles` handle whose bytearrays the caller must keep
        alive until :func:`shutdown_sidecar` zeroes them.
    """
    run_dir = home_dir / _RUN_SUBDIR / str(os.getpid())
    # Pin the parent dir to 0o700 too: ``parents=True`` lands at umask-masked
    # 0o755 on default systems. A world-traversable parent leaks live session
    # PIDs to any local user (ptrace target enumeration).
    parent_run_dir = home_dir / _RUN_SUBDIR
    parent_run_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent_run_dir.chmod(0o700)

    # POSIX guarantees PID uniqueness while the holder is alive, so any dir
    # already at our PID path is the residue of a crashed prior session.
    if run_dir.exists():
        _logger.warning(
            "split_to_tmpfs: removing stale run dir %s (likely from a "
            "prior crashed session at the same PID)",
            run_dir,
        )
        shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
    # ``mkdir(mode=...)`` is umask-masked on POSIX; pin explicitly.
    run_dir.chmod(0o700)

    share_a_path = run_dir / _SHARE_A_NAME
    share_b_path = run_dir / _SHARE_B_NAME

    # Pre-declared so the SR-02 zero-loop in the except branch sees the names
    # even if ``split_key`` itself raises before assignment.
    shard_a: bytearray | None = None
    shard_b: bytearray | None = None
    try:
        result = split_key(fernet_key)
        shard_a = result.shard_a
        shard_b = result.shard_b
        _write_share(share_a_path, shard_a)
        _write_share(share_b_path, shard_b)
    except BaseException:
        for path in (share_a_path, share_b_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            run_dir.rmdir()
        except OSError:
            pass
        # SR-02: zero plaintext shards before re-raising.
        for shard in (shard_a, shard_b):
            if shard is not None:
                zero_buf(shard)
        raise

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
# spawn_sidecar (WRTLS-113 SIDECAR_NOT_READY) + SidecarHandle
# ---------------------------------------------------------------------------


_READY_POLL_INTERVAL_S = 0.05

# AF_UNIX ``sockaddr_un.sun_path`` is char[104] on macOS, char[108] on Linux.
# Cap at the smaller (104) for portability. The string + null terminator
# must fit, so usable strings are at most 103 bytes on macOS.
_AF_UNIX_SUN_PATH_LIMIT = 104


@dataclass
class SidecarHandle:
    """Live handle to a spawned sidecar subprocess.

    The caller owns the lifecycle. No ``__enter__``/``__exit__`` —
    cleanup ordering matters and is wired explicitly by callers.

    ``drain_timeout`` is forwarded to the sidecar via
    ``WORTHLESS_SIDECAR_DRAIN_TIMEOUT`` and read back by
    :func:`shutdown_sidecar` to size the SIGTERM grace window.
    """

    proc: subprocess.Popen[bytes]
    socket_path: Path
    shares: ShareFiles
    allowed_uid: int
    drain_timeout: float


def _can_connect(socket_path: Path) -> bool:
    """True iff a connect() to *socket_path* succeeds.

    The socket inode appears at ``bind()`` time, BEFORE ``listen()`` —
    using ``socket_path.exists()`` as a ready signal opens a window where
    the proxy's first IPC ``connect()`` would hit ``ECONNREFUSED``. A
    successful ``connect()`` is the only ready signal that proves
    ``listen()`` has been called.

    Probe is cheap: AF_UNIX socket creation, 0.1 s timeout, immediate
    close. The sidecar's accept loop sees a connect-then-disconnect (no
    payload sent), which is benign.
    """
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    except OSError:
        return False
    try:
        sock.settimeout(0.1)
        try:
            sock.connect(str(socket_path))
        except OSError:
            return False
        return True
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _wait_for_ready(
    proc: subprocess.Popen[bytes],
    socket_path: Path,
    deadline: float,
) -> bool:
    """Poll until ``socket_path`` is bound AND listening, or the child exits.

    Uses a ``connect()`` probe rather than ``socket_path.exists()``: the
    inode appears at ``bind()`` time, but the proxy's first IPC call
    fails with ``ECONNREFUSED`` until ``listen()`` has run. The race
    window is microseconds in the happy case but real on busy systems —
    a connect-probe closes it deterministically.

    We deliberately do NOT parse stdout: PIPE buffers ~64 KB of kernel
    data, and ``readline()`` blocks until newline-or-EOF, which would
    deadlock if the sidecar ever grew chatty before binding.
    """
    while time.monotonic() < deadline:
        # Cheap fast-fail before the more expensive connect-probe: if the
        # inode doesn't exist yet, neither does the listening socket.
        if socket_path.exists() and _can_connect(socket_path):
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
        WorthlessError: WRTLS-113 if the path is too long for AF_UNIX or
            the sidecar does not become ready.
    """
    # AF_UNIX sun_path is 104 on macOS, 108 on Linux. Eager check surfaces
    # the real cause; otherwise the sidecar's bind() fails with ENAMETOOLONG
    # and we'd report a misleading "did not become ready" timeout.
    encoded_path = str(socket_path).encode()
    if len(encoded_path) >= _AF_UNIX_SUN_PATH_LIMIT:
        raise WorthlessError(
            ErrorCode.SIDECAR_NOT_READY,
            f"socket path too long for AF_UNIX "
            f"({len(encoded_path)} bytes; max {_AF_UNIX_SUN_PATH_LIMIT - 1}): "
            f"{socket_path}",
        )

    # A stale socket inode at the target path would make _wait_for_ready
    # return True against the leftover before the new sidecar binds.
    if socket_path.exists():
        try:
            socket_path.unlink()
        except OSError:
            # Couldn't clear the stale socket; bail before we spawn a child
            # that would race against an unmovable inode.
            raise WorthlessError(
                ErrorCode.SIDECAR_NOT_READY,
                f"stale socket at {socket_path} could not be removed",
            ) from None

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

    # Reap the child on ANY BaseException — WRTLS-113 timeout, KeyboardInterrupt
    # from the poll loop, signal-mapped-to-KbdInt — otherwise the spawned
    # sidecar PID leaks as an orphan the caller can't see.
    try:
        deadline = time.monotonic() + ready_timeout
        ready = _wait_for_ready(proc, socket_path, deadline)
        if not ready:
            raise WorthlessError(
                ErrorCode.SIDECAR_NOT_READY,
                f"sidecar did not become ready within {ready_timeout}s",
            )
    except BaseException:
        # Kill the child, drain stdout+stderr with a deadline (one call,
        # no SIGPIPE risk), unlink any half-formed socket inode.
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
        raise

    _logger.debug("spawn_sidecar: pid=%d socket=%s", proc.pid, socket_path)
    return SidecarHandle(
        proc=proc,
        socket_path=socket_path,
        shares=shares,
        allowed_uid=allowed_uid,
        drain_timeout=drain_timeout,
    )


# ---------------------------------------------------------------------------
# shutdown_sidecar — graceful teardown + SR-02 zeroing
# ---------------------------------------------------------------------------


# SIGKILL follow-up only needs long enough for the kernel to reap.
_SHUTDOWN_KILL_GRACE_S = 2.0


def shutdown_sidecar(handle: SidecarHandle) -> None:
    """Terminate the sidecar and clean up its on-disk state.

    Steps, in order:

    1. SIGTERM the process; wait up to ``handle.drain_timeout`` seconds for
       a graceful exit, matching the drain budget the sidecar was started with.
    2. SIGKILL if still alive after the grace window; wait for the kernel
       to reap.
    3. Best-effort unlink of ``share_a.bin``, ``share_b.bin``, and the
       sidecar socket inode.
    4. Best-effort ``rmdir`` of the per-pid run dir.
    5. Zero the ``shard_a`` and ``shard_b`` bytearrays in memory (SR-02).

    Idempotent: a second call after a successful first call MUST NOT raise.
    """
    proc = handle.proc

    if proc.poll() is None:
        # The child can exit between poll() and signal — ProcessLookupError
        # is benign; proceed to wait + cleanup either way.
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=handle.drain_timeout)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=_SHUTDOWN_KILL_GRACE_S)
            except subprocess.TimeoutExpired:
                # Kernel didn't reap; the up.py supervisor surfaces this as
                # WRTLS-112 if the runaway persists.
                pass

    for path in (
        handle.shares.share_a_path,
        handle.shares.share_b_path,
        handle.socket_path,
    ):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            _logger.warning("shutdown_sidecar: could not unlink %s", path)

    try:
        handle.shares.run_dir.rmdir()
    except FileNotFoundError:
        pass
    except OSError:
        _logger.warning(
            "shutdown_sidecar: could not rmdir %s (run dir not empty)",
            handle.shares.run_dir,
        )

    # SR-02: zero in-memory shard buffers. Idempotent on re-call.
    zero_buf(handle.shares.shard_a)
    zero_buf(handle.shares.shard_b)
