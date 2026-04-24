"""Shared IPC test fixtures (sockets, shares, running sidecar, client).

Extracted from ``test_roundtrip.py`` so failure-matrix and future IPC
tests can reuse them without duplication. Pytest autodiscovers every
fixture in this file for any test in ``tests/ipc/``.

See ``docs/ipc-contract.md`` for the surfaces these fixtures exercise.
"""

from __future__ import annotations

import asyncio
import base64
import os
import secrets
import tempfile
from collections.abc import AsyncIterator, Generator
from pathlib import Path

import pytest
import pytest_asyncio

from worthless.ipc.client import IPCClient
from worthless.sidecar.backends.base import Backend
from worthless.sidecar.backends.fernet import FernetBackend
from worthless.sidecar.server import start_sidecar

# AF_UNIX sun_path is 104 bytes on macOS (108 on Linux). Use the tighter
# limit with a byte of headroom for the terminating NUL.
_SUN_PATH_MAX = 104


@pytest.fixture
def sidecar_socket_path() -> Generator[Path, None, None]:
    """Return a short, xdist-safe AF_UNIX socket path.

    Uses ``tempfile.mkdtemp`` directly under ``/tmp`` (not pytest's
    ``tmp_path_factory``) so we stay under macOS's 104-byte ``sun_path``
    limit even under xdist. Pytest's tmp dir on macOS resolves to a
    ~90-char prefix under ``/private/var/folders/...`` — add any worker
    suffix + ``s.sock`` basename and we blow the limit.

    ``mkdtemp`` is atomic per-worker so there is no race to guard.
    """
    base = Path(tempfile.mkdtemp(prefix="w-", dir="/tmp"))
    sock_path = base / "s.sock"
    if len(str(sock_path)) >= _SUN_PATH_MAX:
        pytest.skip(
            f"tmp path too long for AF_UNIX (len={len(str(sock_path))} >= {_SUN_PATH_MAX}): "
            f"{sock_path}"
        )
    try:
        yield sock_path
    finally:
        # Server unlinks the socket on close; remove the parent dir too.
        try:
            if sock_path.exists():
                sock_path.unlink()
            base.rmdir()
        except OSError:
            pass


@pytest.fixture
def fernet_shares() -> tuple[bytes, bytes]:
    """Two 44-byte shares whose XOR yields a valid urlsafe-b64 Fernet key."""
    key = base64.urlsafe_b64encode(secrets.token_bytes(32))  # 44 bytes
    share_a = secrets.token_bytes(len(key))
    share_b = bytes(a ^ k for a, k in zip(share_a, key, strict=True))
    return share_a, share_b


@pytest.fixture
def fernet_backend(fernet_shares: tuple[bytes, bytes]) -> FernetBackend:
    """Instantiated FernetBackend ready for sealing/opening."""
    return FernetBackend(shares=fernet_shares)


@pytest_asyncio.fixture
async def running_sidecar(sidecar_socket_path: Path, fernet_backend: FernetBackend):
    """Start a sidecar bound to ``sidecar_socket_path``; tear down cleanly."""
    server = await start_sidecar(
        socket_path=sidecar_socket_path,
        backend=fernet_backend,
        allowed_uids=[os.getuid()],
    )
    try:
        yield server
    finally:
        server.close()
        try:
            await server.wait_closed()
        except Exception:
            pass


@pytest_asyncio.fixture
async def ipc_client(running_sidecar, sidecar_socket_path: Path) -> AsyncIterator[IPCClient]:
    """Yield a connected IPCClient against the running sidecar."""
    async with IPCClient(sidecar_socket_path) as client:
        yield client


class StallingBackend(Backend):
    """Backend that blocks forever on ``seal`` so client timeouts trip.

    ``open`` and ``attest`` delegate to a real Fernet so the handshake and
    any non-seal paths still work — only the operation under test stalls.
    """

    def __init__(self, inner: FernetBackend) -> None:
        self._inner = inner

    async def seal(self, plaintext: bytes, context: bytes | None = None) -> bytes:
        await asyncio.sleep(60)
        return b""  # unreachable

    async def open(
        self,
        ciphertext: bytes,
        context: bytes | None = None,
        key_id: bytes | None = None,
    ) -> bytes:
        return await self._inner.open(ciphertext, context, key_id)

    async def attest(self, nonce: bytes, purpose: str | None = None) -> bytes:
        return await self._inner.attest(nonce, purpose)
