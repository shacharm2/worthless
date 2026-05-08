"""Hybrid sidecar healthcheck CLI — ``python -m worthless.sidecar.health``.

Per WOR-310 / WOR-466. Replaces the HTTP ``/healthz`` Dockerfile probe that
proves only "uvicorn is alive" with a probe that actually proves "the sidecar
accept loop is alive and the IPC handshake works".

Failure modes caught (see ``.planning/wor-310/design-sidecar-health-cli.md``):

* socket path missing                  → exit 1, "socket missing"
* path exists but is not a socket      → exit 1, "not a socket"
* sidecar dead, stale socket inode     → exit 1, "connect refused"
* accept loop wedged                   → exit 1, "handshake timeout"
* uid not in sidecar's allowlist       → exit 1, "AUTH rejected"
* env not configured                   → exit 2, "WORTHLESS_SIDECAR_SOCKET unset"

Stderr uses fixed strings — Docker truncates ``State.Health.Log`` to 4 KB and
operators want "socket missing", not Python tracebacks.

Stdout is silent on success. Healthchecks that chatter on the happy path bury
the failure message in the truncated log buffer.
"""

from __future__ import annotations

import asyncio
import os
import stat
import sys
from pathlib import Path

from worthless.ipc.client import (
    IPCAuthError,
    IPCClient,
    IPCProtocolError,
    IPCTimeoutError,
)

# Total wall-clock budget. Docker's HEALTHCHECK timeout is 2s; we cap inside
# that so the kernel-level SIGKILL never trips us mid-handshake.
_TOTAL_BUDGET_S = 1.8

# IPCClient handshake budget. Healthy hello round-trip is sub-100ms. 1.5s gives
# 15× slack while still leaving wall-clock room for stat + connect.
_HANDSHAKE_BUDGET_S = 1.5


def _emit(msg: str) -> None:
    """Single point of stderr output. Fixed strings only — no tracebacks."""
    print(f"health: {msg}", file=sys.stderr)


async def _probe(socket_path: Path) -> int:
    """Open IPCClient, run hello handshake, close. Returns exit code.

    Wall-clock cap is enforced by the outer ``asyncio.wait_for`` in ``main``;
    the IPCClient's per-request timeout is a backstop for the HELLO read.
    """
    try:
        async with IPCClient(socket_path, timeout=_HANDSHAKE_BUDGET_S):
            # ``__aenter__`` runs the HELLO handshake. Nothing else to do.
            return 0
    except IPCAuthError:
        _emit("AUTH rejected")
        return 1
    except IPCTimeoutError:
        _emit("handshake timeout")
        return 1
    except IPCProtocolError:
        # Don't echo the wrapped message — could leak peer state.
        _emit("protocol error")
        return 1
    except (ConnectionRefusedError, FileNotFoundError):
        _emit("connect refused (sidecar dead?)")
        return 1
    except asyncio.TimeoutError:
        _emit("handshake timeout")
        return 1
    except PermissionError:
        # errno == EACCES — proxy uid lacks group membership on the socket.
        _emit("connect denied (permission)")
        return 1
    except OSError:
        # Other socket error (ENOTCONN, EPIPE, etc). Fixed string only.
        _emit("connect failed")
        return 1


def main() -> int:
    """Entry point. Returns the exit code; does NOT call ``sys.exit``."""
    raw = os.environ.get("WORTHLESS_SIDECAR_SOCKET")
    if not raw:
        _emit("WORTHLESS_SIDECAR_SOCKET unset")
        return 2

    socket_path = Path(raw)

    # Stat first — fastest filter for the two cheapest failure modes.
    try:
        st = os.lstat(socket_path)
    except FileNotFoundError:
        # Path comes from env; operator already knows it. No interpolation.
        _emit("socket missing")
        return 1
    except PermissionError:
        _emit("stat denied (permission)")
        return 1
    except OSError:
        _emit("stat failed")
        return 1

    if not stat.S_ISSOCK(st.st_mode):
        _emit("path exists but is not a socket")
        return 1

    # Hand off to the async probe under a wall-clock cap.
    try:
        return asyncio.run(asyncio.wait_for(_probe(socket_path), timeout=_TOTAL_BUDGET_S))
    except asyncio.TimeoutError:
        _emit("handshake timeout")
        return 1


if __name__ == "__main__":  # pragma: no cover — exercised by subprocess test
    sys.exit(main())
