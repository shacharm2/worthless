"""Tests for the ``worthless wrap`` command."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from worthless.cli.app import app
from worthless.cli.commands.wrap import (
    _build_child_env,
    _cleanup_proxy,
    _list_enrolled_providers,
    _run_child_and_wait,
)
from worthless.cli.process import create_liveness_pipe

runner = CliRunner()


class TestWrapEnvInjection:
    """wrap injects BASE_URL env vars for enrolled providers."""

    def test_child_env_has_base_url(self):
        """wrap should inject OPENAI_BASE_URL into child environment."""
        child_env = _build_child_env(port=9999, providers=["openai"])
        assert child_env["OPENAI_BASE_URL"] == "http://127.0.0.1:9999"

    def test_child_env_anthropic(self):
        """wrap should inject ANTHROPIC_BASE_URL for anthropic provider."""
        child_env = _build_child_env(port=8888, providers=["anthropic"])
        assert child_env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8888"

    def test_child_env_multiple_providers(self):
        """wrap injects env vars for all enrolled providers."""
        child_env = _build_child_env(port=7777, providers=["openai", "anthropic"])
        assert child_env["OPENAI_BASE_URL"] == "http://127.0.0.1:7777"
        assert child_env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:7777"

    def test_child_env_no_session_token(self):
        """Session token should not be in child env (dead code removed)."""
        child_env = _build_child_env(port=9999, providers=["openai"])
        assert "WORTHLESS_SESSION_TOKEN" not in child_env


class TestWrapExitCode:
    """wrap mirrors child exit code."""

    @pytest.mark.integration
    @pytest.mark.timeout(30)
    def test_mirrors_child_exit_code(self, tmp_path: Path):
        """wrap should exit with the child's exit code."""
        proc = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.exit(42)"],
            start_new_session=True,
        )
        code = _run_child_and_wait(proc)
        assert code == 42

    @pytest.mark.integration
    @pytest.mark.timeout(30)
    def test_mirrors_zero_exit(self, tmp_path: Path):
        """wrap should exit 0 when child exits 0."""
        proc = subprocess.Popen(
            [sys.executable, "-c", "pass"],
            start_new_session=True,
        )
        code = _run_child_and_wait(proc)
        assert code == 0


class TestWrapNoKeys:
    """wrap errors when no keys are enrolled."""

    def test_no_enrolled_keys_raises(self, tmp_path: Path):
        from worthless.cli.bootstrap import ensure_home

        home = ensure_home(tmp_path / ".worthless")
        providers = _list_enrolled_providers(home)
        assert providers == []


class TestWrapLivenessPipe:
    """wrap creates liveness pipe for proxy death detection."""

    def test_liveness_pipe_created(self):
        read_fd, write_fd = create_liveness_pipe()
        try:
            os.fstat(read_fd)
            os.fstat(write_fd)
        finally:
            os.close(read_fd)
            os.close(write_fd)


class TestWrapSpawnProxyFailure:
    """wrap exits 1 and cleans up FDs when spawn_proxy fails."""

    def test_spawn_failure_exit_code(self, home_with_key, monkeypatch: pytest.MonkeyPatch) -> None:
        """When spawn_proxy raises, wrap exits 1."""

        def _fail(**_kw):
            raise RuntimeError("bind failed")

        monkeypatch.setattr(
            "worthless.cli.commands.wrap.spawn_proxy",
            _fail,
        )
        result = runner.invoke(
            app,
            ["wrap", "--", "echo", "hi"],
            env={"WORTHLESS_HOME": str(home_with_key.base_dir)},
        )
        assert result.exit_code == 1
        assert "proxy" in result.output.lower()


class TestWrapHealthTimeout:
    """wrap cleans up proxy when poll_health times out."""

    def test_health_timeout_cleans_proxy(
        self, home_with_key, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When poll_health returns False, proxy is terminated and exit 1."""
        mock_proxy = MagicMock()
        mock_proxy.poll.return_value = None
        mock_proxy.wait.return_value = 0

        monkeypatch.setattr(
            "worthless.cli.commands.wrap.spawn_proxy",
            lambda **_kw: (mock_proxy, 9999),
        )
        monkeypatch.setattr(
            "worthless.cli.commands.wrap.poll_health",
            lambda *_a, **_kw: False,
        )

        result = runner.invoke(
            app,
            ["wrap", "--", "echo", "hi"],
            env={"WORTHLESS_HOME": str(home_with_key.base_dir)},
        )
        assert result.exit_code == 1
        assert "healthy" in result.output.lower() or "health" in result.output.lower()
        mock_proxy.terminate.assert_called()


class TestWrapChildSpawnFailure:
    """wrap cleans up proxy when child Popen fails."""

    def test_child_spawn_failure_cleans_proxy(
        self, home_with_key, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When child Popen raises, proxy is cleaned up and exit 1."""
        mock_proxy = MagicMock()
        mock_proxy.poll.return_value = None
        mock_proxy.wait.return_value = 0

        monkeypatch.setattr(
            "worthless.cli.commands.wrap.spawn_proxy",
            lambda **_kw: (mock_proxy, 9999),
        )
        monkeypatch.setattr(
            "worthless.cli.commands.wrap.poll_health",
            lambda *_a, **_kw: True,
        )

        def _fail_popen(*_a, **_kw):
            raise FileNotFoundError("No such file")

        monkeypatch.setattr("subprocess.Popen", _fail_popen)

        result = runner.invoke(
            app,
            ["wrap", "--", "nonexistent-binary"],
            env={"WORTHLESS_HOME": str(home_with_key.base_dir)},
        )
        assert result.exit_code == 1
        assert "child" in result.output.lower() or "start" in result.output.lower()
        mock_proxy.terminate.assert_called()


class TestCleanupProxy:
    """_cleanup_proxy handles already-dead and stuck processes."""

    def test_cleanup_already_dead(self) -> None:
        """No error when proxy is already dead."""
        mock = MagicMock()
        mock.poll.return_value = 0
        _cleanup_proxy(mock)
        mock.terminate.assert_not_called()

    def test_cleanup_timeout_kills(self) -> None:
        """Proxy that doesn't stop gets SIGKILL."""
        mock = MagicMock()
        mock.poll.return_value = None
        # First wait (terminate) times out, second wait (kill) succeeds
        mock.wait.side_effect = [
            subprocess.TimeoutExpired(cmd="proxy", timeout=5),
            None,
        ]
        _cleanup_proxy(mock, timeout=0.01)
        mock.kill.assert_called()


class TestWrapProxyCrashMidSession:
    """wrap warns on stderr when proxy dies while child is still running."""

    def test_proxy_crash_warns_and_child_continues(
        self, home_with_key, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When proxy crashes mid-session, warning is emitted and exit code is 0."""
        proxy_waited = threading.Event()
        warning_written = threading.Event()
        captured_messages: list[str] = []

        # -- Mock proxy: .wait() returns immediately (simulating crash)
        mock_proxy = MagicMock()
        mock_proxy.pid = 99999

        def proxy_wait(**_kw):
            proxy_waited.set()
            return 0

        mock_proxy.wait.side_effect = proxy_wait
        mock_proxy.poll.return_value = 0  # already dead after crash
        mock_proxy.returncode = 0

        # -- Mock child: .poll() returns None (alive) then 0 (done)
        mock_child = MagicMock()
        mock_child.pid = 99998
        poll_values = iter([None, None, None, 0, 0, 0])
        mock_child.poll.side_effect = lambda: next(poll_values, 0)

        def child_wait(**_kw):
            # Let the watcher thread detect proxy crash first
            proxy_waited.wait(timeout=5)
            time.sleep(0.15)
            mock_child.returncode = 0
            return 0

        mock_child.wait.side_effect = child_wait
        mock_child.returncode = 0

        # Replace the sys module reference inside wrap so _watch_proxy
        # writes to our capturing stderr, not the real one (which CliRunner
        # may have replaced).
        import worthless.cli.commands.wrap as wrap_mod

        fake_sys = ModuleType("fake_sys")
        # Copy all attributes from real sys
        for attr in dir(sys):
            try:
                setattr(fake_sys, attr, getattr(sys, attr))
            except (AttributeError, TypeError):
                pass

        class _CapturingStderr:
            def write(self, msg: str) -> int:
                captured_messages.append(msg)
                if "proxy crashed" in msg:
                    warning_written.set()
                return len(msg)

            def flush(self) -> None:
                pass

        fake_sys.stderr = _CapturingStderr()
        monkeypatch.setattr(wrap_mod, "sys", fake_sys)

        # Patch spawn_proxy -> returns mock proxy
        monkeypatch.setattr(
            wrap_mod,
            "spawn_proxy",
            lambda **_kw: (mock_proxy, 9999),
        )
        # Patch poll_health -> healthy
        monkeypatch.setattr(
            wrap_mod,
            "poll_health",
            lambda *_a, **_kw: True,
        )
        # Patch forward_signals -> no-op (can't killpg mock PIDs)
        monkeypatch.setattr(
            wrap_mod,
            "forward_signals",
            lambda **_kw: None,
        )
        # Patch subprocess.Popen -> returns mock child
        monkeypatch.setattr("subprocess.Popen", lambda *_a, **_kw: mock_child)

        result = runner.invoke(
            app,
            ["wrap", "--", "echo", "hi"],
            env={"WORTHLESS_HOME": str(home_with_key.base_dir)},
        )
        # Wait for watcher thread to write the warning
        assert warning_written.wait(timeout=5), "Watcher thread did not emit warning"

        assert result.exit_code == 0
        combined = "".join(captured_messages)
        assert "proxy crashed mid-session" in combined


class TestWrapBootstrapFailure:
    """Error branches for wrap bootstrap failures."""

    def test_wrap_get_home_failure_exits_clean(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """OSError in get_home -> exit_code=1."""

        def _boom():
            raise OSError("permission denied")

        monkeypatch.setattr(
            "worthless.cli.commands.wrap.get_home",
            _boom,
        )

        result = runner.invoke(
            app,
            ["wrap", "--", "echo", "hi"],
            env={"WORTHLESS_HOME": str(tmp_path / "nonexistent")},
        )
        assert result.exit_code == 1

    def test_wrap_liveness_pipe_failure_exits_clean(
        self, home_with_key, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OSError in create_liveness_pipe -> exit_code=1."""

        def _boom():
            raise OSError("too many files")

        monkeypatch.setattr(
            "worthless.cli.commands.wrap.create_liveness_pipe",
            _boom,
        )

        result = runner.invoke(
            app,
            ["wrap", "--", "echo", "hi"],
            env={"WORTHLESS_HOME": str(home_with_key.base_dir)},
        )
        assert result.exit_code == 1


class TestWrapNoEnrolledKeysError:
    """wrap exits 1 when no keys are enrolled (empty providers list)."""

    def test_no_keys_enrolled_error_message(
        self, home_dir, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """wrap with no enrolled keys prints KEY_NOT_FOUND error."""
        result = runner.invoke(
            app,
            ["wrap", "--", "echo", "hi"],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 1
        assert "WRTLS-102" in result.output


class TestCleanupProxyWithWriteFd:
    """_cleanup_proxy closes write_fd when provided."""

    def test_cleanup_closes_write_fd(self) -> None:
        """_cleanup_proxy closes write_fd before terminating proxy."""
        r_fd, w_fd = os.pipe()
        mock = MagicMock()
        mock.poll.return_value = 0  # already dead

        _cleanup_proxy(mock, write_fd=w_fd)

        # write_fd should be closed
        with pytest.raises(OSError):
            os.fstat(w_fd)

        # read_fd still valid, clean up
        os.close(r_fd)

    def test_cleanup_write_fd_already_closed(self) -> None:
        """_cleanup_proxy doesn't error if write_fd is already closed."""
        r_fd, w_fd = os.pipe()
        os.close(w_fd)

        mock = MagicMock()
        mock.poll.return_value = 0

        # Should not raise
        _cleanup_proxy(mock, write_fd=w_fd)
        os.close(r_fd)

    def test_cleanup_live_proxy_with_write_fd(self) -> None:
        """_cleanup_proxy closes write_fd then terminates live proxy."""
        r_fd, w_fd = os.pipe()

        mock = MagicMock()
        mock.poll.return_value = None  # proxy still alive
        mock.wait.return_value = 0

        _cleanup_proxy(mock, write_fd=w_fd, timeout=0.01)

        # write_fd closed
        with pytest.raises(OSError):
            os.fstat(w_fd)
        # proxy terminated
        mock.terminate.assert_called()
        os.close(r_fd)


class TestListEnrolledProvidersNoDB:
    """_list_enrolled_providers returns [] when DB doesn't exist."""

    def test_no_db_returns_empty(self, tmp_path: Path) -> None:
        from worthless.cli.bootstrap import WorthlessHome

        home = WorthlessHome(base_dir=tmp_path / ".worthless")
        providers = _list_enrolled_providers(home)
        assert providers == []


class TestWrapExceptionHandlers:
    """Cover WorthlessError and generic Exception handlers in wrap."""

    def test_worthless_error_in_wrap_exits_clean(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """WorthlessError raised inside wrap -> exit_code=1 with WRTLS."""
        from worthless.cli.errors import ErrorCode, WorthlessError

        def _boom():
            raise WorthlessError(ErrorCode.UNKNOWN, "test error")

        monkeypatch.setattr("worthless.cli.commands.wrap.get_home", _boom)

        result = runner.invoke(
            app,
            ["wrap", "--", "echo", "hi"],
        )
        assert result.exit_code == 1
        assert "WRTLS" in result.output

    def test_generic_exception_in_wrap_exits_clean(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Generic Exception raised inside wrap -> exit_code=1 with WRTLS-199."""

        def _boom():
            raise ValueError("unexpected")

        monkeypatch.setattr("worthless.cli.commands.wrap.get_home", _boom)

        result = runner.invoke(
            app,
            ["wrap", "--", "echo", "hi"],
        )
        assert result.exit_code == 1
        # Generic exceptions are wrapped in WRTLS-199 (UNKNOWN)
        assert "WRTLS-199" in result.output


# ------------------------------------------------------------------
# WOR-73: CliRunner tests for `wrap` command
# ------------------------------------------------------------------


class TestWrapSetsEnvAndRunsCommand:
    """WOR-73: wrap sets env vars and runs child command via CliRunner."""

    def test_wrap_sets_env_and_runs_command(
        self, home_with_key, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CliRunner invokes `wrap`, confirms env vars set and subprocess called."""
        mock_proxy = MagicMock()
        mock_proxy.pid = 55555
        mock_proxy.poll.return_value = None
        mock_proxy.wait.return_value = 0

        mock_child = MagicMock()
        mock_child.pid = 55556
        mock_child.poll.return_value = 0
        mock_child.returncode = 0
        mock_child.wait.return_value = 0

        captured_env: dict[str, str] = {}

        monkeypatch.setattr(
            "worthless.cli.commands.wrap.spawn_proxy",
            lambda **_kw: (mock_proxy, 9999),
        )
        monkeypatch.setattr(
            "worthless.cli.commands.wrap.poll_health",
            lambda *_a, **_kw: True,
        )
        monkeypatch.setattr(
            "worthless.cli.commands.wrap.forward_signals",
            lambda **_kw: None,
        )

        def _capture_popen(*args, **kwargs):
            env = kwargs.get("env", {})
            captured_env.update(env)
            return mock_child

        monkeypatch.setattr("subprocess.Popen", _capture_popen)

        result = runner.invoke(
            app,
            ["wrap", "--", "echo", "hi"],
            env={"WORTHLESS_HOME": str(home_with_key.base_dir)},
        )
        assert result.exit_code == 0, f"wrap failed: {result.output}"

        # Verify env vars were injected for the enrolled provider
        assert "OPENAI_BASE_URL" in captured_env, (
            "wrap should inject OPENAI_BASE_URL for enrolled openai key"
        )
        assert "127.0.0.1" in captured_env["OPENAI_BASE_URL"]


# ------------------------------------------------------------------
# worthless-j3y: Daemon + wrap port conflict coexistence tests
# ------------------------------------------------------------------


class TestWrapDaemonCoexistence:
    """wrap uses ephemeral port and is independent of daemon state."""

    def test_wrap_uses_ephemeral_port_not_daemon_port(
        self, home_with_key, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When a daemon is running, wrap still starts on port=0 (ephemeral).

        Verifies that spawn_proxy is called with port=0 regardless of
        whether a daemon PID file exists with a live process.
        """
        # Create a fake daemon PID file to simulate a running daemon
        pid_file = home_with_key.base_dir / "daemon.pid"
        pid_file.write_text(f"{os.getpid()}:8787")

        captured_kwargs: dict = {}

        def _capture_spawn(**kw):
            captured_kwargs.update(kw)
            mock_proxy = MagicMock()
            mock_proxy.pid = 77777
            mock_proxy.poll.return_value = None
            mock_proxy.wait.return_value = 0
            return (mock_proxy, 11111)

        mock_child = MagicMock()
        mock_child.pid = 77778
        mock_child.poll.return_value = 0
        mock_child.returncode = 0
        mock_child.wait.return_value = 0

        monkeypatch.setattr(
            "worthless.cli.commands.wrap.spawn_proxy",
            _capture_spawn,
        )
        monkeypatch.setattr(
            "worthless.cli.commands.wrap.poll_health",
            lambda *_a, **_kw: True,
        )
        monkeypatch.setattr(
            "worthless.cli.commands.wrap.forward_signals",
            lambda **_kw: None,
        )
        monkeypatch.setattr("subprocess.Popen", lambda *_a, **_kw: mock_child)

        result = runner.invoke(
            app,
            ["wrap", "--", "echo", "hi"],
            env={"WORTHLESS_HOME": str(home_with_key.base_dir)},
        )
        assert result.exit_code == 0, f"wrap failed: {result.output}"

        # The critical assertion: wrap always passes port=0 (ephemeral)
        assert captured_kwargs.get("port") == 0, (
            f"wrap should use port=0 (ephemeral), got port={captured_kwargs.get('port')}"
        )

    def test_wrap_and_daemon_independent(
        self, home_with_key, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """wrap does not read or check the daemon PID file.

        Verifies that wrap spawns its own proxy without consulting
        any PID file, proving full independence from daemon state.
        """
        # Create a PID file pointing to a non-existent PID
        pid_file = home_with_key.base_dir / "daemon.pid"
        pid_file.write_text("999999999:8787")

        spawn_called = False

        def _track_spawn(**kw):
            nonlocal spawn_called
            spawn_called = True
            mock_proxy = MagicMock()
            mock_proxy.pid = 88888
            mock_proxy.poll.return_value = None
            mock_proxy.wait.return_value = 0
            return (mock_proxy, 22222)

        mock_child = MagicMock()
        mock_child.pid = 88889
        mock_child.poll.return_value = 0
        mock_child.returncode = 0
        mock_child.wait.return_value = 0

        monkeypatch.setattr(
            "worthless.cli.commands.wrap.spawn_proxy",
            _track_spawn,
        )
        monkeypatch.setattr(
            "worthless.cli.commands.wrap.poll_health",
            lambda *_a, **_kw: True,
        )
        monkeypatch.setattr(
            "worthless.cli.commands.wrap.forward_signals",
            lambda **_kw: None,
        )
        monkeypatch.setattr("subprocess.Popen", lambda *_a, **_kw: mock_child)

        result = runner.invoke(
            app,
            ["wrap", "--", "echo", "hi"],
            env={"WORTHLESS_HOME": str(home_with_key.base_dir)},
        )
        assert result.exit_code == 0, f"wrap failed: {result.output}"

        # wrap spawned its own proxy regardless of stale PID file
        assert spawn_called, "wrap should spawn its own proxy independent of daemon PID file"


# ------------------------------------------------------------------
# Lifecycle: wrap after lock/unlock leaves no enrolled keys
# ------------------------------------------------------------------


class TestWrapAfterUnlockExitsWithError:
    """wrap fails after lock+unlock removes all enrolled keys."""

    def test_wrap_after_lock_unlock_exits_key_not_found(self, home_dir, tmp_path: Path) -> None:
        """Lock a key, unlock it, then wrap should exit 1 with WRTLS-102."""
        from tests.helpers import fake_openai_key

        # Create a .env file with a key
        env_file = tmp_path / ".env"
        test_key = fake_openai_key()
        env_file.write_text(f"OPENAI_API_KEY={test_key}\n")

        home_env = {"WORTHLESS_HOME": str(home_dir.base_dir)}

        # Lock the key
        lock_result = runner.invoke(
            app,
            ["lock", "--env", str(env_file)],
            env=home_env,
        )
        assert lock_result.exit_code == 0, f"lock failed: {lock_result.output}"

        # Verify key is enrolled (wrap would succeed at this point)
        providers = _list_enrolled_providers(home_dir)
        assert len(providers) > 0, "Expected enrolled providers after lock"

        # Unlock the key (removes shards and enrollments)
        unlock_result = runner.invoke(
            app,
            ["unlock", "--env", str(env_file)],
            env=home_env,
        )
        assert unlock_result.exit_code == 0, f"unlock failed: {unlock_result.output}"

        # Verify no providers remain
        providers_after = _list_enrolled_providers(home_dir)
        assert providers_after == [], f"Expected no providers after unlock, got {providers_after}"

        # wrap should now fail with KEY_NOT_FOUND
        wrap_result = runner.invoke(
            app,
            ["wrap", "--", "echo", "hi"],
            env=home_env,
        )
        assert wrap_result.exit_code == 1
        assert "WRTLS-102" in wrap_result.output

    def test_wrap_after_partial_unlock_still_works(self, home_dir, tmp_path: Path) -> None:
        """Lock two keys, unlock one -- wrap should still succeed (with mocked proxy)."""
        from tests.helpers import fake_anthropic_key, fake_openai_key

        env_file = tmp_path / ".env"
        openai_key = fake_openai_key()
        anthropic_key = fake_anthropic_key()
        env_file.write_text(f"OPENAI_API_KEY={openai_key}\nANTHROPIC_API_KEY={anthropic_key}\n")

        home_env = {"WORTHLESS_HOME": str(home_dir.base_dir)}

        # Lock both keys
        lock_result = runner.invoke(
            app,
            ["lock", "--env", str(env_file)],
            env=home_env,
        )
        assert lock_result.exit_code == 0, f"lock failed: {lock_result.output}"

        # Find one alias to unlock
        shard_a_files = [f for f in home_dir.shard_a_dir.iterdir() if f.is_file()]
        assert len(shard_a_files) == 2, f"Expected 2 shard_a files, got {len(shard_a_files)}"

        # Unlock just one alias
        alias_to_unlock = shard_a_files[0].name
        unlock_result = runner.invoke(
            app,
            ["unlock", "--alias", alias_to_unlock, "--env", str(env_file)],
            env=home_env,
        )
        assert unlock_result.exit_code == 0, f"unlock failed: {unlock_result.output}"

        # One provider should remain enrolled
        providers_after = _list_enrolled_providers(home_dir)
        assert len(providers_after) == 1, (
            f"Expected 1 provider after partial unlock, got {providers_after}"
        )


# ------------------------------------------------------------------
# Failure-path tests (bead worthless-1k9)
# ------------------------------------------------------------------


class TestProxySpawnFailureFDCleanup:
    """When spawn_proxy raises, both liveness pipe FDs are closed."""

    def test_spawn_failure_closes_both_fds(
        self, home_with_key, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Both read_fd and write_fd are closed when spawn_proxy raises."""
        closed_fds: list[int] = []
        real_os_close = os.close

        # Create real FDs to return from mocked create_liveness_pipe
        real_r, real_w = os.pipe()

        monkeypatch.setattr(
            "worthless.cli.commands.wrap.create_liveness_pipe",
            lambda: (real_r, real_w),
        )

        def _tracking_close(fd: int) -> None:
            closed_fds.append(fd)
            real_os_close(fd)

        monkeypatch.setattr("worthless.cli.commands.wrap.os.close", _tracking_close)

        def _fail(**_kw):
            raise RuntimeError("bind failed")

        monkeypatch.setattr(
            "worthless.cli.commands.wrap.spawn_proxy",
            _fail,
        )

        result = runner.invoke(
            app,
            ["wrap", "--", "echo", "hi"],
            env={"WORTHLESS_HOME": str(home_with_key.base_dir)},
        )
        assert result.exit_code == 1

        # Both FDs must have been closed
        assert real_r in closed_fds, f"read_fd {real_r} was not closed; closed: {closed_fds}"
        assert real_w in closed_fds, f"write_fd {real_w} was not closed; closed: {closed_fds}"


class TestChildExitCodePropagatedViaWrap:
    """Child nonzero exit code propagated through the full wrap command."""

    def test_child_exits_nonzero_propagated(
        self, home_with_key, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When child exits with code 42, wrap should exit with code 42."""
        mock_proxy = MagicMock()
        mock_proxy.pid = 77770
        mock_proxy.poll.return_value = None
        mock_proxy.wait.return_value = 0

        mock_child = MagicMock()
        mock_child.pid = 77771
        mock_child.poll.return_value = 42
        mock_child.returncode = 42
        mock_child.wait.return_value = 42

        monkeypatch.setattr(
            "worthless.cli.commands.wrap.spawn_proxy",
            lambda **_kw: (mock_proxy, 9999),
        )
        monkeypatch.setattr(
            "worthless.cli.commands.wrap.poll_health",
            lambda *_a, **_kw: True,
        )
        monkeypatch.setattr(
            "worthless.cli.commands.wrap.forward_signals",
            lambda **_kw: None,
        )
        monkeypatch.setattr("subprocess.Popen", lambda *_a, **_kw: mock_child)

        result = runner.invoke(
            app,
            ["wrap", "--", "false"],
            env={"WORTHLESS_HOME": str(home_with_key.base_dir)},
        )
        assert result.exit_code == 42


class TestProxyDiesAfterChildFinishes:
    """When proxy dies AFTER child has already exited, no warning is emitted."""

    def test_proxy_dies_after_child_no_warning(
        self, home_with_key, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No 'proxy crashed mid-session' warning when child finishes first."""
        captured_messages: list[str] = []

        # Child finishes immediately
        mock_child = MagicMock()
        mock_child.pid = 88881
        mock_child.poll.return_value = 0
        mock_child.returncode = 0
        mock_child.wait.return_value = 0

        # Proxy finishes after child (wait blocks briefly)
        child_done = threading.Event()
        mock_proxy = MagicMock()
        mock_proxy.pid = 88880
        mock_proxy.returncode = 0

        def proxy_wait(**_kw):
            # Wait until child is done before proxy "dies"
            child_done.wait(timeout=5)
            time.sleep(0.05)
            return 0

        mock_proxy.wait.side_effect = proxy_wait
        mock_proxy.poll.return_value = 0  # already dead after wait returns

        import worthless.cli.commands.wrap as wrap_mod

        fake_sys = ModuleType("fake_sys")
        for attr in dir(sys):
            try:
                setattr(fake_sys, attr, getattr(sys, attr))
            except (AttributeError, TypeError):
                pass

        class _CapturingStderr:
            def write(self, msg: str) -> int:
                captured_messages.append(msg)
                return len(msg)

            def flush(self) -> None:
                pass

        fake_sys.stderr = _CapturingStderr()
        monkeypatch.setattr(wrap_mod, "sys", fake_sys)

        monkeypatch.setattr(
            wrap_mod,
            "spawn_proxy",
            lambda **_kw: (mock_proxy, 9999),
        )
        monkeypatch.setattr(
            wrap_mod,
            "poll_health",
            lambda *_a, **_kw: True,
        )
        monkeypatch.setattr(
            wrap_mod,
            "forward_signals",
            lambda **_kw: None,
        )

        original_run = _run_child_and_wait

        def _run_and_signal(child):
            code = original_run(child)
            child_done.set()
            return code

        monkeypatch.setattr(wrap_mod, "_run_child_and_wait", _run_and_signal)
        monkeypatch.setattr("subprocess.Popen", lambda *_a, **_kw: mock_child)

        result = runner.invoke(
            app,
            ["wrap", "--", "echo", "hi"],
            env={"WORTHLESS_HOME": str(home_with_key.base_dir)},
        )
        assert result.exit_code == 0

        # Give watcher thread time to run
        time.sleep(0.2)

        combined = "".join(captured_messages)
        assert "proxy crashed mid-session" not in combined, (
            f"Warning should NOT appear when proxy dies after child; got: {combined}"
        )


class TestWrapKeyboardInterruptCleanup:
    """KeyboardInterrupt during child.wait() propagates as exit 130."""

    def test_keyboard_interrupt_exits_130(
        self, home_with_key, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ctrl+C during child.wait() exits with 130 (128 + SIGINT)."""
        mock_proxy = MagicMock()
        mock_proxy.pid = 66660
        mock_proxy.poll.return_value = None
        mock_proxy.wait.return_value = 0

        mock_child = MagicMock()
        mock_child.pid = 66661
        mock_child.poll.return_value = None

        def _child_wait(**_kw):
            raise KeyboardInterrupt

        mock_child.wait.side_effect = _child_wait
        mock_child.returncode = None

        monkeypatch.setattr(
            "worthless.cli.commands.wrap.spawn_proxy",
            lambda **_kw: (mock_proxy, 9999),
        )
        monkeypatch.setattr(
            "worthless.cli.commands.wrap.poll_health",
            lambda *_a, **_kw: True,
        )
        monkeypatch.setattr(
            "worthless.cli.commands.wrap.forward_signals",
            lambda **_kw: None,
        )
        monkeypatch.setattr("subprocess.Popen", lambda *_a, **_kw: mock_child)

        result = runner.invoke(
            app,
            ["wrap", "--", "sleep", "999"],
            env={"WORTHLESS_HOME": str(home_with_key.base_dir)},
        )

        # error_boundary catches KeyboardInterrupt and exits 130 (128 + SIGINT)
        assert result.exit_code == 130
