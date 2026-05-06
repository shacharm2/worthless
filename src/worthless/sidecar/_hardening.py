"""Linux-side process hardening for the Fernet sidecar (WOR-310 Phase A).

Two primitives, both invoked by ``__main__.main()`` ahead of any share
load or socket bind:

* :func:`set_dumpable_zero` — calls ``prctl(PR_SET_DUMPABLE, 0)`` so the
  kernel refuses ptrace from non-parent processes regardless of YAMA and
  refuses to write a core dump if the process crashes mid-decrypt.
  Linux-only; silent no-op on Darwin/Windows.

* :func:`check_yama_ptrace_scope` — verifies
  ``/proc/sys/kernel/yama/ptrace_scope >= 1``. Value ``0`` permits any
  same-uid process to attach via ``ptrace``, defeating the
  proxy-can't-read-sidecar-memory invariant on bare metal. Raises
  ``WorthlessError(YAMA_PTRACE_SCOPE_TOO_LOW)`` on ``0``; treats missing
  or malformed file as warn-pass (Mac dev path, custom kernels).

Both are stdlib-only and avoid touching any cryptographic state.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import sys
from pathlib import Path

from worthless.cli.errors import ErrorCode, WorthlessError

_LOG = logging.getLogger("worthless.sidecar.hardening")

# https://man7.org/linux/man-pages/man2/prctl.2.html — PR_SET_DUMPABLE = 4
PR_SET_DUMPABLE = 4

# Distro-portable YAMA control file.  Each major distro keeps the same path;
# kernels without YAMA simply don't expose the file.
YAMA_FILE = Path("/proc/sys/kernel/yama/ptrace_scope")


def set_dumpable_zero() -> None:
    """Set ``PR_SET_DUMPABLE=0`` on the current process (Linux only).

    Effects (all kernel-enforced):

    * No core dump is written if the process crashes — the Fernet key
      cannot end up on disk via ``/var/lib/systemd/coredump`` or a
      ``ulimit -c unbounded`` operator override.
    * ``/proc/<pid>/mem`` becomes unreadable to non-parent processes
      (independent of YAMA).
    * ``ptrace`` from non-parent processes is refused (independent of
      YAMA — defense in depth on top of :func:`check_yama_ptrace_scope`).

    Silent no-op on macOS/Windows. A non-zero ``prctl`` return — or a
    libc that ``ctypes.util.find_library("c")`` can't locate — surfaces as
    ``SIDECAR_NOT_READY`` because proceeding without dumpable=0 would
    silently break the security claim.
    """
    # ``sys.platform == "linux"`` matches the gating idiom used elsewhere
    # in the codebase (peercred.py, fs_check.py).
    if sys.platform != "linux":
        return
    # ``find_library`` returns the soname for both glibc (``libc.so.6``)
    # and musl (``libc.musl-x86_64.so.1``); hardcoding ``libc.so.6`` would
    # break Alpine, which is in the install matrix.
    libc_path = ctypes.util.find_library("c")
    if libc_path is None:
        raise WorthlessError(
            ErrorCode.SIDECAR_NOT_READY,
            "ctypes.util.find_library('c') returned None on Linux; "
            "refusing to start without PR_SET_DUMPABLE=0.",
        )
    libc = ctypes.CDLL(libc_path, use_errno=True)
    rc = libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)
    if rc != 0:
        errno = ctypes.get_errno()
        raise WorthlessError(
            ErrorCode.SIDECAR_NOT_READY,
            f"prctl(PR_SET_DUMPABLE, 0) failed with errno={errno}; "
            "refusing to start without core-dump protection.",
        )
    _LOG.debug("PR_SET_DUMPABLE=0 — core dumps and non-parent ptrace blocked")


def set_dumpable_zero_or_log() -> None:
    """Fork-child-safe variant of :func:`set_dumpable_zero` (WOR-310 Phase C2).

    Identical syscall (``prctl(PR_SET_DUMPABLE, 0)``) but logs at ``ERROR``
    on any failure path instead of raising. Required because Phase C2's
    ``preexec_fn`` runs in the forked child between ``fork()`` and
    ``exec()`` — Python exception propagation back to the parent is
    undefined there. A raise in that window leaves the parent with a
    child that has *partially* dropped privs; logging + return keeps the
    spawn deterministic (succeeds with dumpable, or fails at exec).

    Linux-only — silent no-op on Darwin/Windows, identical to the strict
    variant.
    """
    if sys.platform != "linux":
        return
    libc_path = ctypes.util.find_library("c")
    if libc_path is None:
        _LOG.error(
            "ctypes.util.find_library('c') returned None on Linux; "
            "skipping PR_SET_DUMPABLE=0 inside preexec_fn (libc unreachable)"
        )
        return
    libc = ctypes.CDLL(libc_path, use_errno=True)
    rc = libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)
    if rc != 0:
        errno = ctypes.get_errno()
        _LOG.error(
            "prctl(PR_SET_DUMPABLE, 0) failed inside preexec_fn with errno=%d; "
            "core-dump protection NOT applied to forked child",
            errno,
        )
        return
    _LOG.debug("PR_SET_DUMPABLE=0 (preexec) — applied to forked child")


def check_yama_ptrace_scope() -> None:
    """Refuse to start if YAMA permits cross-uid memory reads.

    Reads ``/proc/sys/kernel/yama/ptrace_scope``:

    * ``0`` → any same-uid process can ``ptrace`` any other; refuse.
    * ``1`` (default on Ubuntu/Debian) → restricted (parent-only); pass.
    * ``2`` → admin-only; pass.
    * ``3`` → ptrace disabled; pass.

    Missing file (Mac dev path, kernels without YAMA, rootless containers
    on locked-down hosts) and malformed values are warn-pass: the check
    is a kernel-level advisory, not a kernel-version detector.
    """
    try:
        raw = YAMA_FILE.read_text()
    except (FileNotFoundError, PermissionError, OSError) as exc:
        _LOG.warning(
            "%s unreadable (%s) — skipping YAMA check; rely on PR_SET_DUMPABLE for ptrace defense.",
            YAMA_FILE,
            exc.__class__.__name__,
        )
        return
    try:
        scope = int(raw.strip())
    except ValueError:
        _LOG.warning(
            "%s contained non-numeric value %r — skipping YAMA check.",
            YAMA_FILE,
            raw,
        )
        return
    if scope < 1:
        raise WorthlessError(
            ErrorCode.YAMA_PTRACE_SCOPE_TOO_LOW,
            f"YAMA ptrace_scope={scope} permits same-uid memory reads. "
            f"Set {YAMA_FILE} to 1 or higher (Ubuntu/Debian default is 1) "
            "before starting the sidecar.",
        )
    _LOG.debug("YAMA ptrace_scope=%d — cross-process ptrace gated by kernel", scope)
