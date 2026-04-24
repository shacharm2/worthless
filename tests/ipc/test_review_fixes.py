"""Tests for PR #94 review fixes (CodeRabbit + GitHub Advanced Security).

These pin behaviours that were flagged on PR #94 before the contract freeze.
Each test corresponds to one numbered finding so a regression surfaces with
a clean signal rather than an opaque roundtrip failure.

Findings covered:

1. **Timeout-desync**: ``asyncio.wait_for`` cancels ``read_frame`` mid-parse;
   the StreamReader may have consumed a partial length prefix. The client
   must invalidate the connection so the next call fails fast rather than
   reading garbage off a desynchronised socket.

2. **err-with-id-0 routing**: the server emits ``err`` envelopes with the
   ``_ID_UNKNOWN`` sentinel (``id=0``) when it can't parse the inbound id.
   The client must check ``kind == "err"`` BEFORE id-mismatch so AUTH /
   PROTO / BACKEND distinctions are preserved.

3. **No double-prefix**: ``_err_from_envelope`` prepends ``"{code}: "`` to
   the message, but the server already sends pre-prefixed constants like
   ``"AUTH: peer uid not allowed"``. We must detect and skip the prepend
   so callers see ``"AUTH: peer uid not allowed"`` not
   ``"AUTH: AUTH: peer uid not allowed"``.

4. **Near-max frame roundtrip**: ``open_unix_connection`` defaults
   StreamReader buffer to 64 KiB; our contract allows 1 MiB frames. We lift
   the limit via ``limit=MAX_FRAME_SIZE`` on both client and server, so a
   near-max legitimate seal must complete rather than tripping
   ``LimitOverrunError``.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from worthless.ipc.client import (
    IPCAuthError,
    IPCClient,
    IPCProtocolError,
    IPCTimeoutError,
    _err_from_envelope,
)
from worthless.sidecar.backends.base import Backend
from worthless.sidecar.backends.fernet import FernetBackend
from worthless.sidecar.server import start_sidecar


# ---------------------------------------------------------------------------
# Fix 5 (unit): ``_err_from_envelope`` must not double-prefix.
# ---------------------------------------------------------------------------


def test_err_envelope_no_double_prefix() -> None:
    """Server-prefixed ``"AUTH: ..."`` must not become ``"AUTH: AUTH: ..."``.

    The server emits fixed constants like ``"AUTH: peer uid not allowed"``
    that already start with ``"{code}: "``. ``_err_from_envelope`` should
    detect the prefix and skip prepending so callers see a single clean
    code.
    """
    envelope = {
        "v": 1,
        "kind": "err",
        "id": 0,
        "code": "AUTH",
        "message": "AUTH: peer uid not allowed",
    }
    exc = _err_from_envelope(envelope)
    assert isinstance(exc, IPCAuthError)
    assert str(exc) == "AUTH: peer uid not allowed", f"double-prefix regression: got {str(exc)!r}"


def test_err_envelope_prepends_when_missing() -> None:
    """Bare messages still get the code prefix so callers can assert on it."""
    envelope = {"v": 1, "kind": "err", "id": 0, "code": "PROTO", "message": "malformed"}
    exc = _err_from_envelope(envelope)
    assert isinstance(exc, IPCProtocolError)
    assert str(exc) == "PROTO: malformed"


# ---------------------------------------------------------------------------
# Fix 2 (integration): timeout must invalidate the connection.
# ---------------------------------------------------------------------------


class _HangingBackend(Backend):
    """Hangs on ``seal`` forever so the client's timeout trips."""

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


async def test_timeout_invalidates_connection(
    sidecar_socket_path: Path, fernet_backend: FernetBackend
) -> None:
    """After a timeout, the next call must fail fast — not read garbage.

    ``asyncio.wait_for`` cancels ``read_frame`` mid-parse. If the reader has
    consumed a partial length prefix, a subsequent request would hang or
    raise a confusing ``MalformedFrameError``. The client should null its
    reader/writer on timeout so the next call raises a clean
    ``IPCProtocolError("client is not connected")``.
    """
    server = await start_sidecar(
        socket_path=sidecar_socket_path,
        backend=_HangingBackend(fernet_backend),
        allowed_uids=[os.getuid()],
    )
    try:
        async with IPCClient(sidecar_socket_path, timeout=0.15) as client:
            with pytest.raises(IPCTimeoutError):
                await client.seal(b"payload")

            # Second call must see the poisoned connection, not retry and
            # read garbage off the desynchronised socket.
            with pytest.raises(IPCProtocolError) as exc_info:
                await client.attest(b"n" * 32)
            assert "not connected" in str(exc_info.value).lower(), (
                f"timeout must invalidate connection; got {exc_info.value!r}"
            )
    finally:
        server.close()
        try:
            await server.wait_closed()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Fix 3 (integration): err envelopes with id=0 sentinel route to typed exc.
# ---------------------------------------------------------------------------


async def test_err_with_zero_id_routes_to_typed_auth_error(
    sidecar_socket_path: Path, fernet_backend: FernetBackend
) -> None:
    """Server ``err id=0`` must surface as ``IPCAuthError`` for AUTH code.

    Peer-auth failure happens before a request id exists, so the server
    emits the error with ``id=0`` (``_ID_UNKNOWN`` sentinel). If the client
    checks id-mismatch before kind, it raises a generic
    ``IPCProtocolError("id mismatch")`` and callers lose the AUTH signal.

    We trigger this by starting a sidecar with an empty allowlist so every
    peer is rejected.
    """
    server = await start_sidecar(
        socket_path=sidecar_socket_path,
        backend=fernet_backend,
        allowed_uids=[],  # reject every peer
    )
    try:
        with pytest.raises(IPCAuthError) as exc_info:
            # Entering the context manager runs the handshake, which is
            # where auth rejection surfaces.
            async with IPCClient(sidecar_socket_path, timeout=1.0):
                pass
        assert "AUTH" in str(exc_info.value).upper(), (
            f"err with id=0 must route to typed AUTH exc, got {exc_info.value!r}"
        )
    finally:
        server.close()
        try:
            await server.wait_closed()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Fix 4 (integration): near-max frame must roundtrip (StreamReader limit).
# ---------------------------------------------------------------------------


async def test_near_max_frame_roundtrip(ipc_client: IPCClient) -> None:
    """A large plaintext must seal/open without tripping the StreamReader cap.

    Default ``asyncio.StreamReader`` buffer is 64 KiB. Our framing allows
    1 MiB bodies. Without ``limit=MAX_FRAME_SIZE`` on both sides, a frame
    larger than 64 KiB raises ``LimitOverrunError`` and surfaces as a
    confusing protocol failure. This pins the fix end-to-end.

    We pick 600 KiB — well past the default 64 KiB StreamReader limit, but
    under the 1 MiB frame cap even after Fernet's ~1.33x base64 expansion on
    the return path (600 * 1.33 ≈ 800 KiB ciphertext frame).
    """
    plaintext = b"\xa5" * (600 * 1024)  # 600 KiB, non-zero so no accidental nulls

    ciphertext = await ipc_client.seal(plaintext)
    recovered = await ipc_client.open(ciphertext)
    assert recovered == plaintext
