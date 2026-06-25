"""
Recommended rewrite of _start_foreground() in up.py

Key changes:
1. Signal handler ONLY sets a threading.Event -- no subprocess calls
2. Main loop uses Event.wait() instead of proxy.wait(timeout=0.5)
3. All cleanup in a single finally block -- no double-cleanup risk
4. signal.pause() alternative noted but Event is cross-platform
"""

import signal
import subprocess
import threading
from pathlib import Path


def _start_foreground(
    proxy_env: dict[str, str],
    port: int,
    pid_file: Path,
    console,
) -> None:
    """Start proxy in foreground mode (blocks until SIGINT/SIGTERM)."""
    from worthless.cli.errors import ErrorCode, WorthlessError, sanitize_exception
    from worthless.cli.platform import IS_WINDOWS
    from worthless.cli.process import poll_health, spawn_proxy, write_pid

    import typer

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

    write_pid(pid_file, proxy.pid, actual_port)

    # Event-based shutdown: signal handler does minimal work
    shutdown_event = threading.Event()
    original_sigint = signal.getsignal(signal.SIGINT)

    def _on_signal(signum, frame):
        """Minimal signal handler -- just set the event."""
        shutdown_event.set()

    signal.signal(signal.SIGINT, _on_signal)
    if not IS_WINDOWS:
        signal.signal(signal.SIGTERM, _on_signal)

    try:
        # Health check
        healthy = poll_health(actual_port, timeout=15.0)
        if not healthy:
            console.print_error(
                WorthlessError(ErrorCode.PROXY_UNREACHABLE, "Proxy failed to become healthy")
            )
            raise typer.Exit(code=1)

        console.print_success(f"Proxy running on 127.0.0.1:{actual_port} (Ctrl+C to stop)")

        # Block until signal or process exit.
        # Event.wait() releases the GIL and is interruptible on Python 3.12+.
        # We still poll process liveness periodically in case it crashes.
        while not shutdown_event.is_set() and proxy.poll() is None:
            shutdown_event.wait(timeout=1.0)

    except KeyboardInterrupt:
        # Belt-and-suspenders: if default SIGINT behavior leaks through
        pass
    finally:
        # === ALL cleanup here, in normal code flow, never in a signal handler ===
        if proxy.poll() is None:
            proxy.terminate()
            try:
                proxy.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proxy.kill()
                try:
                    proxy.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass  # OS will reap

        pid_file.unlink(missing_ok=True)

        # Restore original handlers
        signal.signal(signal.SIGINT, original_sigint)
        if not IS_WINDOWS:
            signal.signal(signal.SIGTERM, signal.SIG_DFL)

        console.print_warning("Proxy stopped.")
