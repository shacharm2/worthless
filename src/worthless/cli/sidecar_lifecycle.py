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
    # Security panel C-1: ``parents=True`` creates the intermediate
    # ``~/.worthless/run/`` dir with the process umask masking the mode
    # arg — on a default umask 0o022 system that lands at 0o755,
    # world-traversable. The leaf <pid>/ dir is 0o700 so share files are
    # safe by Unix semantics, BUT a world-traversable parent leaks live
    # session PIDs to any local user, enabling targeted ptrace/proc-mem
    # attacks on the sidecar (which holds plaintext shard B). Pin both
    # the parent AND the leaf to 0o700 explicitly.
    parent_run_dir = home_dir / _RUN_SUBDIR
    parent_run_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent_run_dir.chmod(0o700)

    # If a directory already exists at our pid path, it's stale by physics:
    # POSIX guarantees PID uniqueness while the holder is alive, so any
    # prior occupant of this PID is dead (the kernel doesn't recycle a PID
    # until its owner has been reaped). Treat any leftover as the residue
    # of a crashed prior session — log a warning so the user knows we
    # cleaned up, then nuke + retry mkdir. Without this, ``worthless up``
    # would crash with a raw ``FileExistsError`` stack trace.
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

    # If anything past mkdir fails (split_key / disk-full / signal mid-write),
    # leave no half-state on disk — unlink any partial shares and remove the
    # run dir before re-raising. Cleanup itself is best-effort so it can't
    # mask the original exception.
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
# Phase B: spawn_sidecar + SidecarHandle (WRTLS-113 SIDECAR_NOT_READY)
# ---------------------------------------------------------------------------


_READY_POLL_INTERVAL_S = 0.05

# AF_UNIX ``sockaddr_un.sun_path`` is char[104] on macOS, char[108] on Linux.
# Cap at the smaller (104) for portability. The string + null terminator
# must fit, so usable strings are at most 103 bytes on macOS.
_AF_UNIX_SUN_PATH_LIMIT = 104


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
    # AF_UNIX ``sun_path`` is 104 bytes on macOS and 108 on Linux. We cap
    # at 104 (the lower) for portability — minus 1 for the null terminator,
    # so paths up to 103 bytes are accepted. Pre-check this BEFORE Popen:
    # an oversized path makes the sidecar's ``bind()`` fail with
    # ``ENAMETOOLONG``, the sidecar exits, and ``_wait_for_ready`` returns
    # False — surfaced as a misleading "did not become ready" timeout. Eager
    # validation surfaces the real cause and saves the spawn cycle.
    encoded_path = str(socket_path).encode()
    if len(encoded_path) >= _AF_UNIX_SUN_PATH_LIMIT:
        raise WorthlessError(
            ErrorCode.SIDECAR_NOT_READY,
            f"socket path too long for AF_UNIX "
            f"({len(encoded_path)} bytes; max {_AF_UNIX_SUN_PATH_LIMIT - 1}): "
            f"{socket_path}",
        )

    # A stale socket inode at the target path would make ``_wait_for_ready``
    # return True before the new sidecar has bound (false positive). The
    # per-pid run dir is created with ``mkdir(exist_ok=False)`` so this is
    # already extremely unlikely, but unlink-if-exists is cheap belt-and-braces
    # and cooperates with future Phase 4 crash-recovery flows.
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

    # Single cleanup envelope around the wait (QA #2): if anything raises
    # — WRTLS-113 timeout below, KeyboardInterrupt from the poll loop's
    # ``time.sleep``, SIGTERM-mapped-to-KbdInt from the spawn-window handler
    # the caller installed, OR any other ``BaseException`` — we MUST reap
    # the child Popen before propagating, otherwise the caller's
    # ``handle is None`` branch only sees the share files and the spawned
    # sidecar PID is leaked as an orphan.
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
        # Race: child can exit between poll() and signal. ProcessLookupError
        # on a vanished pid is benign — proceed to wait + cleanup either way.
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=handle.drain_timeout)
        except subprocess.TimeoutExpired:
            # Drain budget exhausted — escalate.
            try:
                proc.kill()
            except ProcessLookupError:
                pass
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
