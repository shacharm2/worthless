"""up command -- standalone proxy daemon/foreground.

``worthless up`` starts the proxy in foreground on port 8787.
``worthless up -d`` starts it in daemon mode (background).
"""

from __future__ import annotations

import os
import signal
import subprocess  # nosec B404 — required for daemon process management
import time
from pathlib import Path

import typer

from worthless.cli.bootstrap import get_home
from worthless.cli.console import get_console
from worthless.cli.errors import ErrorCode, WorthlessError, error_boundary, sanitize_exception
from worthless.cli.platform import (
    IS_WINDOWS,
    pid_in_tree,
    popen_platform_kwargs,
    warn_windows_once,
)
from worthless.cli.process import (
    build_proxy_env,
    check_pid,
    cleanup_stale_pid,
    disable_core_dumps,
    fernet_transport,
    finalize_fernet_transport,
    pid_path,
    poll_health_pid,
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


def start_daemon(
    proxy_env: dict[str, str],
    port: int,
    pid_file: Path,
    log_file: Path,
    console,
) -> int:
    """Start proxy in daemon mode (setsid, write PID, detach).

    Returns the daemon PID on success.  Importable by other modules
    (e.g. the default command pipeline) that need to start the proxy
    programmatically.
    """
    cmd = proxy_cmd(port)

    log_fd: int = -1
    try:
        with fernet_transport(proxy_env) as (fernet_key, fernet_fd, fernet_fds):
            full_env = prepare_proxy_env(proxy_env, fernet_fd)
            platform_kwargs = popen_platform_kwargs(detach=True, pass_fds=tuple(fernet_fds))

            log_fd = os.open(str(log_file), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)

            proc = subprocess.Popen(  # nosec B603 — cmd is internally constructed, not user input
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

    # Record the spawn PID up front so a racing second invocation has
    # something live to detect even before health comes up.
    try:
        write_pid(pid_file, proc.pid, port)
    except OSError:
        proc.kill()
        raise

    resolved_pid = poll_health_pid(port, timeout=10.0)
    if resolved_pid is None:
        console.print_warning(
            f"Proxy started (PID {proc.pid}) but health check timed out. Check logs."
        )
        return proc.pid

    canonical_pid = _upgrade_pidfile_if_trusted(
        spawn_pid=proc.pid,
        resolved_pid=resolved_pid,
        port=port,
        pid_file=pid_file,
        console=console,
    )
    console.print_success(f"Proxy running on 127.0.0.1:{port} (PID {canonical_pid})")
    return canonical_pid


def _upgrade_pidfile_if_trusted(
    *,
    spawn_pid: int,
    resolved_pid: int,
    port: int,
    pid_file: Path,
    console,
) -> int:
    """Rewrite *pid_file* with *resolved_pid* only if it belongs to our tree.

    Returns the PID the caller should treat as canonical. A foreign daemon
    already bound to the port would also answer ``/healthz`` — we must not
    record its PID as ours. If the rewrite fails we keep the stale-but-
    writable *spawn_pid* rather than leaving an un-stoppable daemon.
    """
    if resolved_pid == spawn_pid:
        return spawn_pid
    if not pid_in_tree(spawn_pid, resolved_pid):
        console.print_warning(
            f"Proxy started (PID {spawn_pid}) but /healthz reports PID {resolved_pid}, "
            "which is not a descendant of our spawn — recording the spawn PID instead."
        )
        return spawn_pid
    try:
        write_pid(pid_file, resolved_pid, port)
    except OSError:
        return spawn_pid
    return resolved_pid


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
            start_daemon(proxy_env, actual_port, pid_file, log_file, console)
        else:
            _start_foreground(proxy_env, actual_port, pid_file, console)

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

        # Write PID file immediately so a racing second invocation has
        # something to detect. Rewrite below once /healthz reports the
        # authoritative PID.
        write_pid(pid_file, proxy.pid, actual_port)

        # Signal handler ONLY sets a flag — all cleanup in main thread.
        # This avoids reentrant wait() calls and blocking I/O in handlers.
        _shutdown = False

        def _on_signal(_signum=None, _frame=None):
            nonlocal _shutdown
            _shutdown = True

        signal.signal(signal.SIGINT, _on_signal)
        if not IS_WINDOWS:
            signal.signal(signal.SIGTERM, _on_signal)

        resolved_pid = poll_health_pid(actual_port, timeout=15.0)
        if resolved_pid is None:
            proxy.terminate()
            try:
                proxy.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proxy.kill()
                proxy.wait(timeout=2)  # reap after kill to prevent zombies
            pid_file.unlink(missing_ok=True)
            console.print_error(
                WorthlessError(ErrorCode.PROXY_UNREACHABLE, "Proxy failed to become healthy")
            )
            raise typer.Exit(code=1)

        _upgrade_pidfile_if_trusted(
            spawn_pid=proxy.pid,
            resolved_pid=resolved_pid,
            port=actual_port,
            pid_file=pid_file,
            console=console,
        )

        console.print_success(f"Proxy running on 127.0.0.1:{actual_port} (Ctrl+C to stop)")

        # Wait for proxy exit or signal. time.sleep is interrupted by
        # signals, so _shutdown gets checked within 0.5s of Ctrl+C.
        while proxy.poll() is None and not _shutdown:
            time.sleep(0.5)

        # Cleanup — always in main thread, never in handler
        proxy.terminate()
        try:
            proxy.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proxy.kill()
            try:
                proxy.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        pid_file.unlink(missing_ok=True)
        console.print_warning("Proxy stopped.")
