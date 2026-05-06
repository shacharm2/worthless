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


def _load_libc() -> ctypes.CDLL | None:
    """Load libc with a deterministic fallback chain (WOR-310 C2f).

    ``ctypes.util.find_library('c')`` shells out to ``gcc``/``ld``/
    ``ldconfig`` — these are absent from distroless final stages. The
    primary ``libc.so.6`` and ``libc.musl-x86_64.so.1`` covers both
    glibc and musl distributions, so we try those by name FIRST and
    fall back to ``find_library`` only if both fail.

    Returns ``None`` when no libc can be located; caller logs and
    returns (preserving the fork-child-safe contract of the ``_or_log``
    variants).
    """
    if sys.platform != "linux":
        return None
    for soname in ("libc.so.6", "libc.musl-x86_64.so.1"):
        try:
            return ctypes.CDLL(soname, use_errno=True)
        except OSError:
            continue
    libc_path = ctypes.util.find_library("c")
    if libc_path is None:
        return None
    try:
        return ctypes.CDLL(libc_path, use_errno=True)
    except OSError:
        return None


# https://man7.org/linux/man-pages/man2/prctl.2.html — PR_SET_DUMPABLE = 4
PR_SET_DUMPABLE = 4

# https://man7.org/linux/man-pages/man2/prctl.2.html — PR_SET_NO_NEW_PRIVS = 38
# Locks the no_new_privs bit. Once set, the process and its children can
# never gain privs via setuid/setgid binaries or file capabilities. Phase
# C2 sets this in the forked child BEFORE the uid drop so the bit is
# locked under root's CAP_SYS_ADMIN and applies to the dropped uid.
PR_SET_NO_NEW_PRIVS = 38

# https://man7.org/linux/man-pages/man2/prctl.2.html — PR_CAPBSET_DROP = 24
# Removes a capability from the bounding set. Even with NO_NEW_PRIVS set,
# a process that retains its bounding set could regain capabilities via
# (rare) setuid file capabilities or LSM-mediated transitions. Dropping
# every cap from the bounding set means the dropped uid CANNOT regain
# any capability, ever — defense in depth alongside NNP.
PR_CAPBSET_DROP = 24

# Linux capabilities are 0..CAP_LAST_CAP (currently 40 on kernel 5.15+,
# but kernels add new caps over time). We iterate to a safe upper bound;
# unknown caps simply EINVAL — caught by the rc != 0 + errno check.
_CAPBSET_RANGE = range(64)

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
    libc = _load_libc()
    if libc is None:
        raise WorthlessError(
            ErrorCode.SIDECAR_NOT_READY,
            "could not load libc on Linux (tried libc.so.6, "
            "libc.musl-x86_64.so.1, ctypes.util.find_library('c')); "
            "refusing to start without PR_SET_DUMPABLE=0.",
        )
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
    libc = _load_libc()
    if libc is None:
        _LOG.error(
            "could not load libc on Linux; skipping PR_SET_DUMPABLE=0 inside "
            "preexec_fn (libc unreachable). Core-dump protection NOT applied."
        )
        return
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


def set_no_new_privs_or_log() -> None:
    """Fork-child-safe ``prctl(PR_SET_NO_NEW_PRIVS, 1)`` (WOR-310 Phase C2).

    Locks the no_new_privs bit so the process (and any children) cannot
    gain privs via setuid/setgid binaries or file capabilities — even if
    a future setuid binary somehow ends up on PATH inside the container,
    invoking it produces ENOENT-flavored failure instead of escalation.

    Sibling of :func:`set_dumpable_zero_or_log`: same fork-child-safe
    contract — never raises, logs at ERROR on any failure path. C2's
    ``preexec_fn`` calls this BEFORE ``setresuid`` so the bit is locked
    while we still have CAP_SYS_ADMIN (cleaner audit) and applies to
    the dropped uid.

    Linux-only — silent no-op on Darwin/Windows.
    """
    if sys.platform != "linux":
        return
    libc = _load_libc()
    if libc is None:
        _LOG.error(
            "could not load libc on Linux; skipping PR_SET_NO_NEW_PRIVS=1 "
            "inside preexec_fn. Setuid escalation defense NOT applied."
        )
        return
    rc = libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)
    if rc != 0:
        errno = ctypes.get_errno()
        _LOG.error(
            "prctl(PR_SET_NO_NEW_PRIVS, 1) failed inside preexec_fn with errno=%d; "
            "no_new_privs NOT applied to forked child",
            errno,
        )
        return
    _LOG.debug("PR_SET_NO_NEW_PRIVS=1 (preexec) — applied to forked child")


def set_capbset_drop_or_log() -> None:
    """Drop ALL capabilities from the bounding set (WOR-310 C2f).

    Defense in depth on top of NO_NEW_PRIVS. NNP locks "no NEW privs",
    but a process with a populated bounding set could still — in
    pathological scenarios (legacy file capabilities, LSM transitions) —
    retain authority. Iterating ``prctl(PR_CAPBSET_DROP, cap)`` for cap
    0..63 removes EVERY capability from the bounding set. After this,
    the dropped uid CANNOT regain any capability under any kernel-
    supported escalation path.

    Errors are logged + swallowed (fork-child-safe contract). EINVAL
    on out-of-range cap numbers is expected (kernels < 5.15 reject the
    higher cap numbers); we ignore EINVAL specifically and only log
    other errnos.

    Linux-only — silent no-op on Darwin/Windows.
    """
    if sys.platform != "linux":
        return
    libc = _load_libc()
    if libc is None:
        _LOG.error(
            "could not load libc on Linux; skipping PR_CAPBSET_DROP. "
            "Capability bounding set NOT dropped."
        )
        return

    # EINVAL is the kernel saying "no such capability number" — expected
    # for caps the running kernel doesn't know about. Only non-EINVAL
    # errors are real failures worth flagging.
    EINVAL = 22
    failed_caps: list[tuple[int, int]] = []
    for cap in _CAPBSET_RANGE:
        rc = libc.prctl(PR_CAPBSET_DROP, cap, 0, 0, 0)
        if rc != 0:
            errno = ctypes.get_errno()
            if errno != EINVAL:
                failed_caps.append((cap, errno))

    if failed_caps:
        _LOG.error(
            "PR_CAPBSET_DROP failed for %d capability/capabilities (cap, errno): %s; "
            "capability bounding set may still be populated",
            len(failed_caps),
            failed_caps,
        )
    else:
        _LOG.debug("PR_CAPBSET_DROP — full bounding set cleared on forked child")


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
