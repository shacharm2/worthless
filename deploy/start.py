"""Single-container Docker entrypoint: spawn sidecar, then exec uvicorn proxy.

Mirrors the lifecycle that ``worthless up`` runs in the foreground:
1. Read fernet key from disk
2. ``split_to_tmpfs`` -> share files in ``$WORTHLESS_HOME/run/<pid>/``
3. ``spawn_sidecar`` -> ``python -m worthless.sidecar`` listening on a Unix socket
4. ``os.execvp`` uvicorn with the right env contract

tini supervises both children so SIGTERM propagates correctly. We do NOT call
into the foreground supervisor (no signal flag, no health-poll wrapper) — that's
the ``worthless up`` user-facing flow. Containers want a flat process tree where
tini is PID 1 and both sidecar + uvicorn are siblings.

Env contract on entry (set by ``deploy/entrypoint.sh``):
    WORTHLESS_HOME              base dir (default ``/data``)
    WORTHLESS_FERNET_KEY_PATH   fernet key location (optional, defaults to
                                ``$WORTHLESS_HOME/fernet.key``)
    PORT                        port for the proxy to bind (default 8787)
    WORTHLESS_DEPLOY_MODE       loopback | lan | public (default loopback)
    WORTHLESS_HOST              explicit bind host (optional)
    WORTHLESS_TRUSTED_PROXIES   CSV of CIDRs (required when mode=public)

The Fernet key is loaded from disk here rather than passed via fd because
the sidecar reads it back from a share file the parent writes — no fd
inheritance subtlety needed.
"""

from __future__ import annotations

import os
from pathlib import Path

from worthless.cli.bootstrap import ensure_home
from worthless.cli.sidecar_lifecycle import spawn_sidecar, split_to_tmpfs
from worthless.crypto.types import zero_buf


def _socket_path(run_dir: Path) -> Path:
    """Return the AF_UNIX socket path inside the run dir."""
    return run_dir / "sidecar.sock"


def _resolve_bind(mode: str) -> str:
    """Pick the uvicorn bind host for *mode*.

    Mirrors :func:`worthless.proxy.config._read_default_host` — explicit
    ``WORTHLESS_HOST`` always wins; otherwise ``public`` defaults to
    ``0.0.0.0`` (PaaS edge) and the others to loopback. ProxySettings'
    own ``validate()`` re-checks the (mode, host) tuple before the app
    binds, so a misconfigured combination still refuses to start.
    """
    explicit = os.environ.get("WORTHLESS_HOST", "").strip()
    if explicit:
        return explicit
    if mode == "public":
        return "0.0.0.0"  # noqa: S104 — public mode binds the edge-facing iface by design
    return "127.0.0.1"


def _build_uvicorn_argv(port: str) -> list[str]:
    mode = os.environ.get("WORTHLESS_DEPLOY_MODE", "loopback").strip().lower() or "loopback"
    host = _resolve_bind(mode)
    argv: list[str] = [
        "uvicorn",
        "worthless.proxy.app:create_app",
        "--factory",
        "--host",
        host,
        "--port",
        port,
    ]
    trusted = os.environ.get("WORTHLESS_TRUSTED_PROXIES", "").strip()
    # ``public`` REQUIRES the trusted-proxy list (entrypoint.sh refuses
    # boot without it). ``lan`` enables --proxy-headers iff the operator
    # opted in by setting the env. ``loopback`` never trusts forwarded
    # headers — there is no edge.
    if mode == "public" or (mode == "lan" and trusted):
        argv += ["--proxy-headers", f"--forwarded-allow-ips={trusted}"]
    return argv


def main() -> None:
    home_dir = Path(os.environ.get("WORTHLESS_HOME", "/data"))
    home = ensure_home(home_dir)
    port = os.environ.get("PORT", "8787")

    fernet_key = home.fernet_key
    try:
        shares = split_to_tmpfs(fernet_key, home.base_dir)
    finally:
        # SR-02: fernet bytes are no longer needed once split into shares.
        zero_buf(fernet_key)

    socket_path = _socket_path(shares.run_dir)
    try:
        spawn_sidecar(socket_path, shares, allowed_uid=os.getuid())
    except BaseException:
        # Mirror up.py's failure path: unlink share files, rmdir run_dir,
        # zero shard bytearrays, then re-raise.
        for path in (shares.share_a_path, shares.share_b_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            shares.run_dir.rmdir()
        except OSError:
            pass
        zero_buf(shares.shard_a)
        zero_buf(shares.shard_b)
        raise

    # Sidecar has read the share files from disk; in-memory shard buffers
    # in this process are redundant. Zero before exec replaces the process.
    zero_buf(shares.shard_a)
    zero_buf(shares.shard_b)

    os.environ["WORTHLESS_SIDECAR_SOCKET"] = str(socket_path)

    uvicorn_argv = _build_uvicorn_argv(port)
    # uvicorn_argv is static (no user input apart from CIDR allowlist
    # which uvicorn parses); no shell, no injection surface.
    os.execvp(uvicorn_argv[0], uvicorn_argv)  # noqa: S606


if __name__ == "__main__":
    main()
