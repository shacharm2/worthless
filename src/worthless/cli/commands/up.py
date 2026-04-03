"""up command -- standalone proxy daemon/foreground.

``worthless up`` starts the proxy in foreground on port 8787.
``worthless up -d`` starts it in daemon mode (background).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

import typer

from worthless.cli.bootstrap import WorthlessHome, get_home
from worthless.cli.console import get_console
from worthless.cli.errors import ErrorCode, WorthlessError
from worthless.cli.process import (
    build_proxy_env,
    check_pid,
    cleanup_stale_pid,
    disable_core_dumps,
    poll_health,
    read_pid,
    spawn_proxy,
    write_pid,
)


def _pid_path(home: WorthlessHome) -> Path:
    """Return the PID file path."""
    return home.base_dir / "proxy.pid"


def _resolve_port(port_arg: int | None) -> int:
    """Resolve port from argument, env var, or default.

    Priority: explicit arg > WORTHLESS_PORT env > 8787 default.
    """
    if port_arg is not None:
        return port_arg
    env_port = os.environ.get("WORTHLESS_PORT")
    if env_port:
        return int(env_port)
    return 8787


def register_up_commands(app: typer.Typer) -> None:
    """Register the ``up`` command on the Typer app."""

    @app.command()
    def up(
        port: int | None = typer.Option(
            None, "--port", "-p", help="Port to bind (default: 8787 or WORTHLESS_PORT)"
        ),
        daemon: bool = typer.Option(
            False, "--daemon", "-d", help="Run in background (daemon mode)"
        ),
    ) -> None:
        """Start the proxy server (foreground or daemon)."""
        console = get_console()
        home = get_home()

        try:
            actual_port = _resolve_port(port)

            # Check PID file for existing proxy
            pid_file = _pid_path(home)
            if pid_file.exists():
                info = read_pid(pid_file)
                if info is not None:
                    existing_pid, existing_port = info
                    if check_pid(existing_pid):
                        msg = (
                            f"Proxy already running "
                            f"(PID {existing_pid} "
                            f"on port {existing_port}). "
                            f"Stop it first or use "
                            f"a different port."
                        )
                        console.print_error(
                            WorthlessError(
                                ErrorCode.PORT_IN_USE,
                                msg,
                            )
                        )
                        raise typer.Exit(code=1)
                    else:
                        # Stale PID file -- reclaim
                        cleanup_stale_pid(pid_file)
                        console.print_warning(
                            f"Reclaimed stale PID file "
                            f"(was PID {existing_pid})"
                        )

            # Disable core dumps
            disable_core_dumps()

            # Build proxy env
            proxy_env = build_proxy_env(home)

            if daemon:
                _start_daemon(proxy_env, actual_port, pid_file, console)
            else:
                _start_foreground(proxy_env, actual_port, pid_file, console)
        except (typer.Exit, SystemExit):
            raise
        except WorthlessError as exc:
            console.print_error(exc)
            raise typer.Exit(code=1) from exc
        except Exception as exc:
            console.print_error(WorthlessError(ErrorCode.UNKNOWN, str(exc)))
            raise typer.Exit(code=1) from exc

    def _start_daemon(
        proxy_env: dict[str, str],
        port: int,
        pid_file: Path,
        console,
    ) -> None:
        """Start proxy in daemon mode (setsid, write PID, detach)."""
        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "worthless.proxy.app:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ]

        # Pass Fernet key via fd, not env var
        fernet_key = proxy_env.pop("WORTHLESS_FERNET_KEY", None)
        fernet_fd: int | None = None
        if fernet_key:
            r_fd, w_fd = os.pipe()
            os.write(w_fd, fernet_key.encode() if isinstance(fernet_key, str) else fernet_key)
            os.close(w_fd)
            fernet_fd = r_fd

        full_env = {
            **os.environ,
            **proxy_env,
            "WORTHLESS_ALLOW_INSECURE": proxy_env.get("WORTHLESS_ALLOW_INSECURE", "true"),
        }
        pass_fds: list[int] = []
        if fernet_fd is not None:
            full_env["WORTHLESS_FERNET_FD"] = str(fernet_fd)
            pass_fds.append(fernet_fd)

        # Start detached process
        try:
            proc = subprocess.Popen(
                cmd,
                env=full_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                pass_fds=tuple(pass_fds),
            )
        except Exception as exc:
            console.print_error(
                WorthlessError(ErrorCode.PROXY_UNREACHABLE, f"Failed to start daemon: {exc}")
            )
            raise typer.Exit(code=1) from exc

        # Write PID file
        write_pid(pid_file, proc.pid, port)

        # Brief health check
        healthy = poll_health(port, timeout=10.0)
        if healthy:
            console.print_success(f"Proxy running on 127.0.0.1:{port} (PID {proc.pid})")
        else:
            console.print_warning(
                f"Proxy started (PID {proc.pid}) but health check timed out. "
                f"Check logs."
            )

    def _start_foreground(
        proxy_env: dict[str, str],
        port: int,
        pid_file: Path,
        console,
    ) -> None:
        """Start proxy in foreground mode (blocks until SIGINT/SIGTERM)."""
        try:
            proxy, actual_port = spawn_proxy(env=proxy_env, port=port)
        except Exception as exc:
            console.print_error(
                WorthlessError(ErrorCode.PROXY_UNREACHABLE, f"Failed to start proxy: {exc}")
            )
            raise typer.Exit(code=1) from exc

        # Write PID file
        write_pid(pid_file, proxy.pid, actual_port)

        # Poll health
        healthy = poll_health(actual_port, timeout=15.0)
        if not healthy:
            proxy.terminate()
            proxy.wait(timeout=5)
            pid_file.unlink(missing_ok=True)
            console.print_error(
                WorthlessError(ErrorCode.PROXY_UNREACHABLE, "Proxy failed to become healthy")
            )
            raise typer.Exit(code=1)

        console.print_success(f"Proxy running on 127.0.0.1:{actual_port} (Ctrl+C to stop)")

        # Register signal handler that cleans up PID file and stops proxy
        def _cleanup(_signum, _frame):
            proxy.terminate()
            try:
                proxy.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proxy.kill()
                proxy.wait(timeout=2)
            pid_file.unlink(missing_ok=True)
            console.print_warning("Proxy stopped.")
            raise SystemExit(0)

        signal.signal(signal.SIGINT, _cleanup)
        signal.signal(signal.SIGTERM, _cleanup)

        # Wait for proxy to exit (either by signal or crash)
        proxy.wait()
        pid_file.unlink(missing_ok=True)
