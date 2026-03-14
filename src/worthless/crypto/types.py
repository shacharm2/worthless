"""Cryptographic data types for the Worthless key-splitting system."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SplitResult:
    """Result of splitting an API key into two XOR shards with HMAC commitment.

    All byte fields are redacted in ``__repr__`` to prevent accidental logging
    of key material.
    """

    shard_a: bytes
    shard_b: bytes
    commitment: bytes
    nonce: bytes

    def __repr__(self) -> str:
        return (
            "SplitResult("
            "shard_a=<redacted>, "
            "shard_b=<redacted>, "
            "commitment=<redacted>, "
            "nonce=<redacted>)"
        )
