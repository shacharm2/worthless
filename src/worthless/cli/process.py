"""Process lifecycle — pipe death detection, signal forwarding, PID files.

Shared infrastructure for ``wrap`` (ephemeral proxy + child) and ``up``
(standalone daemon) commands.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from worthless.cli.bootstrap import WorthlessHome
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# Regex to capture the port from uvicorn's startup line
_UVICORN_PORT_RE = re.compile(r"Uvicorn running on http://[\d.]+:(\d+)")


# ---------------------------------------------------------------------------
# Core dump suppression
# ---------------------------------------------------------------------------


def build_proxy_env(home: WorthlessHome) -> dict[str, str]:
    """Build the environment dict for spawning a proxy process."""
    return {
        "WORTHLESS_DB_PATH": str(home.db_path),
        "WORTHLESS_FERNET_KEY": home.fernet_key.decode(),
        "WORTHLESS_SHARD_A_DIR": str(home.shard_a_dir),
        "WORTHLESS_ALLOW_ALIAS_INFERENCE": "true",
    }


def disable_core_dumps() -> None:
    """Set RLIMIT_CORE to (0, 0) to prevent core dumps leaking key material.

    Silently ignored on platforms that don't support it (e.g. some CI runners).
    """
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (OSError, ValueError, AttributeError):
        pass


# ---------------------------------------------------------------------------
# Liveness pipe — parent holds write_fd, proxy watches read_fd for EOF
# ---------------------------------------------------------------------------


def create_liveness_pipe() -> tuple[int, int]:
    """Create an OS pipe for death detection.

    Returns:
        (read_fd, write_fd).  Pass *read_fd* to the proxy via
        ``WORTHLESS_LIVENESS_FD``.  Keep *write_fd* open in the parent;
        closing it (or parent death) signals EOF to the proxy.
    """
    return os.pipe()


# ---------------------------------------------------------------------------
# Proxy spawning
# ---------------------------------------------------------------------------


def spawn_proxy(
    env: dict[str, str],
    port: int = 0,
    liveness_fd: int | None = None,
) -> tuple[subprocess.Popen, int]:
    """Start uvicorn with the proxy app.

    Args:
        env: Environment variables for the proxy (WORTHLESS_*).
        port: Port to bind.  ``0`` means OS-assigned random port.
        liveness_fd: If set, passed to the child and advertised via
            ``WORTHLESS_LIVENESS_FD``.

    Returns:
        (process, actual_port).
    """
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

    # Pass Fernet key via inherited fd instead of env var (avoids /proc/PID/environ leak)
    fernet_key = env.pop("WORTHLESS_FERNET_KEY", None)
    fernet_fd: int | None = None
    if fernet_key:
        r_fd, w_fd = os.pipe()
        os.write(w_fd, fernet_key.encode() if isinstance(fernet_key, str) else fernet_key)
        os.close(w_fd)
        fernet_fd = r_fd

    # Build subprocess environment
    insecure = env.get("WORTHLESS_ALLOW_INSECURE", "true")
    full_env = {
        **os.environ,
        **env,
        "WORTHLESS_ALLOW_INSECURE": insecure,
    }
    if fernet_fd is not None:
        full_env["WORTHLESS_FERNET_FD"] = str(fernet_fd)

    pass_fds: list[int] = []
    if liveness_fd is not None:
        full_env["WORTHLESS_LIVENESS_FD"] = str(liveness_fd)
        pass_fds.append(liveness_fd)
    if fernet_fd is not None:
        pass_fds.append(fernet_fd)

    proc = subprocess.Popen(
        cmd,
        env=full_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        pass_fds=tuple(pass_fds),
    )

    if port == 0:
        # Parse actual port from uvicorn output
        actual_port = _parse_uvicorn_port(proc, timeout=15.0)
    else:
        actual_port = port

    return proc, actual_port


def _parse_uvicorn_port(proc: subprocess.Popen, timeout: float = 15.0) -> int:
    """Read uvicorn stdout until we find the port announcement."""
    assert proc.stdout is not None

    # Read output in a thread so we can impose a deadline
    lines: list[str] = []

    def _reader():
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            lines.append(line)
            m = _UVICORN_PORT_RE.search(line)
            if m:
                return

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    t.join(timeout=timeout)

    for line in lines:
        m = _UVICORN_PORT_RE.search(line)
        if m:
            return int(m.group(1))

    raise RuntimeError(
        f"Could not parse uvicorn port within {timeout}s. Output so far: {''.join(lines[:20])}"
    )


# ---------------------------------------------------------------------------
# Health polling
# ---------------------------------------------------------------------------


def poll_health(port: int, timeout: float = 10.0) -> bool:
    """Poll ``GET /healthz`` until 200 or *timeout*.

    Returns True if healthy, False if timeout.
    """
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/healthz"

    with httpx.Client(timeout=2.0) as client:
        while time.monotonic() < deadline:
            try:
                resp = client.get(url)
                if resp.status_code == 200:
                    return True
            except (httpx.ConnectError, httpx.TimeoutException, OSError):
                pass
            time.sleep(0.3)

    return False


# ---------------------------------------------------------------------------
# PID file management
# ---------------------------------------------------------------------------


def pid_path(home: WorthlessHome) -> Path:
    """Return the standard PID file path for a proxy daemon."""
    return home.base_dir / "proxy.pid"


def write_pid(pid_path: Path, pid: int, port: int) -> None:
    """Write PID file with ``pid\\nport`` format (0600 permissions)."""
    fd = os.open(str(pid_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, f"{pid}\n{port}\n".encode())
    finally:
        os.close(fd)


def read_pid(pid_path: Path) -> tuple[int, int] | None:
    """Read PID file.  Returns ``(pid, port)`` or ``None`` if missing/corrupt."""
    try:
        text = pid_path.read_text().strip()
        parts = text.split("\n")
        if len(parts) < 2:
            return None
        return int(parts[0]), int(parts[1])
    except (FileNotFoundError, ValueError, IndexError, OSError):
        return None


# Reject PIDs outside the valid range for any mainstream OS.
# Linux default pid_max is 4194304; macOS uses 99998.
MAX_VALID_PID: int = 4_194_304


def check_pid(pid: int) -> bool:
    """Return True if *pid* is alive (via ``os.kill(pid, 0)``).

    Rejects PIDs ≤ 1 or beyond the OS range to prevent signaling
    init, the caller's process group, or every user process.
    """
    if pid <= 1 or pid > MAX_VALID_PID:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def cleanup_stale_pid(pid_path: Path) -> bool:
    """Remove PID file if the recorded process is dead.

    Returns True if reclaimed (file removed or didn't exist),
    False if the process is still alive.
    """
    info = read_pid(pid_path)
    if info is None:
        # Missing or corrupt — treat as reclaimed
        try:
            pid_path.unlink(missing_ok=True)
        except OSError:
            pass
        return True

    pid, _port = info
    if check_pid(pid):
        return False  # Still alive

    pid_path.unlink(missing_ok=True)
    return True


# ---------------------------------------------------------------------------
# Signal forwarding
# ---------------------------------------------------------------------------


def forward_signals(
    proxy: subprocess.Popen,
    child: subprocess.Popen | None,
) -> None:
    """Register handlers that forward SIGINT/SIGTERM to *proxy* and *child*.

    Uses process group kill (``os.killpg``) for robust cleanup.
    """

    def _handler(signum: int, _frame: object) -> None:
        for proc in (child, proxy):
            if proc is not None and proc.poll() is None:
                try:
                    os.killpg(os.getpgid(proc.pid), signum)
                except (ProcessLookupError, PermissionError, OSError):
                    pass

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)
