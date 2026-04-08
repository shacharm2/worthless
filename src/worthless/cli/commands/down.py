"""down command -- stop a running proxy daemon.

``worthless down`` reads the PID file, sends SIGTERM to the process group,
polls for exit, escalates to SIGKILL after a timeout, and cleans up.
"""

from __future__ import annotations

import os
import signal
import time
from pathlib import Path

import typer

from worthless.cli.bootstrap import get_home
from worthless.cli.console import WorthlessConsole, get_console
from worthless.cli.errors import ErrorCode, WorthlessError, error_boundary
from worthless.cli.process import MAX_VALID_PID, check_pid, pid_path, read_pid

# Tunable for tests
_TERM_TIMEOUT: float = 5.0
_POLL_INTERVAL: float = 0.2


def _done(pf: Path, console: WorthlessConsole, msg: str, *, success: bool = False) -> None:
    """Clean PID file and print status."""
    pf.unlink(missing_ok=True)
    if success:
        console.print_success(msg)
    else:
        console.print_warning(msg)


def register_down_commands(app: typer.Typer) -> None:
    """Register the ``down`` command on the Typer app."""

    @app.command()
    @error_boundary
    def down() -> None:
        """Stop the running proxy daemon."""
        console = get_console()
        home = get_home()

        pf = pid_path(home)
        info = read_pid(pf)

        # No PID file or corrupt → nothing to stop (idempotent)
        if info is None:
            _done(pf, console, "Proxy is not running.")
            return

        pid, port = info

        # Reject dangerous or out-of-range PID values
        if pid <= 1 or pid > MAX_VALID_PID:
            _done(pf, console, f"Proxy is not running (invalid PID {pid} cleaned up).")
            return

        # Stale PID → clean up
        if not check_pid(pid):
            _done(pf, console, f"Proxy is not running (stale PID {pid} cleaned up).")
            return

        # Send SIGTERM to the process group
        try:
            pgid = os.getpgid(pid)
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            _done(pf, console, f"Proxy stopped (was PID {pid} on port {port}).", success=True)
            return
        except PermissionError as exc:
            raise WorthlessError(
                ErrorCode.PROXY_NOT_RUNNING,
                f"Cannot stop PID {pid}: permission denied. "
                f"Try: sudo kill -9 {pid}, then: worthless down",
            ) from exc

        console.print_hint(f"Stopping proxy (PID {pid})...")

        # Poll for graceful exit
        deadline = time.monotonic() + _TERM_TIMEOUT
        while time.monotonic() < deadline:
            if not check_pid(pid):
                _done(pf, console, f"Proxy stopped (was PID {pid} on port {port}).", success=True)
                return
            time.sleep(_POLL_INTERVAL)

        # Escalate to SIGKILL on the process group (Unix only)
        if hasattr(signal, "SIGKILL"):
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

        time.sleep(_POLL_INTERVAL)
        _done(
            pf,
            console,
            f"Proxy did not respond to SIGTERM within {_TERM_TIMEOUT:.0f}s; "
            f"force-killed (was PID {pid} on port {port}).",
        )
