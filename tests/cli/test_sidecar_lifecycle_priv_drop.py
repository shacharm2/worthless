"""Privilege-drop tests for ``spawn_sidecar`` (WOR-310 Phase C).

Phase C wires a runtime ``setresgid → setgroups([]) → PR_SET_NO_NEW_PRIVS
→ setresuid → PR_SET_DUMPABLE=0`` dance into ``deploy/start.py`` so the
single-container Docker topology runs:

* ``worthless-crypto`` (uid 10002) — sidecar process
* ``worthless-proxy`` (uid 10001) — uvicorn process

Both inside one container, neither root, kernel-enforced uid wall.

The dance is split across the lifecycle module (``spawn_sidecar``
gains a ``service_uids`` kwarg + a private ``_make_priv_drop_preexec``
factory) and ``deploy/start.py`` (parent drops self after spawn).

This file pins the **foundation** (Segment C1):

* ``ServiceUids`` NamedTuple — immutable, single Optional through
  ``spawn_sidecar`` (no ``target_uid + target_gid`` invalid-state pair).
* ``subprocess.Popen(close_fds=True, pass_fds=())`` — every spawn,
  not just when dropping privs. Closes brutus's weakest-link FD-leak.
* ``_hardening.set_dumpable_zero_or_log()`` — fork-child-safe variant
  that logs instead of raising. Phase C2's ``preexec_fn`` calls this
  inside the forked child where raising loses partial-drop state.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import worthless.cli.sidecar_lifecycle as _sidecar_lifecycle
from worthless.cli.errors import ErrorCode, WorthlessError
from worthless.cli.sidecar_lifecycle import ServiceUids, ShareFiles, spawn_sidecar
from worthless.sidecar import _hardening

# Linux prctl constant — pinned at module scope so a future refactor
# that quietly changes the value (e.g. shadows ``PR_SET_NO_NEW_PRIVS``
# with a different code) is caught by the test that compares to this
# literal. man prctl(2): PR_SET_NO_NEW_PRIVS = 38.
PR_SET_NO_NEW_PRIVS = 38


# ---------------------------------------------------------------------------
# ServiceUids — pinned NamedTuple shape
# ---------------------------------------------------------------------------


def test_service_uids_is_immutable_namedtuple() -> None:
    """``ServiceUids`` must be a NamedTuple with three int fields.

    The `target_uid + target_gid` two-Optional API was rejected by the
    architect review (invalid `(set, None)` state representable). One
    NamedTuple, one Optional through `spawn_sidecar`. Pinning the shape
    here so a future refactor to a dataclass — which would be mutable —
    is caught.
    """
    uids = ServiceUids(proxy_uid=10001, crypto_uid=10002, worthless_gid=10001)
    assert uids.proxy_uid == 10001
    assert uids.crypto_uid == 10002
    assert uids.worthless_gid == 10001
    # NamedTuples raise AttributeError on attribute assignment.
    with pytest.raises(AttributeError):
        uids.proxy_uid = 9999  # type: ignore[misc]


def test_service_uids_field_order_is_proxy_crypto_gid() -> None:
    """Pin positional order so ``ServiceUids(10001, 10002, 10001)`` reads correctly.

    Positional construction is allowed (it's a NamedTuple) — the field
    order pins the meaning. A future reorder would silently shift uids
    if any caller used positional args.
    """
    assert ServiceUids._fields == ("proxy_uid", "crypto_uid", "worthless_gid")


@pytest.mark.parametrize(
    ("proxy_uid", "crypto_uid", "worthless_gid"),
    [
        (0, 10002, 10001),  # proxy=root → no-op drop
        (10001, 0, 10001),  # crypto=root → silently breaks claim
        (10001, 10002, 0),  # gid=root group → group-permission escape
        (-1, 10002, 10001),  # negative uid (impossible but defensive)
    ],
)
def test_spawn_sidecar_rejects_service_uids_with_root_or_negative_id(
    _share_files: ShareFiles, proxy_uid: int, crypto_uid: int, worthless_gid: int
) -> None:
    """``spawn_sidecar`` refuses to start when any id is < 1 (root or negative).

    A future Dockerfile drift / shadowed ``/etc/passwd`` that resolves
    ``worthless-proxy`` to uid 0 would silently no-op the privilege drop
    in C2 — the sidecar would still run as root, killing the v1.1
    security claim with no log line. Eager validation at the spawn
    boundary turns the silent failure into a structured WRTLS-114.
    """
    import uuid

    socket_path = Path(f"/tmp/wor310-c1-{uuid.uuid4().hex[:8]}.sock")  # noqa: S108
    bad_uids = ServiceUids(proxy_uid=proxy_uid, crypto_uid=crypto_uid, worthless_gid=worthless_gid)
    with pytest.raises(WorthlessError) as exc_info:
        spawn_sidecar(
            socket_path=socket_path,
            shares=_share_files,
            allowed_uid=1000,
            service_uids=bad_uids,
        )
    assert exc_info.value.code == ErrorCode.SIDECAR_NOT_READY
    assert "non-root" in exc_info.value.message


# ---------------------------------------------------------------------------
# spawn_sidecar — Popen kwargs
# ---------------------------------------------------------------------------


@pytest.fixture
def _share_files(tmp_path: Path) -> ShareFiles:
    """Minimal ShareFiles for spawn_sidecar to consume.

    The Popen call is mocked, so the share contents never matter — but
    the dataclass needs valid Path types. Real bytes match the WOR-307
    contract (44-byte b64 fernet key) so a future schema check on
    ShareFiles construction wouldn't fail this fixture spuriously.
    """
    run_dir = tmp_path / "run" / "12345"
    run_dir.mkdir(parents=True)
    a_path = run_dir / "share_a"
    b_path = run_dir / "share_b"
    a_path.write_bytes(b"a" * 44)
    b_path.write_bytes(b"b" * 44)
    return ShareFiles(
        run_dir=run_dir,
        share_a_path=a_path,
        share_b_path=b_path,
        shard_a=bytearray(b"a" * 44),
        shard_b=bytearray(b"b" * 44),
    )


def test_spawn_sidecar_passes_close_fds_and_empty_pass_fds(
    _share_files: ShareFiles,
) -> None:
    """``Popen`` must always get ``close_fds=True, pass_fds=()`` (WOR-310 brutus).

    Without this, the SQLite handle, log fds, prometheus socket, and
    any other open descriptor in the parent process inherit into the
    sidecar. RCE in the sidecar = read everything. Pinning the kwargs
    here is the foundation of the uid-wall security claim — kernel
    isolation only matters if the FDs aren't already shared.

    Applies on EVERY spawn, not just when dropping privs (defense in
    depth on bare-metal where uid wall doesn't exist).
    """
    # AF_UNIX sun_path limit (104 on macOS) — pytest's nested tmp_path on
    # macOS exceeds it, so we use a short ``/tmp`` path.  Process is fully
    # mocked; the inode is never created.
    import uuid

    socket_path = Path(f"/tmp/wor310-c1-{uuid.uuid4().hex[:8]}.sock")  # noqa: S108
    captured_kwargs: dict[str, object] = {}

    fake_proc = MagicMock()
    fake_proc.pid = 12345
    fake_proc.poll.return_value = None

    def fake_popen(*_args: object, **kwargs: object) -> MagicMock:
        captured_kwargs.update(kwargs)
        return fake_proc

    with (
        patch.object(_sidecar_lifecycle.subprocess, "Popen", fake_popen),
        patch.object(_sidecar_lifecycle, "_wait_for_ready", return_value=True),
    ):
        spawn_sidecar(
            socket_path=socket_path,
            shares=_share_files,
            allowed_uid=1000,
        )

    assert captured_kwargs.get("close_fds") is True, (
        "WOR-310 C1: Popen must close_fds=True to prevent FD inheritance into the sidecar process"
    )
    assert captured_kwargs.get("pass_fds") == (), (
        "WOR-310 C1: Popen must pass_fds=() — empty tuple, not unset — so no FD ever inherits"
    )


# ---------------------------------------------------------------------------
# set_dumpable_zero_or_log — fork-child-safe variant
# ---------------------------------------------------------------------------


def test_set_dumpable_zero_or_log_exists_as_separate_function() -> None:
    """The ``_or_log`` variant must be its own callable.

    Phase A's ``set_dumpable_zero`` raises ``WorthlessError`` on
    failure. Phase C2's preexec_fn cannot raise cleanly (the forked
    child between fork and exec has no exception path back to the
    parent). The architect review required a child-safe variant.
    """
    assert callable(_hardening.set_dumpable_zero_or_log), (
        "WOR-310 C1: _hardening.set_dumpable_zero_or_log must exist as a "
        "fork-child-safe variant of set_dumpable_zero"
    )
    assert _hardening.set_dumpable_zero_or_log is not _hardening.set_dumpable_zero, (
        "WOR-310 C1: _or_log variant must be a distinct function, not an alias"
    )


def test_set_dumpable_zero_or_log_logs_and_returns_on_prctl_failure(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_or_log`` must swallow ``prctl != 0`` and log instead of raising.

    Inside ``preexec_fn`` the forked child has no clean way to surface
    a Python exception back to the parent — it crashes the child,
    leaving the parent with a child that may have already partially
    dropped privs. The ``_or_log`` variant logs at ERROR and returns
    so the spawn either completes (uvicorn sees the dropped child) or
    fails at exec, never mid-drop.
    """
    # Force the Linux path so the test runs cross-platform; the test's
    # subject is the failure-handling branch, not the platform gate.
    monkeypatch.setattr(_hardening.sys, "platform", "linux")
    fake_libc = MagicMock()
    fake_libc.prctl.return_value = -1

    with (
        patch("worthless.sidecar._hardening.ctypes.util.find_library", return_value="libc.so.6"),
        patch("worthless.sidecar._hardening.ctypes.CDLL", return_value=fake_libc),
        caplog.at_level(logging.ERROR, logger="worthless.sidecar.hardening"),
    ):
        _hardening.set_dumpable_zero_or_log()  # must not raise

    assert any(
        "PR_SET_DUMPABLE" in rec.message or "dumpable" in rec.message for rec in caplog.records
    ), "WOR-310 C1: _or_log variant must emit an ERROR log when prctl fails"


def test_set_dumpable_zero_or_log_logs_when_libc_unreachable(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_or_log`` swallows ``find_library`` returning None too.

    Same rationale as the prctl-failure case: any Linux failure path
    inside preexec_fn must log + return, never raise.
    """
    monkeypatch.setattr(_hardening.sys, "platform", "linux")
    with (
        patch("worthless.sidecar._hardening.ctypes.util.find_library", return_value=None),
        caplog.at_level(logging.ERROR, logger="worthless.sidecar.hardening"),
    ):
        _hardening.set_dumpable_zero_or_log()  # must not raise

    assert any("libc" in rec.message or "find_library" in rec.message for rec in caplog.records), (
        "WOR-310 C1: _or_log must log when libc is unreachable"
    )


def test_set_dumpable_zero_or_log_is_noop_on_non_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-Linux short-circuits before any ctypes interaction.

    Same Darwin/Windows behavior as Phase A's ``set_dumpable_zero``:
    silent no-op. The dev path on macOS must NEVER hit libc.
    """
    monkeypatch.setattr(_hardening.sys, "platform", "darwin")
    sentinel = MagicMock(side_effect=AssertionError("CDLL must not run on non-Linux"))
    monkeypatch.setattr(_hardening.ctypes, "CDLL", sentinel)
    monkeypatch.setattr(_hardening.ctypes.util, "find_library", sentinel)
    _hardening.set_dumpable_zero_or_log()  # must not raise


# ---------------------------------------------------------------------------
# _make_priv_drop_preexec — syscall ORDER + correctness (Segment C2)
# ---------------------------------------------------------------------------


def test_make_priv_drop_preexec_calls_in_correct_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The preexec_fn must call syscalls in exactly this order:

      1. ``os.setresgid(gid, gid, gid)``       — first, still has CAP_SETGID
      2. ``os.setgroups([])``                  — clear inherited groups
      3. ``_hardening.set_no_new_privs_or_log()`` — locks before uid drop
      4. ``os.setresuid(uid, uid, uid)``       — last, drops cap_set*
      5. ``_hardening.set_dumpable_zero_or_log()`` — applies to dropped process

    A swap (e.g. setresuid before setgroups) breaks the kernel-enforced
    capability model: setgroups() requires CAP_SETGID, which setresuid()
    drops. Pinning the exact order here so a future refactor can't
    silently rearrange and turn the security claim into theater.
    """
    calls: list[str] = []

    monkeypatch.setattr(
        _sidecar_lifecycle.os,
        "setresgid",
        lambda r, e, s: calls.append(f"setresgid({r},{e},{s})"),
        raising=False,
    )
    monkeypatch.setattr(
        _sidecar_lifecycle.os,
        "setgroups",
        lambda groups: calls.append(f"setgroups({list(groups)})"),
        raising=False,
    )
    monkeypatch.setattr(
        _sidecar_lifecycle.os,
        "setresuid",
        lambda r, e, s: calls.append(f"setresuid({r},{e},{s})"),
        raising=False,
    )
    monkeypatch.setattr(
        _hardening,
        "set_no_new_privs_or_log",
        lambda: calls.append("set_no_new_privs_or_log"),
    )
    monkeypatch.setattr(
        _hardening,
        "set_dumpable_zero_or_log",
        lambda: calls.append("set_dumpable_zero_or_log"),
    )

    uids = ServiceUids(proxy_uid=10001, crypto_uid=10002, worthless_gid=10001)
    preexec = _sidecar_lifecycle._make_priv_drop_preexec(uids)
    preexec()  # invoke as if inside the forked child

    assert calls == [
        "setresgid(10001,10001,10001)",
        "setgroups([])",
        "set_no_new_privs_or_log",
        "setresuid(10002,10002,10002)",
        "set_dumpable_zero_or_log",
    ], f"WOR-310 C2: priv-drop syscall order is wrong: {calls}"


def test_make_priv_drop_preexec_uses_setresgid_not_setgid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``setresgid`` is required, NOT ``setgid`` — saved-gid leak guard.

    ``os.setgid(gid)`` only sets effective gid (when euid=0 it sets all
    three on Linux, but that's glibc behavior, not kernel ABI). The
    saved gid being left as 0 means a post-RCE attacker that gains
    CAP_SETGID can swap back. ``setresgid(gid, gid, gid)`` locks all
    three atomically.
    """
    sentinel_setgid = MagicMock(
        side_effect=AssertionError("WOR-310 C2: must use setresgid, not setgid")
    )
    monkeypatch.setattr(_sidecar_lifecycle.os, "setgid", sentinel_setgid, raising=False)
    monkeypatch.setattr(_sidecar_lifecycle.os, "setresgid", lambda r, e, s: None, raising=False)
    monkeypatch.setattr(_sidecar_lifecycle.os, "setgroups", lambda g: None, raising=False)
    monkeypatch.setattr(_sidecar_lifecycle.os, "setresuid", lambda r, e, s: None, raising=False)
    monkeypatch.setattr(_hardening, "set_no_new_privs_or_log", lambda: None)
    monkeypatch.setattr(_hardening, "set_dumpable_zero_or_log", lambda: None)

    uids = ServiceUids(proxy_uid=10001, crypto_uid=10002, worthless_gid=10001)
    _sidecar_lifecycle._make_priv_drop_preexec(uids)()  # must not raise


def test_setgroups_passed_empty_list_not_tuple(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``os.setgroups([])`` — empty *list*, not tuple.

    glibc accepts either, but musl (Alpine, in the install matrix)
    historically had quirks with non-list iterables. Pin the type so a
    future refactor to ``setgroups(())`` doesn't bite us at runtime on
    a customer's Alpine deployment.
    """
    captured: list[object] = []
    monkeypatch.setattr(_sidecar_lifecycle.os, "setresgid", lambda r, e, s: None, raising=False)
    monkeypatch.setattr(
        _sidecar_lifecycle.os, "setgroups", lambda g: captured.append(g), raising=False
    )
    monkeypatch.setattr(_sidecar_lifecycle.os, "setresuid", lambda r, e, s: None, raising=False)
    monkeypatch.setattr(_hardening, "set_no_new_privs_or_log", lambda: None)
    monkeypatch.setattr(_hardening, "set_dumpable_zero_or_log", lambda: None)

    uids = ServiceUids(proxy_uid=10001, crypto_uid=10002, worthless_gid=10001)
    _sidecar_lifecycle._make_priv_drop_preexec(uids)()

    assert captured == [[]], f"WOR-310 C2: setgroups must be called with [], got {captured}"
    assert isinstance(captured[0], list), (
        f"WOR-310 C2: setgroups arg must be a list, got {type(captured[0]).__name__}"
    )


def test_no_new_privs_called_before_setresuid(monkeypatch: pytest.MonkeyPatch) -> None:
    """``PR_SET_NO_NEW_PRIVS`` must be set BEFORE ``setresuid`` drops privs.

    If we drop uid first, then try to set NO_NEW_PRIVS, the syscall
    still works but the order pin protects the rationale: setting it
    pre-drop means the bit is locked under root's CAP_SYS_ADMIN
    (cleaner audit), and protects against a future bug where a
    seccomp filter or LSM might restrict prctl post-drop on some
    kernels.
    """
    calls: list[str] = []
    monkeypatch.setattr(_sidecar_lifecycle.os, "setresgid", lambda r, e, s: None, raising=False)
    monkeypatch.setattr(_sidecar_lifecycle.os, "setgroups", lambda g: None, raising=False)
    monkeypatch.setattr(
        _sidecar_lifecycle.os,
        "setresuid",
        lambda r, e, s: calls.append("setresuid"),
        raising=False,
    )
    monkeypatch.setattr(
        _hardening,
        "set_no_new_privs_or_log",
        lambda: calls.append("no_new_privs"),
    )
    monkeypatch.setattr(_hardening, "set_dumpable_zero_or_log", lambda: None)

    uids = ServiceUids(proxy_uid=10001, crypto_uid=10002, worthless_gid=10001)
    _sidecar_lifecycle._make_priv_drop_preexec(uids)()

    assert calls.index("no_new_privs") < calls.index("setresuid"), (
        f"WOR-310 C2: no_new_privs must precede setresuid; got {calls}"
    )


# ---------------------------------------------------------------------------
# spawn_sidecar wiring: preexec_fn only when service_uids set
# ---------------------------------------------------------------------------


def test_spawn_sidecar_passes_preexec_fn_when_service_uids_set(
    _share_files: ShareFiles,
) -> None:
    """When ``service_uids`` is set, ``Popen`` must receive a callable ``preexec_fn``.

    The forked child runs the callable to drop privs before exec.
    Without ``preexec_fn``, the sidecar inherits the parent's uid
    (root in Docker) and the security claim collapses.
    """
    import uuid

    socket_path = Path(f"/tmp/wor310-c2-{uuid.uuid4().hex[:8]}.sock")  # noqa: S108
    captured_kwargs: dict[str, object] = {}

    fake_proc = MagicMock()
    fake_proc.pid = 12345
    fake_proc.poll.return_value = None

    def fake_popen(*_args: object, **kwargs: object) -> MagicMock:
        captured_kwargs.update(kwargs)
        return fake_proc

    uids = ServiceUids(proxy_uid=10001, crypto_uid=10002, worthless_gid=10001)
    with (
        patch.object(_sidecar_lifecycle.subprocess, "Popen", fake_popen),
        patch.object(_sidecar_lifecycle, "_wait_for_ready", return_value=True),
    ):
        spawn_sidecar(
            socket_path=socket_path,
            shares=_share_files,
            allowed_uid=10001,
            service_uids=uids,
        )

    preexec_fn = captured_kwargs.get("preexec_fn")
    assert callable(preexec_fn), (
        f"WOR-310 C2: Popen must receive preexec_fn callable when service_uids "
        f"is set; got {preexec_fn!r}"
    )


def test_spawn_sidecar_omits_preexec_fn_when_service_uids_is_none(
    _share_files: ShareFiles,
) -> None:
    """Bare-metal path: ``service_uids=None`` → no ``preexec_fn`` at all.

    The bare-metal install never modifies the host. ``preexec_fn``
    setting it to anything would force a non-trivial fork-child
    callback that does nothing useful and risks the threading
    deadlock from BPO-34394. Cleaner to omit entirely.
    """
    import uuid

    socket_path = Path(f"/tmp/wor310-c2-{uuid.uuid4().hex[:8]}.sock")  # noqa: S108
    captured_kwargs: dict[str, object] = {}

    fake_proc = MagicMock()
    fake_proc.pid = 12345
    fake_proc.poll.return_value = None

    def fake_popen(*_args: object, **kwargs: object) -> MagicMock:
        captured_kwargs.update(kwargs)
        return fake_proc

    with (
        patch.object(_sidecar_lifecycle.subprocess, "Popen", fake_popen),
        patch.object(_sidecar_lifecycle, "_wait_for_ready", return_value=True),
    ):
        spawn_sidecar(
            socket_path=socket_path,
            shares=_share_files,
            allowed_uid=1000,
            service_uids=None,
        )

    # Either absent, or explicitly None — both are equivalent semantically.
    preexec = captured_kwargs.get("preexec_fn")
    assert preexec is None, (
        f"WOR-310 C2: bare-metal path (service_uids=None) must not set preexec_fn; got {preexec!r}"
    )


# ---------------------------------------------------------------------------
# set_no_new_privs_or_log — fork-child-safe variant of NO_NEW_PRIVS
# ---------------------------------------------------------------------------


def test_set_no_new_privs_or_log_exists_as_callable() -> None:
    """The C2 NO_NEW_PRIVS helper must exist as a fork-child-safe callable.

    Same shape as ``set_dumpable_zero_or_log``: log on failure, never
    raise inside the forked child. PR_SET_NO_NEW_PRIVS = 38 (Linux
    kernel uapi).
    """
    assert callable(_hardening.set_no_new_privs_or_log), (
        "WOR-310 C2: _hardening.set_no_new_privs_or_log must exist"
    )


def test_set_no_new_privs_or_log_invokes_prctl_with_38(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``prctl(PR_SET_NO_NEW_PRIVS=38, 1, 0, 0, 0)`` is the kernel call shape.

    Pinned literal 38 here so a future refactor that replaces with
    ``ctypes.c_int(some_const)`` doesn't silently change the call.
    """
    monkeypatch.setattr(_hardening.sys, "platform", "linux")
    fake_libc = MagicMock()
    fake_libc.prctl.return_value = 0
    with (
        patch("worthless.sidecar._hardening.ctypes.util.find_library", return_value="libc.so.6"),
        patch("worthless.sidecar._hardening.ctypes.CDLL", return_value=fake_libc),
    ):
        _hardening.set_no_new_privs_or_log()
    fake_libc.prctl.assert_called_once_with(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)


def test_set_no_new_privs_or_log_logs_on_failure(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``prctl != 0`` must log at ERROR and return — never raise from preexec_fn."""
    monkeypatch.setattr(_hardening.sys, "platform", "linux")
    fake_libc = MagicMock()
    fake_libc.prctl.return_value = -1
    with (
        patch("worthless.sidecar._hardening.ctypes.util.find_library", return_value="libc.so.6"),
        patch("worthless.sidecar._hardening.ctypes.CDLL", return_value=fake_libc),
        caplog.at_level(logging.ERROR, logger="worthless.sidecar.hardening"),
    ):
        _hardening.set_no_new_privs_or_log()  # must not raise

    assert any(
        "NO_NEW_PRIVS" in rec.message or "no_new_privs" in rec.message for rec in caplog.records
    ), "WOR-310 C2: failure must log at ERROR"


def test_set_no_new_privs_or_log_is_noop_on_non_linux(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-Linux short-circuits before any libc interaction (parity with set_dumpable)."""
    monkeypatch.setattr(_hardening.sys, "platform", "darwin")
    sentinel = MagicMock(side_effect=AssertionError("CDLL must not run on non-Linux"))
    monkeypatch.setattr(_hardening.ctypes, "CDLL", sentinel)
    monkeypatch.setattr(_hardening.ctypes.util, "find_library", sentinel)
    _hardening.set_no_new_privs_or_log()  # must not raise
