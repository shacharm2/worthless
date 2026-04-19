"""End-to-end checks that a second ``worthless up`` detects the first daemon.

The daemon writes the listening process's self-reported PID to the PID file,
so a second invocation reads a live PID and refuses with "already running".
Previously the CLI wrote whatever ``subprocess.Popen(...).pid`` returned,
which could drift from the real uvicorn process and cause duplicate proxies
to spawn on the same port (and orphan uvicorn on ``worthless down``).

These tests spawn real subprocesses to lock that behavior end-to-end. The
unit-level coverage of the polling helper lives in ``test_health_polling.py``.
"""

from __future__ import annotations

import os
import signal
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import psutil
import pytest

from worthless.cli.bootstrap import ensure_home
from worthless.cli.process import check_pid, pid_path, poll_health, read_pid


pytestmark = [pytest.mark.integration, pytest.mark.timeout(30)]


def _ephemeral_port() -> int:
    # Same collision-resistant pattern used in tests/test_cli_down.py.
    return 18900 + os.getpid() % 100


def _kill_pidfile(pf: Path) -> None:
    """Best-effort cleanup: signal the process tree, unlink the pid file."""
    if not pf.exists():
        return
    info = read_pid(pf)
    if info is not None:
        pid = info[0]
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
    pf.unlink(missing_ok=True)


@pytest.fixture()
def cli_env(tmp_path: Path) -> dict[str, str]:
    """Isolated WORTHLESS_HOME env for subprocess invocations."""
    home = ensure_home(tmp_path / ".worthless")
    return {
        **os.environ,
        "WORTHLESS_HOME": str(home.base_dir),
    }


@pytest.fixture()
def cli_home(cli_env: dict[str, str]) -> Path:
    return Path(cli_env["WORTHLESS_HOME"])


@pytest.fixture()
def worthless_bin() -> str:
    found = shutil.which("worthless")
    if found is None:
        pytest.skip("worthless CLI not found on PATH")
    return found


class TestDuplicateProxyDetection:
    """WOR-228 — second `worthless up` must detect the first and refuse."""

    def test_second_up_rejected_when_first_alive(
        self,
        worthless_bin: str,
        cli_env: dict[str, str],
        cli_home: Path,
    ) -> None:
        port = _ephemeral_port()
        first = subprocess.run(
            [worthless_bin, "up", "--daemon", "--port", str(port)],
            env=cli_env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        pf = pid_path(ensure_home(cli_home))
        try:
            if first.returncode != 0:
                # The binary is on PATH (checked in fixture) — if it can't
                # start a daemon here, that's a real regression, not an env
                # issue. Fail loudly rather than mask it as a skip.
                pytest.fail(f"first daemon failed to start: {first.stderr}")
            # Close the race: don't fire the second `up` until the proxy
            # actually binds the port. subprocess.run returning is not
            # sufficient — the parent may detach before uvicorn is ready.
            assert poll_health(port, timeout=10.0), "first daemon never became healthy"

            second = subprocess.run(
                [worthless_bin, "up", "--daemon", "--port", str(port)],
                env=cli_env,
                capture_output=True,
                text=True,
                timeout=15,
            )
            assert second.returncode != 0, (
                f"second up should fail — got {second.returncode}\n"
                f"stdout: {second.stdout}\nstderr: {second.stderr}"
            )
            combined = (second.stdout + second.stderr).lower()
            assert "already running" in combined, (
                f"expected 'already running' message, got:\n{combined}"
            )
        finally:
            _kill_pidfile(pf)

    def test_second_up_clean_after_crash(
        self,
        worthless_bin: str,
        cli_env: dict[str, str],
        cli_home: Path,
    ) -> None:
        """After the proxy crashes the next `up` must start cleanly."""
        port = _ephemeral_port()
        first = subprocess.run(
            [worthless_bin, "up", "--daemon", "--port", str(port)],
            env=cli_env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        pf = pid_path(ensure_home(cli_home))
        try:
            if first.returncode != 0:
                # The binary is on PATH (checked in fixture) — if it can't
                # start a daemon here, that's a real regression, not an env
                # issue. Fail loudly rather than mask it as a skip.
                pytest.fail(f"first daemon failed to start: {first.stderr}")
            assert poll_health(port, timeout=10.0)
            info = read_pid(pf)
            assert info is not None
            first_pid = info[0]

            # Kill the real listening process — simulating an unclean crash.
            try:
                psutil.Process(first_pid).kill()
            except psutil.NoSuchProcess:
                pytest.skip("daemon vanished before we could kill it")
            deadline = time.monotonic() + 5.0
            while check_pid(first_pid) and time.monotonic() < deadline:
                time.sleep(0.1)
            assert not check_pid(first_pid), "crashed process still alive"

            second = subprocess.run(
                [worthless_bin, "up", "--daemon", "--port", str(port)],
                env=cli_env,
                capture_output=True,
                text=True,
                timeout=15,
            )
            assert second.returncode == 0, (
                f"second up should succeed after crash — got {second.returncode}\n"
                f"stdout: {second.stdout}\nstderr: {second.stderr}"
            )
            assert poll_health(port, timeout=10.0)
            info_after = read_pid(pf)
            assert info_after is not None
            assert info_after[0] != first_pid, "new daemon reused crashed PID"
        finally:
            _kill_pidfile(pf)

    def test_pidfile_contains_listening_process(
        self,
        worthless_bin: str,
        cli_env: dict[str, str],
        cli_home: Path,
    ) -> None:
        """The PID in the file must belong to a process actually bound to the port.

        Directly pins the fix: writing `proc.pid` (the wrapper/shim) could record
        a PID that does not own the listening socket. Writing the self-reported
        PID guarantees this invariant.
        """
        port = _ephemeral_port()
        first = subprocess.run(
            [worthless_bin, "up", "--daemon", "--port", str(port)],
            env=cli_env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        pf = pid_path(ensure_home(cli_home))
        try:
            if first.returncode != 0:
                # The binary is on PATH (checked in fixture) — if it can't
                # start a daemon here, that's a real regression, not an env
                # issue. Fail loudly rather than mask it as a skip.
                pytest.fail(f"first daemon failed to start: {first.stderr}")
            assert poll_health(port, timeout=10.0)

            info = read_pid(pf)
            assert info is not None
            pid = info[0]

            # Walk the recorded process + its descendants; one of them must
            # own a LISTEN socket on `port`. Writing the shim PID would
            # typically put the listener outside this subtree on some
            # platforms — the self-reported PID always matches.
            try:
                proc = psutil.Process(pid)
            except psutil.NoSuchProcess:
                pytest.fail("PID file references a dead process")

            candidates = [proc, *proc.children(recursive=True)]
            listening = False
            for candidate in candidates:
                try:
                    for conn in candidate.net_connections(kind="tcp"):
                        if (
                            conn.status == psutil.CONN_LISTEN
                            and conn.laddr
                            and conn.laddr.port == port
                        ):
                            listening = True
                            break
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    continue
                if listening:
                    break
            assert listening, (
                f"PID {pid} (and descendants) is not the process bound to "
                f"port {port} — WOR-228 regression"
            )
        finally:
            _kill_pidfile(pf)

    def test_down_leaves_no_listener(
        self,
        worthless_bin: str,
        cli_env: dict[str, str],
        cli_home: Path,
    ) -> None:
        """``worthless down`` must clear the listening socket.

        Before the fix, ``down`` called ``kill_tree(shim_pid)`` against a
        dead shim PID and returned successfully while leaving uvicorn
        orphaned on the port. A successful ``down`` exit code is not
        enough — verify the port is actually free.
        """
        port = _ephemeral_port()
        first = subprocess.run(
            [worthless_bin, "up", "--daemon", "--port", str(port)],
            env=cli_env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        pf = pid_path(ensure_home(cli_home))
        try:
            if first.returncode != 0:
                pytest.fail(f"first daemon failed to start: {first.stderr}")
            assert poll_health(port, timeout=10.0)

            down = subprocess.run(
                [worthless_bin, "down"],
                env=cli_env,
                capture_output=True,
                text=True,
                timeout=15,
            )
            assert down.returncode == 0, (
                f"down should succeed — got {down.returncode}\n"
                f"stdout: {down.stdout}\nstderr: {down.stderr}"
            )

            # Poll briefly — kill_tree signals are async. Use HTTP because
            # macOS denies unprivileged callers of psutil.net_connections()
            # without a PID argument. A ConnectError means the socket is
            # gone, which is exactly what we care about.
            deadline = time.monotonic() + 5.0
            still_listening = True
            while time.monotonic() < deadline:
                try:
                    httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=0.5)
                except (httpx.ConnectError, httpx.ConnectTimeout):
                    still_listening = False
                    break
                time.sleep(0.1)
            assert not still_listening, (
                f"after `down`, something is still serving /healthz on port {port}"
            )
        finally:
            _kill_pidfile(pf)

    def test_healthz_pid_matches_spawn_pid_under_default_cmd(
        self,
        worthless_bin: str,
        cli_env: dict[str, str],
        cli_home: Path,
    ) -> None:
        """Tripwire: under the current single-process uvicorn launch, the
        self-reported PID and the Popen PID must be identical.

        The fix assumes ``os.getpid()`` inside the healthz handler equals
        the process actually bound to the port. That holds for a
        single-process uvicorn run. The day someone adds ``--workers N``
        or ``--reload`` to ``proxy_cmd``, the assumption breaks and this
        test fails — forcing a rethink of ``poll_health_pid``'s authority
        before the behaviour drifts.
        """
        port = _ephemeral_port()
        first = subprocess.run(
            [worthless_bin, "up", "--daemon", "--port", str(port)],
            env=cli_env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        pf = pid_path(ensure_home(cli_home))
        try:
            if first.returncode != 0:
                pytest.fail(f"first daemon failed to start: {first.stderr}")
            assert poll_health(port, timeout=10.0)

            info = read_pid(pf)
            assert info is not None
            recorded_pid = info[0]

            resp = httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=2.0)
            resp.raise_for_status()
            healthz_pid = resp.json()["pid"]

            assert recorded_pid == healthz_pid, (
                "pidfile PID must equal /healthz PID under the current "
                "single-process proxy_cmd; a mismatch means `proxy_cmd` "
                "now spawns workers/reloader/etc. and `poll_health_pid` "
                "authority needs revisiting"
            )
        finally:
            _kill_pidfile(pf)
