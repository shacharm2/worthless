"""Length-prefix + msgpack framing for proxy↔sidecar IPC.

Wire format (see ``docs/ipc-contract.md`` §Frame)::

    ┌─────────────┬──────────────────────────┐
    │ length (4B) │  msgpack-encoded envelope │
    │  uint32 BE  │       (≤ length bytes)    │
    └─────────────┴──────────────────────────┘

All frame bodies MUST be msgpack-encoded ``dict`` envelopes. Max frame size
is ``MAX_FRAME_SIZE`` — larger frames raise :class:`FrameTooLargeError` and
the connection MUST be closed by the caller.

This module is used by BOTH sides of the IPC: proxy client and sidecar
server. Keep it primitive-agnostic — no Fernet/MPC/KMS assumptions here.
"""

from __future__ import annotations

import asyncio
from typing import Any
from collections.abc import Mapping

import msgpack

__all__ = [
    "MAX_FRAME_SIZE",
    "FrameError",
    "FrameTooLargeError",
    "FrameTruncatedError",
    "MalformedFrameError",
    "encode_frame",
    "read_frame",
]

#: Largest permitted frame body in bytes (1 MiB). Anything larger is treated
#: as hostile or buggy and rejected before allocation.
MAX_FRAME_SIZE = 1024 * 1024

_HEADER_LEN = 4


class FrameError(Exception):
    """Base class for all frame-level protocol errors."""


class FrameTooLargeError(FrameError):
    """Frame exceeds :data:`MAX_FRAME_SIZE`.

    Raised on both encode (refused to serialize) and decode (refused to
    allocate a buffer for a declared-oversized frame).
    """


class FrameTruncatedError(FrameError):
    """Stream ended mid-frame.

    Typically means the peer closed the socket unexpectedly.
    """


class MalformedFrameError(FrameError):
    """Frame body is not a valid msgpack-encoded dict envelope."""


def encode_frame(envelope: Mapping[str, Any]) -> bytes:
    """Serialize ``envelope`` to length-prefixed msgpack bytes.

    Args:
        envelope: Mapping that will be msgpack-encoded. ``bytes`` values in
            the body are preserved as bytes (not coerced to str) — critical
            for ``seal``/``open`` plaintext/ciphertext payloads.

    Raises:
        FrameTooLargeError: encoded body would exceed :data:`MAX_FRAME_SIZE`.
    """
    payload = msgpack.packb(dict(envelope), use_bin_type=True)
    if len(payload) > MAX_FRAME_SIZE:
        raise FrameTooLargeError(f"encoded frame is {len(payload)} bytes (max {MAX_FRAME_SIZE})")
    return len(payload).to_bytes(_HEADER_LEN, "big") + payload


async def read_frame(reader: asyncio.StreamReader) -> dict[str, Any]:
    """Read a single length-prefixed msgpack frame from ``reader``.

    Leaves any subsequent bytes in the stream untouched so back-to-back
    frames can be parsed by repeated calls.

    Raises:
        FrameTruncatedError: stream closed before a complete frame was read.
        FrameTooLargeError: declared length exceeds :data:`MAX_FRAME_SIZE`.
        MalformedFrameError: body is not valid msgpack, or decodes to a
            non-dict (envelopes must be dicts per contract).
    """
    # --- header
    try:
        header = await reader.readexactly(_HEADER_LEN)
    except asyncio.IncompleteReadError as exc:
        raise FrameTruncatedError(
            f"stream ended after {len(exc.partial)} of {_HEADER_LEN} header bytes"
        ) from exc

    length = int.from_bytes(header, "big")
    if length == 0:
        raise MalformedFrameError("zero-length frame")
    if length > MAX_FRAME_SIZE:
        # Refuse to allocate: a hostile peer could declare 4 GiB and OOM us.
        raise FrameTooLargeError(f"frame declares {length} bytes (max {MAX_FRAME_SIZE})")

    # --- body
    try:
        payload = await reader.readexactly(length)
    except asyncio.IncompleteReadError as exc:
        raise FrameTruncatedError(
            f"stream ended after {len(exc.partial)} of {length} body bytes"
        ) from exc

    # --- decode
    try:
        decoded = msgpack.unpackb(payload, raw=False)
    except Exception as exc:  # msgpack raises several error types; treat all as malformed
        raise MalformedFrameError(f"invalid msgpack payload: {exc}") from exc

    if not isinstance(decoded, dict):
        raise MalformedFrameError(
            f"frame payload decoded to {type(decoded).__name__}, expected dict"
        )
    return decoded
