"""Tests for ``worthless.cli.sidecar_lifecycle`` — WOR-384 Phases A + B."""

from __future__ import annotations

import base64
import importlib
import logging
import os
import secrets
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from worthless.cli.errors import ErrorCode, WorthlessError
from worthless.cli.sidecar_lifecycle import (
    ShareFiles,
    SidecarHandle,
    shutdown_sidecar,
    spawn_sidecar,
    split_to_tmpfs,
)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """A fresh Worthless home dir under the test's tmp_path.

    Per-test rather than session-scoped so xdist parallel workers don't
    collide on the per-pid run subdir (each test creates a *fresh* home,
    so the same pid writing into two homes is fine).
    """
    h = tmp_path / ".worthless"
    h.mkdir()
    return h


@pytest.fixture
def key() -> bytearray:
    """A 44-byte placeholder fernet key. Uniform bytes are fine for tests
    that don't care about the XOR roundtrip — the dedicated XOR test uses
    non-uniform bytes to prove the split isn't degenerate."""
    return bytearray(b"A" * 44)


class _FakeProc:
    """Stand-in for ``subprocess.Popen`` used by env-capture tests.

    Models a still-running child: ``poll()`` returns None, ``kill()``/``wait()``
    are no-ops. Stdout/stderr are None so the production failure path's
    ``communicate()`` is bypassed (it isn't reached on the success path
    that env-capture tests exercise).
    """

    def __init__(self, pid: int = 12345) -> None:
        self.pid = pid
        self.stdout: object = None
        self.stderr: object = None

    def poll(self) -> int | None:
        return None

    def kill(self) -> None:  # pragma: no cover — never reached on success path
        pass

    def wait(self, timeout: float | None = None) -> int:  # pragma: no cover
        return 0


class _FakeShutdownProc:
    """Stand-in Popen for Phase-C shutdown tests.

    Models a child that responds to ``terminate()``/``kill()`` deterministically
    without a real process. Set ``terminate_hangs=True`` to simulate a child
    that ignores SIGTERM — ``wait()`` will then raise ``TimeoutExpired`` until
    ``kill()`` is called, after which it returns 0.
    """

    def __init__(self, pid: int = 12345, terminate_hangs: bool = False) -> None:
        self.pid = pid
        self.stdout: object = None
        self.stderr: object = None
        self._terminate_hangs = terminate_hangs
        self._exit_code: int | None = None
        self.terminate_called = False
        self.kill_called = False
        self.wait_timeouts: list[float | None] = []

    def poll(self) -> int | None:
        return self._exit_code

    def terminate(self) -> None:
        self.terminate_called = True
        if not self._terminate_hangs:
            self._exit_code = 0

    def kill(self) -> None:
        self.kill_called = True
        self._exit_code = -9

    def wait(self, timeout: float | None = None) -> int:
        self.wait_timeouts.append(timeout)
        if self._exit_code is None:
            # Caller is doing the SIGTERM grace wait but the child is hung.
            raise subprocess.TimeoutExpired(cmd="fake-sidecar", timeout=timeout or 0.0)
        return self._exit_code


def _capturing_popen(captured: dict[str, dict[str, str]], pid: int = 12345) -> object:
    """Return a Popen-shaped callable that records ``kwargs['env']``."""

    def _fake_popen(*args: object, **kwargs: object) -> _FakeProc:
        captured["env"] = dict(kwargs["env"])  # type: ignore[arg-type]
        return _FakeProc(pid=pid)

    return _fake_popen


def test_split_to_tmpfs_creates_two_shares(home: Path, key: bytearray) -> None:
    shares = split_to_tmpfs(key, home)
    assert shares.share_a_path.exists()
    assert shares.share_b_path.exists()


def test_split_to_tmpfs_xor_yields_original_key(home: Path) -> None:
    # Non-uniform bytes so XOR roundtrip isn't degenerate over a single
    # repeated byte (which would pass even if shard_b were a constant).
    key = bytearray(b"fernet-key-44-bytes-urlsafe-base64-here-padd")
    assert len(key) == 44
    shares = split_to_tmpfs(key, home)
    a = shares.share_a_path.read_bytes()
    b = shares.share_b_path.read_bytes()
    assert len(a) == len(key)
    assert len(b) == len(key)
    reconstructed = bytes(x ^ y for x, y in zip(a, b, strict=True))
    assert reconstructed == bytes(key)


def test_share_files_have_0600_perms_and_owner_uid(home: Path, key: bytearray) -> None:
    shares = split_to_tmpfs(key, home)
    for p in (shares.share_a_path, shares.share_b_path):
        st = p.stat()
        assert stat.S_IMODE(st.st_mode) == 0o600, f"{p} mode={oct(st.st_mode)}"
        assert st.st_uid == os.getuid()


def test_share_dir_is_per_pid_under_home(home: Path, key: bytearray) -> None:
    shares = split_to_tmpfs(key, home)
    expected = home / "run" / str(os.getpid())
    assert shares.run_dir == expected
    assert shares.run_dir.exists()
    assert stat.S_IMODE(shares.run_dir.stat().st_mode) == 0o700


def test_split_to_tmpfs_does_not_log_share_bytes(
    home: Path, key: bytearray, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="worthless")
    shares: ShareFiles = split_to_tmpfs(key, home)
    a_hex = shares.share_a_path.read_bytes().hex()
    b_hex = shares.share_b_path.read_bytes().hex()
    for record in caplog.records:
        msg = record.getMessage()
        assert a_hex not in msg, f"share_a hex leaked: {msg!r}"
        assert b_hex not in msg, f"share_b hex leaked: {msg!r}"
        for arg in record.args or ():
            arg_str = repr(arg)
            assert a_hex not in arg_str
            assert b_hex not in arg_str


# ---------------------------------------------------------------------------
# Phase B: spawn_sidecar tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_spawn_sidecar_returns_handle_with_running_process(home: Path) -> None:
    """B1: spawn a real sidecar and verify the handle reflects a live process.

    Uses a real Fernet key (not the uniform-byte ``key`` fixture) because the
    sidecar's FernetBackend validates the reconstructed key on startup and
    will exit rc=1 on a malformed key. Path is forced under /tmp so AF_UNIX's
    104-byte sun_path limit on macOS doesn't trip.
    """
    # Force /tmp-rooted home so socket path stays under 104 bytes on macOS.
    short_home = Path(tempfile.mkdtemp(prefix="w-", dir="/tmp")) / ".worthless"
    short_home.mkdir()
    fernet_key = bytearray(base64.urlsafe_b64encode(secrets.token_bytes(32)))
    shares = split_to_tmpfs(fernet_key, short_home)
    socket_path = shares.run_dir / "sc.sock"
    if len(str(socket_path)) >= 104:
        pytest.skip(f"tmp path too long for AF_UNIX (len={len(str(socket_path))} >= 104)")
    handle = spawn_sidecar(socket_path, shares, allowed_uid=os.getuid())
    try:
        assert isinstance(handle, SidecarHandle)
        assert handle.proc.poll() is None  # still running
        assert handle.socket_path.exists()
        assert handle.allowed_uid == os.getuid()
        assert handle.shares is shares
    finally:
        handle.proc.terminate()
        try:
            handle.proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            handle.proc.kill()
            handle.proc.wait(timeout=2.0)
        if handle.socket_path.exists():
            try:
                handle.socket_path.unlink()
            except OSError:
                pass


def test_spawn_sidecar_passes_current_uid_in_env(home: Path, key: bytearray) -> None:
    """B2: env carries the caller-provided uid as the sidecar's allowlist."""
    shares = split_to_tmpfs(key, home)
    socket_path = shares.run_dir / "sidecar.sock"
    captured: dict[str, dict[str, str]] = {}

    with (
        patch(
            "worthless.cli.sidecar_lifecycle.subprocess.Popen",
            _capturing_popen(captured),
        ),
        patch(
            "worthless.cli.sidecar_lifecycle._wait_for_ready",
            return_value=True,
        ),
    ):
        handle = spawn_sidecar(socket_path, shares, allowed_uid=os.getuid())

    assert handle.allowed_uid == os.getuid()
    env = captured["env"]
    assert env["WORTHLESS_SIDECAR_ALLOWED_UID"] == str(os.getuid())
    assert env["WORTHLESS_SIDECAR_SOCKET"] == str(socket_path)
    assert env["WORTHLESS_SIDECAR_SHARE_A"] == str(shares.share_a_path)
    assert env["WORTHLESS_SIDECAR_SHARE_B"] == str(shares.share_b_path)


def test_spawn_sidecar_times_out_with_wrtls_113(home: Path, key: bytearray) -> None:
    """B3: a non-sidecar child that never binds raises WRTLS-113 and is reaped."""
    shares = split_to_tmpfs(key, home)
    socket_path = shares.run_dir / "sidecar.sock"

    real_popen = subprocess.Popen
    spawned: list[subprocess.Popen[bytes]] = []

    def _bogus_popen(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        # Replace the sidecar invocation with a sleeper that won't bind.
        proc = real_popen(  # noqa: S603 — args are static, no shell
            [sys.executable, "-c", "import time; time.sleep(30)"],
            env=kwargs.get("env"),
            stdout=kwargs.get("stdout"),
            stderr=kwargs.get("stderr"),
        )
        spawned.append(proc)
        return proc

    with patch("worthless.cli.sidecar_lifecycle.subprocess.Popen", _bogus_popen):
        with pytest.raises(WorthlessError) as exc_info:
            spawn_sidecar(
                socket_path,
                shares,
                allowed_uid=os.getuid(),
                ready_timeout=0.5,
            )

    assert exc_info.value.code == ErrorCode.SIDECAR_NOT_READY
    assert exc_info.value.code.value == 113
    # The bogus child must be reaped, not orphaned.
    assert spawned, "Popen replacement was never invoked"
    proc = spawned[0]
    assert proc.poll() is not None, "bogus child still running after timeout"


def test_spawn_sidecar_passes_drain_timeout_and_log_level(home: Path, key: bytearray) -> None:
    """B4: drain_timeout default and WARNING log level land in the env."""
    shares = split_to_tmpfs(key, home)
    socket_path = shares.run_dir / "sidecar.sock"
    captured: dict[str, dict[str, str]] = {}

    with (
        patch(
            "worthless.cli.sidecar_lifecycle.subprocess.Popen",
            _capturing_popen(captured, pid=67890),
        ),
        patch(
            "worthless.cli.sidecar_lifecycle._wait_for_ready",
            return_value=True,
        ),
    ):
        spawn_sidecar(socket_path, shares, allowed_uid=os.getuid())

    env = captured["env"]
    assert env["WORTHLESS_SIDECAR_DRAIN_TIMEOUT"] == "5.0"
    assert env["WORTHLESS_LOG_LEVEL"] == "WARNING"


# ---------------------------------------------------------------------------
# Phase C: shutdown_sidecar tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_shutdown_sidecar_terminates_and_unlinks() -> None:
    """C1: SIGTERM the sidecar, then verify all on-disk artifacts are gone."""
    short_home = Path(tempfile.mkdtemp(prefix="w-", dir="/tmp")) / ".worthless"
    short_home.mkdir()
    fernet_key = bytearray(base64.urlsafe_b64encode(secrets.token_bytes(32)))
    shares = split_to_tmpfs(fernet_key, short_home)
    socket_path = shares.run_dir / "sc.sock"
    if len(str(socket_path)) >= 104:
        pytest.skip(f"tmp path too long for AF_UNIX (len={len(str(socket_path))} >= 104)")
    handle = spawn_sidecar(socket_path, shares, allowed_uid=os.getuid())

    shutdown_sidecar(handle)

    assert handle.proc.poll() is not None, "process still running after shutdown"
    assert not handle.shares.share_a_path.exists()
    assert not handle.shares.share_b_path.exists()
    assert not handle.socket_path.exists()
    assert not handle.shares.run_dir.exists()


def test_shutdown_sidecar_sigkills_after_grace_window(home: Path, key: bytearray) -> None:
    """C2: a child that ignores SIGTERM is escalated to SIGKILL."""
    shares = split_to_tmpfs(key, home)
    handle = SidecarHandle(
        proc=_FakeShutdownProc(terminate_hangs=True),  # type: ignore[arg-type]
        socket_path=shares.run_dir / "sc.sock",
        shares=shares,
        allowed_uid=os.getuid(),
        drain_timeout=0.25,
    )
    shutdown_sidecar(handle)
    assert handle.proc.terminate_called  # type: ignore[attr-defined]
    assert handle.proc.kill_called  # type: ignore[attr-defined]
    assert handle.proc.wait_timeouts[0] == 0.25  # type: ignore[attr-defined]
    # SR-02 must hold even when the SIGKILL leg fires — zeroing happens after
    # the kill, not on the graceful-terminate branch only.
    assert all(b == 0 for b in handle.shares.shard_a)
    assert all(b == 0 for b in handle.shares.shard_b)


def test_shutdown_sidecar_zeroes_share_bytearrays(home: Path, key: bytearray) -> None:
    """C3 (SR-02): both shard bytearrays are all-zero after shutdown."""
    shares = split_to_tmpfs(key, home)
    handle = SidecarHandle(
        proc=_FakeShutdownProc(),  # type: ignore[arg-type]
        socket_path=shares.run_dir / "sc.sock",
        shares=shares,
        allowed_uid=os.getuid(),
        drain_timeout=5.0,
    )
    # Pre-shutdown sanity: both shards carry non-zero content.
    assert any(b != 0 for b in handle.shares.shard_a)
    assert any(b != 0 for b in handle.shares.shard_b)

    shutdown_sidecar(handle)

    assert all(b == 0 for b in handle.shares.shard_a), "shard_a not zeroed"
    assert all(b == 0 for b in handle.shares.shard_b), "shard_b not zeroed"


def test_shutdown_sidecar_is_idempotent(home: Path, key: bytearray) -> None:
    """C4: calling shutdown twice is safe — files already gone, mem already zeroed."""
    shares = split_to_tmpfs(key, home)
    handle = SidecarHandle(
        proc=_FakeShutdownProc(),  # type: ignore[arg-type]
        socket_path=shares.run_dir / "sc.sock",
        shares=shares,
        allowed_uid=os.getuid(),
        drain_timeout=5.0,
    )
    shutdown_sidecar(handle)
    # Second call must not raise.
    shutdown_sidecar(handle)


# ---------------------------------------------------------------------------
# CodeRabbit regression guards (PR #116 review)
# ---------------------------------------------------------------------------


def test_spawn_sidecar_unlinks_stale_socket_before_spawn(home: Path, key: bytearray) -> None:
    """Stale socket inode at the target path must be removed before spawn —
    otherwise ``_wait_for_ready`` returns True instantly on the leftover
    file and the new sidecar's bind would race against it.
    """
    shares = split_to_tmpfs(key, home)
    socket_path = shares.run_dir / "sidecar.sock"
    socket_path.write_bytes(b"")  # pre-create the stale inode
    captured: dict[str, dict[str, str]] = {}

    with (
        patch(
            "worthless.cli.sidecar_lifecycle.subprocess.Popen",
            _capturing_popen(captured),
        ),
        patch(
            "worthless.cli.sidecar_lifecycle._wait_for_ready",
            return_value=True,
        ),
    ):
        # Should NOT raise — the stale inode is unlinked, then spawn proceeds.
        spawn_sidecar(socket_path, shares, allowed_uid=os.getuid())

    # Popen was reached → unlink succeeded.
    assert "env" in captured


def test_split_to_tmpfs_cleans_up_on_write_failure(
    home: Path, key: bytearray, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If ``_write_share`` raises mid-sequence (e.g., disk-full on share_b),
    no half-state must survive: share_a is unlinked, run dir is removed,
    and the original exception propagates.
    """
    call_count = {"n": 0}
    real_write_share = importlib.import_module("worthless.cli.sidecar_lifecycle")._write_share

    def _flaky_write(path: Path, data: bytearray) -> None:
        call_count["n"] += 1
        if call_count["n"] == 1:
            real_write_share(path, data)
            return
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("worthless.cli.sidecar_lifecycle._write_share", _flaky_write)

    with pytest.raises(OSError, match="No space left"):
        split_to_tmpfs(key, home)

    run_dir = home / "run" / str(os.getpid())
    assert not run_dir.exists(), "run_dir not cleaned up after partial-write failure"


# ---------------------------------------------------------------------------
# QA #2 — spawn_sidecar must reap the child if interrupted during
# _wait_for_ready (e.g., SIGINT mid-poll, BaseException from any source)
# ---------------------------------------------------------------------------


def test_spawn_sidecar_reaps_child_on_keyboardinterrupt_during_wait(
    home: Path, key: bytearray
) -> None:
    """Regression guard for QA #2: if ``_wait_for_ready`` raises (e.g.,
    SIGINT mid-poll causes ``time.sleep`` to raise ``KeyboardInterrupt``),
    the child Popen we just created must be killed and reaped before
    propagating — otherwise the exception leaves an orphan sidecar PID
    that the caller cannot clean up (caller's ``handle is None`` branch
    only knows about share files on disk).
    """
    shares = split_to_tmpfs(key, home)
    socket_path = shares.run_dir / "sidecar.sock"

    # Track kill + communicate on a fake Popen.
    class _SpawnFakeProc:
        def __init__(self) -> None:
            self.pid = 33333
            self.stdout: object = None
            self.stderr: object = None
            self._exit_code: int | None = None
            self.kill_called = False
            self.communicate_called = False

        def poll(self) -> int | None:
            return self._exit_code

        def kill(self) -> None:
            self.kill_called = True
            self._exit_code = -9

        def wait(self, timeout: float | None = None) -> int:  # pragma: no cover
            return self._exit_code or 0

        def communicate(
            self, input: object = None, timeout: float | None = None
        ) -> tuple[bytes, bytes]:
            self.communicate_called = True
            return (b"", b"")

    fake_proc = _SpawnFakeProc()

    def _interrupt_during_wait(*_args: object, **_kwargs: object) -> bool:
        raise KeyboardInterrupt

    with (
        patch(
            "worthless.cli.sidecar_lifecycle.subprocess.Popen",
            return_value=fake_proc,
        ),
        patch(
            "worthless.cli.sidecar_lifecycle._wait_for_ready",
            side_effect=_interrupt_during_wait,
        ),
    ):
        with pytest.raises(KeyboardInterrupt):
            spawn_sidecar(socket_path, shares, allowed_uid=os.getuid())

    assert fake_proc.kill_called, (
        "spawn_sidecar leaked the child Popen on KeyboardInterrupt during wait — "
        "kill() was never called, child PID is now an orphan"
    )
    assert fake_proc.communicate_called, (
        "spawn_sidecar killed the child but did not communicate() to drain "
        "pipes / reap — could leave a zombie"
    )


# ---------------------------------------------------------------------------
# QA #1 (CRITICAL) — bind/listen race
#
# The sidecar uses asyncio.start_unix_server which calls bind() and listen()
# as separate syscalls. Between them, socket_path.exists() returns True but
# connect() fails with ECONNREFUSED. Pre-fix _wait_for_ready returns True at
# the bind moment — proxy's first IPC call can hit ECONNREFUSED. Fix: use a
# connect() probe so we only return True after listen() has run.
#
# This test reproduces the race deterministically by holding the bind/listen
# window open for 500ms in a worker thread, then verifying _wait_for_ready
# does NOT return until listen() actually runs.
# ---------------------------------------------------------------------------


def test_wait_for_ready_blocks_until_listen_not_just_bind() -> None:
    """Real-OS race repro for QA #1: the bind/listen window. Holds the
    window open for 500ms in a worker thread; ``_wait_for_ready`` must
    NOT return True until the worker calls ``listen()``.
    """
    import socket as socket_mod
    import tempfile as _tempfile
    import threading
    import time
    from unittest.mock import MagicMock as _MagicMock

    from worthless.cli.sidecar_lifecycle import _wait_for_ready

    # /tmp-rooted to dodge AF_UNIX 104-byte sun_path limit on macOS.
    tmp_dir = Path(_tempfile.mkdtemp(prefix="wlr-", dir="/tmp"))  # noqa: S108
    sock_path = tmp_dir / "race.sock"
    bind_done = threading.Event()
    listen_done = threading.Event()
    exit_signal = threading.Event()

    def fake_sidecar() -> None:
        s = socket_mod.socket(socket_mod.AF_UNIX, socket_mod.SOCK_STREAM)
        try:
            s.bind(str(sock_path))
            bind_done.set()
            # Hold the bind/listen window open. Pre-fix _wait_for_ready
            # would return True during this window because exists() is True
            # but connect() would fail with ECONNREFUSED.
            time.sleep(0.5)
            s.listen(5)
            listen_done.set()
            # Stay alive long enough for connect() probes to succeed.
            exit_signal.wait(timeout=3.0)
        finally:
            try:
                s.close()
            except OSError:
                pass
            try:
                sock_path.unlink()
            except OSError:
                pass

    thread = threading.Thread(target=fake_sidecar, daemon=True)
    thread.start()

    try:
        # Wait for bind so the socket inode exists.
        assert bind_done.wait(timeout=2.0), "fake sidecar never bound"
        assert sock_path.exists(), "socket inode missing post-bind"
        assert not listen_done.is_set(), "listen() already fired — race window not held"

        # Now call _wait_for_ready. Must block until listen() actually runs.
        fake_proc = _MagicMock()
        fake_proc.poll.return_value = None

        start = time.monotonic()
        ready = _wait_for_ready(fake_proc, sock_path, deadline=start + 2.0)
        elapsed = time.monotonic() - start

        assert ready is True, "_wait_for_ready timed out"
        # Pre-fix: returns within ~50ms of bind (one poll tick) — race is open.
        # Post-fix: connect() probe waits for listen() — elapsed >= 0.4s.
        assert elapsed >= 0.4, (
            f"_wait_for_ready returned at {elapsed:.3f}s, before listen() "
            "fired at 0.5s. The bind/listen race is OPEN — proxy first "
            "IPC call would hit ECONNREFUSED."
        )
        assert listen_done.is_set(), "_wait_for_ready returned but listen() never ran"
    finally:
        exit_signal.set()
        thread.join(timeout=3.0)
