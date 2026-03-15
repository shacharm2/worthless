"""XOR key splitting with HMAC commitment and secure memory zeroing.

This module implements the core cryptographic primitives for Worthless:
- split_key: Splits an API key into two XOR shards with an HMAC commitment
- reconstruct_key: Reconstructs the key from shards after HMAC verification
- secure_key: Context manager that zeros key material on exit
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from contextlib import contextmanager
from typing import Generator

from worthless.crypto.types import SplitResult
from worthless.exceptions import ShardTamperedError


def split_key(api_key: bytes) -> SplitResult:
    """Split an API key into two XOR shards with an HMAC commitment.

    Args:
        api_key: The raw API key bytes to split.

    Returns:
        A SplitResult containing shard_a, shard_b, commitment, and nonce.

    Raises:
        ValueError: If the key is empty.
    """
    if not api_key:
        raise ValueError("Cannot split an empty key")

    # Generate a random mask (shard_b) using CSPRNG
    mask = secrets.token_bytes(len(api_key))

    # XOR the key with the mask to produce shard_a
    shard_a = bytearray(a ^ b for a, b in zip(api_key, mask))
    shard_b = mask

    # Create HMAC commitment over the original key
    nonce = secrets.token_bytes(32)
    commitment = hmac.new(nonce, api_key, hashlib.sha256).digest()

    return SplitResult(
        shard_a=shard_a,
        shard_b=shard_b,
        commitment=commitment,
        nonce=nonce,
    )


def reconstruct_key(
    shard_a: bytes,
    shard_b: bytes,
    commitment: bytes,
    nonce: bytes,
) -> bytearray:
    """Reconstruct the original API key from two XOR shards.

    Verifies the HMAC commitment before returning the key. If verification
    fails, the reconstructed material is zeroed and ShardTamperedError is raised.

    Args:
        shard_a: The first shard (key XOR mask).
        shard_b: The second shard (mask).
        commitment: The HMAC commitment from the split operation.
        nonce: The nonce used to create the commitment.

    Returns:
        A mutable bytearray containing the reconstructed key.

    Raises:
        ValueError: If shard lengths do not match.
        ShardTamperedError: If the HMAC verification fails.
    """
    if len(shard_a) != len(shard_b):
        raise ValueError(
            f"Shard length mismatch: shard_a={len(shard_a)}, shard_b={len(shard_b)}"
        )

    # Reconstruct the key via XOR
    key = bytearray(a ^ b for a, b in zip(shard_a, shard_b))

    try:
        # Verify HMAC commitment
        expected = hmac.new(nonce, key, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, commitment):
            raise ShardTamperedError(
                "HMAC verification failed: shard data has been tampered with"
            )
    except ShardTamperedError:
        key[:] = b"\x00" * len(key)
        raise
    except Exception:
        # Zero key material on any unexpected error during verification
        key[:] = b"\x00" * len(key)
        raise

    return key


@contextmanager
def secure_key(key_buf: bytearray) -> Generator[bytearray, None, None]:
    """Context manager that zeros key material on exit.

    Usage::

        key = reconstruct_key(shard_a, shard_b, commitment, nonce)
        with secure_key(key) as k:
            # Use k to make API call
            ...
        # k is now all zeros

    Args:
        key_buf: A mutable bytearray containing key material.

    Yields:
        The same bytearray, for use within the block.

    Raises:
        TypeError: If key_buf is not a bytearray.
    """
    if not isinstance(key_buf, bytearray):
        raise TypeError(f"secure_key requires a bytearray, got {type(key_buf).__name__}")
    try:
        yield key_buf
    finally:
        key_buf[:] = b"\x00" * len(key_buf)
