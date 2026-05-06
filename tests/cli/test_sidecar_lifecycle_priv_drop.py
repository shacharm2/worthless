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
