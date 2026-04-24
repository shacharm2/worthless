"""Env-configured sidecar entry point.

Launches the sidecar server from two XOR-share files and an allowlist,
then serves until ``SIGTERM``/``SIGINT``. Intended to be the container
entrypoint in ``docker/sidecar/Dockerfile`` (WOR-307 Day 3 prototype)
and usable directly from systemd / launchd in production.

Usage::

    python -m worthless.sidecar

Environment:

* ``WORTHLESS_SIDECAR_SOCKET``      Pathname AF_UNIX socket to bind.
* ``WORTHLESS_SIDECAR_SHARE_A``     Path to first XOR share file (raw bytes).
* ``WORTHLESS_SIDECAR_SHARE_B``     Path to second XOR share file (raw bytes).
* ``WORTHLESS_SIDECAR_ALLOWED_UID`` CSV of uids permitted to connect.
* ``WORTHLESS_LOG_LEVEL`` (opt)     Python logging level; default ``INFO``.

Exit codes:

* ``0``  — graceful shutdown after signal
* ``1``  — config error (missing env, unreadable share, empty allowlist)
* ``2``  — bind failure

See ``docs/ipc-contract.md`` for the wire contract this server honours.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import stat
import sys
from pathlib import Path

from worthless.sidecar.backends.fernet import FernetBackend
from worthless.sidecar.server import start_sidecar

_LOG = logging.getLogger("worthless.sidecar")

_REQUIRED_ENV = (
    "WORTHLESS_SIDECAR_SOCKET",
    "WORTHLESS_SIDECAR_SHARE_A",
    "WORTHLESS_SIDECAR_SHARE_B",
    "WORTHLESS_SIDECAR_ALLOWED_UID",
)


def _load_shares(a_path: Path, b_path: Path) -> tuple[bytes, bytes]:
    share_a = a_path.read_bytes()
    share_b = b_path.read_bytes()
    if len(share_a) != len(share_b):
        raise ValueError(f"share length mismatch: {len(share_a)} vs {len(share_b)}")
    return share_a, share_b


_PROBE_TIMEOUT_S = 1.0


def _check_socket_path_available(path: Path) -> bool:
    """Probe ``path`` to decide whether we can safely bind there.

    Returns True in three cases: the path is missing, it's a stale socket
    inode (``connect`` → ``ECONNREFUSED``), or it's a socket inode whose
    peer has disappeared. The stale inode is unlinked before returning
    so the subsequent ``bind`` sees a clean path.

    Returns False when the path is occupied in a way we must not
    clobber — a live sidecar still accepting connections, or a
    non-socket file (which would indicate an operator config mistake;
    silently unlinking user data is worse than refusing to start).

    Rationale: ``asyncio.start_unix_server`` auto-unlinks *any* existing
    socket path before binding — including live ones — so two sidecars
    pointed at the same socket silently race for new connections. This
    probe closes that gap.
    """
    try:
        st = path.lstat()
    except FileNotFoundError:
        return True
    if not stat.S_ISSOCK(st.st_mode):
        _LOG.error("refusing to bind: %s exists and is not a socket", path)
        return False
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(_PROBE_TIMEOUT_S)
    try:
        probe.connect(str(path))
    except (ConnectionRefusedError, FileNotFoundError):
        # Stale inode — no one is accept()ing. Clear and proceed.
        path.unlink(missing_ok=True)
        return True
    except OSError as exc:
        _LOG.error("probe of existing socket %s failed: %s", path, exc)
        return False
    else:
        _LOG.error("sidecar already running on %s", path)
        return False
    finally:
        probe.close()


def _parse_allowlist(raw: str) -> tuple[int, ...]:
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        raise ValueError("allowlist is empty")
    return tuple(int(p) for p in parts)


async def _run() -> int:
    missing = [k for k in _REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        _LOG.error("missing required env: %s", ", ".join(missing))
        return 1

    socket_path = Path(os.environ["WORTHLESS_SIDECAR_SOCKET"])
    share_a = Path(os.environ["WORTHLESS_SIDECAR_SHARE_A"])
    share_b = Path(os.environ["WORTHLESS_SIDECAR_SHARE_B"])
    try:
        allowed = _parse_allowlist(os.environ["WORTHLESS_SIDECAR_ALLOWED_UID"])
    except ValueError as exc:
        _LOG.error("bad WORTHLESS_SIDECAR_ALLOWED_UID: %s", exc)
        return 1

    try:
        shares = _load_shares(share_a, share_b)
    except (OSError, ValueError) as exc:
        _LOG.error("share load failed: %s", exc)
        return 1

    try:
        backend = FernetBackend(shares=shares)
    except ValueError as exc:
        _LOG.error("backend init failed: %s", exc)
        return 1

    if not _check_socket_path_available(socket_path):
        return 2

    try:
        server = await start_sidecar(
            socket_path=socket_path,
            backend=backend,
            allowed_uids=allowed,
        )
    except OSError as exc:
        _LOG.error("bind failed on %s: %s", socket_path, exc)
        return 2

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_stop() -> None:
        _LOG.info("shutdown signal received; closing")
        stop.set()

    # add_signal_handler wires SIGTERM/SIGINT to the running loop safely;
    # signal.signal() would fight with asyncio's internal wakeup fd.
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _request_stop)

    _LOG.info(
        "sidecar ready on %s (allowed_uids=%s, backend_caps=%s)",
        socket_path,
        list(allowed),
        ("seal", "open", "attest"),
    )
    # Print a stable, machine-parseable ready line for supervisors.
    print(f"sidecar: ready socket={socket_path}", flush=True)

    await stop.wait()
    server.close()
    try:
        await server.wait_closed()
    except Exception as exc:  # noqa: BLE001 — best-effort drain
        _LOG.warning("wait_closed raised during shutdown: %s", exc)
    _LOG.info("sidecar shut down cleanly")
    return 0


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("WORTHLESS_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
