"""Single-container Docker entrypoint: spawn sidecar, then exec uvicorn proxy.

Mirrors the lifecycle that ``worthless up`` runs in the foreground:
1. Read fernet key from disk
2. ``split_to_tmpfs`` -> share files in ``$WORTHLESS_HOME/run/<pid>/``
3. ``spawn_sidecar`` -> ``python -m worthless.sidecar`` listening on a Unix socket
4. ``os.execvp`` uvicorn with the right env contract

After WOR-309, the proxy refuses to start without an IPC peer; tini supervises
both children so SIGTERM propagates correctly. We do NOT call into the
foreground supervisor (no signal flag, no health-poll wrapper) — that's the
``worthless up`` user-facing flow. Containers want a flat process tree where
tini is PID 1 and both sidecar + uvicorn are siblings.

Env contract on entry (set by ``deploy/entrypoint.sh``):
    WORTHLESS_HOME              base dir (default ``/data``)
    WORTHLESS_FERNET_KEY_PATH   fernet key location (optional, defaults to
                                ``$WORTHLESS_HOME/fernet.key``)
    PORT                        port for the proxy to bind (default 8787)

The Fernet key is loaded from disk here rather than passed via fd because
the sidecar reads it back from a share file the parent writes — no fd
inheritance subtlety needed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from worthless.cli.bootstrap import ensure_home
from worthless.cli.sidecar_lifecycle import spawn_sidecar, split_to_tmpfs


def _socket_path(run_dir: Path) -> Path:
    """Return the AF_UNIX socket path inside the run dir."""
    return run_dir / "sidecar.sock"


def main() -> None:
    home_dir = Path(os.environ.get("WORTHLESS_HOME", "/data"))
    home = ensure_home(home_dir)
    port = os.environ.get("PORT", "8787")

    # Step 1: split fernet key to per-PID share files.
    fernet_key = home.fernet_key
    shares = split_to_tmpfs(fernet_key, home.base_dir)

    # Step 2: spawn the sidecar; tini will reap it on SIGTERM.
    socket_path = _socket_path(shares.run_dir)
    handle = spawn_sidecar(socket_path, shares, allowed_uid=os.getuid())
    # Detach from the handle's lifecycle. tini supervises the sidecar
    # process directly via the parent-child relationship; we don't want a
    # Python-side handle holding it open through the exec below.
    _ = handle  # keep for type checkers; not used after exec

    # Step 3: thread the socket path into uvicorn's env so the proxy's
    # IPCSupervisor can connect on lifespan startup.
    os.environ["WORTHLESS_SIDECAR_SOCKET"] = str(socket_path)

    # Step 4: replace this process with uvicorn. tini sees the proxy as a
    # direct child; sidecar is also a direct child of tini. SIGTERM hits
    # both via the process group.
    uvicorn_argv = [
        "uvicorn",
        "worthless.proxy.app:create_app",
        "--factory",
        "--host",
        "0.0.0.0",  # noqa: S104 — Docker container; bind on all interfaces
        "--port",
        port,
    ]
    # uvicorn_argv is static (no user input); no shell, no injection surface.
    os.execvp(uvicorn_argv[0], uvicorn_argv)  # noqa: S606


if __name__ == "__main__":
    main()
    sys.exit(1)  # unreachable; execvp replaces the process
