"""up command -- standalone proxy daemon/foreground.

``worthless up`` starts the proxy in foreground on port 8787.
``worthless up -d`` starts it in daemon mode (background).
"""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path

import typer

from worthless.cli.bootstrap import get_home
from worthless.cli.console import get_console
from worthless.cli.errors import ErrorCode, WorthlessError, error_boundary, sanitize_exception
from worthless.cli.platform import IS_WINDOWS, popen_platform_kwargs, warn_windows_once
from worthless.cli.process import (
    build_proxy_env,
    check_pid,
    cleanup_stale_pid,
    disable_core_dumps,
    fernet_transport,
    finalize_fernet_transport,
    pid_path,
    poll_health,
    prepare_proxy_env,
    proxy_cmd,
    read_pid,
    spawn_proxy,
    write_pid,
)


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
    @error_boundary
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
        warn_windows_once(quiet=console.quiet)

        actual_port = _resolve_port(port)

        # Check PID file for existing proxy
        pid_file = pid_path(home)
        if pid_file.exists():
            info = read_pid(pid_file)
            if info is not None:
                existing_pid, existing_port = info
                if check_pid(existing_pid):
                    raise WorthlessError(
                        ErrorCode.PORT_IN_USE,
                        f"Proxy already running "
                        f"(PID {existing_pid} "
                        f"on port {existing_port}). "
                        f"Stop it first or use "
                        f"a different port.",
                    )
                else:
                    # Stale PID file -- reclaim
                    cleanup_stale_pid(pid_file)
                    console.print_warning(f"Reclaimed stale PID file (was PID {existing_pid})")

        # Disable core dumps
        disable_core_dumps()

        # Build proxy env
        proxy_env = build_proxy_env(home)

        if daemon:
            log_file = home.base_dir / "proxy.log"
            _start_daemon(proxy_env, actual_port, pid_file, log_file, console)
        else:
            _start_foreground(proxy_env, actual_port, pid_file, console)

    def _start_daemon(
        proxy_env: dict[str, str],
        port: int,
        pid_file: Path,
        log_file: Path,
        console,
    ) -> None:
        """Start proxy in daemon mode (setsid, write PID, detach)."""
        cmd = proxy_cmd(port)

        log_fd: int = -1
        try:
            with fernet_transport(proxy_env) as (fernet_key, fernet_fd, fernet_fds):
                full_env = prepare_proxy_env(proxy_env, fernet_fd)
                platform_kwargs = popen_platform_kwargs(detach=True, pass_fds=tuple(fernet_fds))

                log_fd = os.open(str(log_file), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)

                proc = subprocess.Popen(
                    cmd,
                    env=full_env,
                    stdout=subprocess.DEVNULL,
                    stderr=log_fd,
                    stdin=subprocess.PIPE if fernet_key else None,
                    **platform_kwargs,
                )

                finalize_fernet_transport(proc, fernet_key, fernet_fd)
        except Exception as exc:
            if not isinstance(exc, typer.Exit):
                console.print_error(
                    WorthlessError(
                        ErrorCode.PROXY_UNREACHABLE,
                        sanitize_exception(exc, generic="failed to start daemon"),
                    )
                )
            raise typer.Exit(code=1) from exc
        finally:
            if log_fd >= 0:
                os.close(log_fd)

        # Write PID file — kill daemon if this fails to prevent orphans
        try:
            write_pid(pid_file, proc.pid, port)
        except OSError:
            proc.kill()
            raise

        # Brief health check
        healthy = poll_health(port, timeout=10.0)
        if healthy:
            console.print_success(f"Proxy running on 127.0.0.1:{port} (PID {proc.pid})")
        else:
            console.print_warning(
                f"Proxy started (PID {proc.pid}) but health check timed out. Check logs."
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
                WorthlessError(
                    ErrorCode.PROXY_UNREACHABLE,
                    sanitize_exception(exc, generic="failed to start proxy"),
                )
            )
            raise typer.Exit(code=1) from exc

        # Write PID file
        write_pid(pid_file, proxy.pid, actual_port)

        # Register signal handler BEFORE health poll to prevent orphans
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
        if not IS_WINDOWS:
            signal.signal(signal.SIGTERM, _cleanup)

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

        # Wait for proxy to exit (either by signal or crash)
        proxy.wait()
        pid_file.unlink(missing_ok=True)
