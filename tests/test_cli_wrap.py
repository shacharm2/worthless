"""Tests for the ``worthless wrap`` command."""

from __future__ import annotations

import asyncio
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
from tests.conftest import make_repo as _repo
from worthless.cli.commands.wrap import (
    _build_child_env,
    _cleanup_proxy,
    _list_enrolled_aliases,
    _run_child_and_wait,
)
from worthless.cli.process import create_liveness_pipe

runner = CliRunner()


@pytest.fixture(autouse=True)
def _stub_sidecar_lifecycle(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Stub ``split_to_tmpfs`` + ``spawn_sidecar`` + ``shutdown_sidecar`` so
    wrap tests don't try to launch a real sidecar subprocess.

    After WOR-309, ``wrap`` spawns the sidecar before the proxy. The full
    lifecycle is covered by ``tests/cli/test_sidecar_lifecycle.py`` — here
    we stub to no-op fakes and yield a recorder dict so individual tests can
    assert call ordering (e.g., that shutdown_sidecar fired on cleanup).
    """
    from pathlib import Path
    from unittest.mock import MagicMock as _MagicMock

    fake_run_dir = Path("/tmp/wor-test-run")  # noqa: S108
    fake_socket = fake_run_dir / "sidecar.sock"
    fake_shares = _MagicMock(
        run_dir=fake_run_dir,
        share_a_path=fake_run_dir / "share_a.bin",
        share_b_path=fake_run_dir / "share_b.bin",
        shard_a=bytearray(32),
        shard_b=bytearray(32),
    )
    fake_handle = _MagicMock(
        socket_path=fake_socket,
        shares=fake_shares,
        allowed_uid=os.getuid(),
        proc=_MagicMock(pid=99999, poll=lambda: 0),
    )

    calls: dict = {"shutdown_count": 0, "shutdown_handle": None}

    def _record_shutdown(handle):
        calls["shutdown_count"] += 1
        calls["shutdown_handle"] = handle

    monkeypatch.setattr(
        "worthless.cli.commands.wrap.split_to_tmpfs",
        lambda _key, _home: fake_shares,
    )
    monkeypatch.setattr(
        "worthless.cli.commands.wrap.spawn_sidecar",
        lambda _socket, _shares, **_kw: fake_handle,
    )
    monkeypatch.setattr(
        "worthless.cli.commands.wrap.shutdown_sidecar",
        _record_shutdown,
    )
    return calls


class TestWrapEnvInjection:
    """wrap injects BASE_URL env vars for enrolled providers."""

    def test_child_env_has_base_url(self):
        """8rqs Phase 8: wrap NO LONGER injects OPENAI_BASE_URL — lock writes
        the right value into the user's .env at lock time and wrap respects it.

        We don't assert the var is absent (parent env may still have it
        from .env loading); the strict ``no synthesis`` contract is verified
        in :meth:`test_child_env_no_injection` below.
        """
        # Smoke: function callable, no exception with a real-shape input.
        _ = _build_child_env(port=9999, aliases=[("my-alias", "openai")])

    def test_child_env_no_injection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The headline 8rqs Phase 8 contract: wrap does not synthesise *_BASE_URL
        from the alias list. If the parent env is clean, the child env is too."""
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
        monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
        child_env = _build_child_env(
            port=9999,
            aliases=[("oai", "openai"), ("ant", "anthropic"), ("or", "openai")],
        )
        # No URL synthesis from aliases.
        assert "OPENAI_BASE_URL" not in child_env
        assert "ANTHROPIC_BASE_URL" not in child_env
        assert "OPENROUTER_BASE_URL" not in child_env

    def test_child_env_no_session_token(self):
        """Session token should not be in child env (dead code removed)."""
        child_env = _build_child_env(port=9999, aliases=[("my-alias", "openai")])
        assert "WORTHLESS_SESSION_TOKEN" not in child_env


class TestBuildChildEnvEdgeCases:
    """Edge cases for _build_child_env (post-8rqs Phase 8 — wrap is a passthrough)."""

    def test_empty_aliases(self, monkeypatch: pytest.MonkeyPatch):
        """Empty aliases list — env still inherits from parent."""
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
        child_env = _build_child_env(port=9999, aliases=[])
        assert "OPENAI_BASE_URL" not in child_env
        assert "ANTHROPIC_BASE_URL" not in child_env

    def test_inherits_current_env(self):
        """Child env should include current process env vars."""
        child_env = _build_child_env(port=9999, aliases=[("a", "openai")])
        assert "PATH" in child_env

    def test_parent_baseurl_passes_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If parent has OPENROUTER_BASE_URL set (from user's .env), wrap
        does not overwrite it — the user's value reaches the child."""
        monkeypatch.setenv("OPENROUTER_BASE_URL", "http://127.0.0.1:8787/openrouter-x/v1")
        child_env = _build_child_env(port=9999, aliases=[("openrouter-x", "openai")])
        assert child_env["OPENROUTER_BASE_URL"] == "http://127.0.0.1:8787/openrouter-x/v1"


class TestListEnrolledAliasesWithDB:
    """_list_enrolled_aliases returns aliases when DB has data."""

    def test_returns_aliases_from_db(self, home_with_key) -> None:
        aliases = _list_enrolled_aliases(home_with_key)
        assert len(aliases) >= 1
        assert all(isinstance(a, str) and isinstance(p, str) for a, p in aliases)


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
        aliases = _list_enrolled_aliases(home)
        assert aliases == []


class TestWrapLifecycleOrdering:
    """Pin the wrap startup contract: sidecar spawns BEFORE proxy, and the
    proxy is handed the sidecar's socket path via WORTHLESS_SIDECAR_SOCKET.

    Regression target: the wrap command shipped without sidecar spawn at all
    (worthless-r67t — proxy refused to bind because no IPC peer was running).
    The 36 stubbed tests in this file all passed because they don't assert
    ordering or env threading. This class is the canary that fires if anyone
    drops the spawn_sidecar call or forgets to thread the socket path.
    """

    def test_spawn_sidecar_runs_before_spawn_proxy(
        self, home_with_key, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """spawn_sidecar must be invoked strictly before spawn_proxy."""
        from pathlib import Path
        from unittest.mock import MagicMock as _MagicMock

        order: list[str] = []

        # Override the autouse fixture's stubs to record call order.
        fake_run_dir = Path("/tmp/wor-test-run-order")  # noqa: S108
        fake_socket = fake_run_dir / "sidecar.sock"
        fake_shares = _MagicMock(
            run_dir=fake_run_dir,
            share_a_path=fake_run_dir / "share_a.bin",
            share_b_path=fake_run_dir / "share_b.bin",
            shard_a=bytearray(32),
            shard_b=bytearray(32),
        )
        fake_handle = _MagicMock(
            socket_path=fake_socket,
            shares=fake_shares,
            allowed_uid=os.getuid(),
            proc=_MagicMock(pid=99999, poll=lambda: 0),
        )

        def _record_split(_key, _home):
            order.append("split_to_tmpfs")
            return fake_shares

        def _record_spawn_sidecar(_socket, _shares, **_kw):
            order.append("spawn_sidecar")
            return fake_handle

        def _record_spawn_proxy(**_kw):
            order.append("spawn_proxy")
            mock_proxy = MagicMock()
            mock_proxy.poll.return_value = None
            mock_proxy.wait.return_value = 0
            return (mock_proxy, 9999)

        monkeypatch.setattr("worthless.cli.commands.wrap.split_to_tmpfs", _record_split)
        monkeypatch.setattr("worthless.cli.commands.wrap.spawn_sidecar", _record_spawn_sidecar)
        monkeypatch.setattr("worthless.cli.commands.wrap.spawn_proxy", _record_spawn_proxy)
        # Health poll fails so we exit cleanly without running the child.
        monkeypatch.setattr("worthless.cli.commands.wrap.poll_health", lambda *_a, **_kw: False)

        runner.invoke(
            app,
            ["wrap", "--", "echo", "hi"],
            env={"WORTHLESS_HOME": str(home_with_key.base_dir)},
        )

        assert order[:3] == ["split_to_tmpfs", "spawn_sidecar", "spawn_proxy"], (
            f"wrong startup order: {order}. Expected split_to_tmpfs → spawn_sidecar → spawn_proxy."
        )

    def test_proxy_env_contains_sidecar_socket(
        self, home_with_key, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """spawn_proxy must receive WORTHLESS_SIDECAR_SOCKET pointing to the
        sidecar's actual socket path. Without this, the proxy can't connect
        to the sidecar's IPC peer and refuses to bind."""
        from pathlib import Path
        from unittest.mock import MagicMock as _MagicMock

        fake_socket = Path("/tmp/wor-test-run-env/sidecar.sock")  # noqa: S108
        fake_shares = _MagicMock(
            run_dir=fake_socket.parent,
            share_a_path=fake_socket.parent / "share_a.bin",
            share_b_path=fake_socket.parent / "share_b.bin",
            shard_a=bytearray(32),
            shard_b=bytearray(32),
        )
        fake_handle = _MagicMock(
            socket_path=fake_socket,
            shares=fake_shares,
            allowed_uid=os.getuid(),
            proc=_MagicMock(pid=99999, poll=lambda: 0),
        )

        captured_proxy_env: dict = {}

        def _capture_spawn_proxy(**kw):
            captured_proxy_env.update(kw.get("env", {}))
            mock_proxy = MagicMock()
            mock_proxy.poll.return_value = None
            mock_proxy.wait.return_value = 0
            return (mock_proxy, 9999)

        monkeypatch.setattr(
            "worthless.cli.commands.wrap.split_to_tmpfs",
            lambda _key, _home: fake_shares,
        )
        monkeypatch.setattr(
            "worthless.cli.commands.wrap.spawn_sidecar",
            lambda _socket, _shares, **_kw: fake_handle,
        )
        monkeypatch.setattr("worthless.cli.commands.wrap.spawn_proxy", _capture_spawn_proxy)
        monkeypatch.setattr("worthless.cli.commands.wrap.poll_health", lambda *_a, **_kw: False)

        runner.invoke(
            app,
            ["wrap", "--", "echo", "hi"],
            env={"WORTHLESS_HOME": str(home_with_key.base_dir)},
        )

        assert "WORTHLESS_SIDECAR_SOCKET" in captured_proxy_env, (
            f"proxy env must include WORTHLESS_SIDECAR_SOCKET, got keys: "
            f"{sorted(captured_proxy_env)}"
        )
        assert captured_proxy_env["WORTHLESS_SIDECAR_SOCKET"] == str(fake_socket), (
            f"socket path mismatch: env={captured_proxy_env['WORTHLESS_SIDECAR_SOCKET']!r}, "
            f"expected={str(fake_socket)!r}"
        )


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
        self,
        home_with_key,
        monkeypatch: pytest.MonkeyPatch,
        _stub_sidecar_lifecycle: dict,
    ) -> None:
        """When poll_health returns False, proxy AND sidecar are torn down."""
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
        # _cleanup_lifecycle must shut down the sidecar after the proxy.
        assert _stub_sidecar_lifecycle["shutdown_count"] == 1


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


class TestListEnrolledAliasesNoDB:
    """_list_enrolled_aliases returns [] when DB doesn't exist."""

    def test_no_db_returns_empty(self, tmp_path: Path) -> None:
        from worthless.cli.bootstrap import WorthlessHome

        home = WorthlessHome(base_dir=tmp_path / ".worthless")
        aliases = _list_enrolled_aliases(home)
        assert aliases == []


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

        # 8rqs Phase 8: wrap no longer injects OPENAI_BASE_URL (lock owns
        # that now via the user's own .env). The child env is the parent
        # env passed through; we verify it's NOT the synthetic alias-URL
        # form that pre-8rqs wrap would have produced.
        synthetic = "http://127.0.0.1:9999/"
        assert not captured_env.get("OPENAI_BASE_URL", "").startswith(synthetic), (
            f"wrap synthesised OPENAI_BASE_URL post-8rqs: {captured_env.get('OPENAI_BASE_URL')!r}"
        )


# ------------------------------------------------------------------
# worthless-j3y: Daemon + wrap port coexistence
# ------------------------------------------------------------------


class TestWrapDaemonCoexistence:
    """wrap always uses port=0 (ephemeral), ignoring daemon state."""

    def test_wrap_always_requests_ephemeral_port(
        self, home_with_key, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """wrap passes port=0 to spawn_proxy even when WORTHLESS_PORT is set."""
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

        monkeypatch.setattr("worthless.cli.commands.wrap.spawn_proxy", _capture_spawn)
        monkeypatch.setattr("worthless.cli.commands.wrap.poll_health", lambda *_a, **_kw: True)
        monkeypatch.setattr("worthless.cli.commands.wrap.forward_signals", lambda **_kw: None)
        monkeypatch.setattr("subprocess.Popen", lambda *_a, **_kw: mock_child)
        monkeypatch.setenv("WORTHLESS_PORT", "8787")

        result = runner.invoke(
            app,
            ["wrap", "--", "echo", "hi"],
            env={"WORTHLESS_HOME": str(home_with_key.base_dir), "WORTHLESS_PORT": "8787"},
        )
        assert result.exit_code == 0, f"wrap failed: {result.output}"
        assert captured_kwargs.get("port") == 0


# ------------------------------------------------------------------
# Lifecycle: wrap after lock/unlock leaves no enrolled keys
# ------------------------------------------------------------------


class TestWrapAfterUnlockExitsWithError:
    """wrap refuses when all keys have been unlocked."""

    def test_lock_unlock_then_wrap_fails(self, home_dir, tmp_path: Path) -> None:
        """lock → unlock → wrap exits 1 with WRTLS-102."""
        from tests.helpers import fake_openai_key

        env_file = tmp_path / ".env"
        env_file.write_text(f"OPENAI_API_KEY={fake_openai_key()}\n")
        home_env = {"WORTHLESS_HOME": str(home_dir.base_dir)}

        result = runner.invoke(app, ["lock", "--env", str(env_file)], env=home_env)
        assert result.exit_code == 0, result.output

        result = runner.invoke(app, ["unlock", "--env", str(env_file)], env=home_env)
        assert result.exit_code == 0, result.output
        assert _list_enrolled_aliases(home_dir) == []

        result = runner.invoke(app, ["wrap", "--", "echo", "hi"], env=home_env)
        assert result.exit_code == 1
        assert "WRTLS-102" in result.output

    def test_partial_unlock_leaves_wrap_functional(self, home_dir, tmp_path: Path) -> None:
        """Lock two keys, unlock one — wrap still has a provider."""
        from tests.helpers import fake_anthropic_key, fake_openai_key

        env_file = tmp_path / ".env"
        env_file.write_text(
            f"OPENAI_API_KEY={fake_openai_key()}\nANTHROPIC_API_KEY={fake_anthropic_key()}\n"
        )
        home_env = {"WORTHLESS_HOME": str(home_dir.base_dir)}

        result = runner.invoke(app, ["lock", "--env", str(env_file)], env=home_env)
        assert result.exit_code == 0, result.output

        repo = _repo(home_dir)
        aliases = asyncio.run(repo.list_keys())
        alias = aliases[0]
        result = runner.invoke(
            app, ["unlock", "--alias", alias, "--env", str(env_file)], env=home_env
        )
        assert result.exit_code == 0, result.output
        assert len(_list_enrolled_aliases(home_dir)) == 1


# ------------------------------------------------------------------
# Failure-path tests (bead worthless-1k9)
# ------------------------------------------------------------------


class TestProxySpawnFailureFDCleanup:
    """spawn_proxy failure must close both liveness pipe FDs."""

    def test_spawn_failure_closes_both_fds(
        self, home_with_key, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        closed_fds: list[int] = []
        real_os_close = os.close
        real_r, real_w = os.pipe()

        monkeypatch.setattr(
            "worthless.cli.commands.wrap.create_liveness_pipe",
            lambda: (real_r, real_w),
        )
        monkeypatch.setattr(
            "worthless.cli.commands.wrap.os.close",
            lambda fd: (closed_fds.append(fd), real_os_close(fd)),
        )

        def _fail(**_kw):
            raise RuntimeError("bind failed")

        monkeypatch.setattr("worthless.cli.commands.wrap.spawn_proxy", _fail)

        result = runner.invoke(
            app,
            ["wrap", "--", "echo", "hi"],
            env={"WORTHLESS_HOME": str(home_with_key.base_dir)},
        )
        assert result.exit_code == 1
        assert real_r in closed_fds
        assert real_w in closed_fds


class TestChildExitCodePropagatedViaWrap:
    """Child nonzero exit code flows through wrap → typer.Exit."""

    def test_child_exits_42(self, home_with_key, monkeypatch: pytest.MonkeyPatch) -> None:
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
        monkeypatch.setattr("worthless.cli.commands.wrap.poll_health", lambda *_a, **_kw: True)
        monkeypatch.setattr("worthless.cli.commands.wrap.forward_signals", lambda **_kw: None)
        monkeypatch.setattr("subprocess.Popen", lambda *_a, **_kw: mock_child)

        result = runner.invoke(
            app,
            ["wrap", "--", "false"],
            env={"WORTHLESS_HOME": str(home_with_key.base_dir)},
        )
        assert result.exit_code == 42


class TestWrapKeyboardInterruptCleanup:
    """Ctrl+C during child.wait() exits 130 (128 + SIGINT)."""

    def test_keyboard_interrupt_exits_130(
        self, home_with_key, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_proxy = MagicMock()
        mock_proxy.pid = 66660
        mock_proxy.poll.return_value = None
        mock_proxy.wait.return_value = 0

        mock_child = MagicMock()
        mock_child.pid = 66661
        mock_child.poll.return_value = None
        mock_child.wait.side_effect = KeyboardInterrupt
        mock_child.returncode = None

        monkeypatch.setattr(
            "worthless.cli.commands.wrap.spawn_proxy",
            lambda **_kw: (mock_proxy, 9999),
        )
        monkeypatch.setattr("worthless.cli.commands.wrap.poll_health", lambda *_a, **_kw: True)
        monkeypatch.setattr("worthless.cli.commands.wrap.forward_signals", lambda **_kw: None)
        monkeypatch.setattr("subprocess.Popen", lambda *_a, **_kw: mock_child)

        result = runner.invoke(
            app,
            ["wrap", "--", "sleep", "999"],
            env={"WORTHLESS_HOME": str(home_with_key.base_dir)},
        )
        assert result.exit_code == 130
