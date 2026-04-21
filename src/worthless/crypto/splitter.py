"""Format-preserving key splitting with HMAC commitment and secure memory zeroing.

This module implements the core cryptographic primitives for Worthless:
- split_key_fp: Format-preserving split — shard-A keeps the key's prefix/charset/length
- reconstruct_key_fp: Reconstructs from format-preserving shards after HMAC verification
- split_key: Legacy byte-level XOR split (to be removed — no production users)
- reconstruct_key: Legacy byte-level XOR reconstruct (to be removed — no production users)
- secure_key: Context manager that zeros key material on exit
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from contextlib import contextmanager
from collections.abc import Generator

from worthless.crypto.charsets import ALPHANUMERIC, BASE64URL, PRINTABLE, PROVIDER_CHARSETS
from worthless.crypto.types import FormatPreservingSplitResult, SplitResult, zero_buf
from worthless.exceptions import ShardTamperedError

# ---------------------------------------------------------------------------
# Precomputed lookups for format-preserving split (SR-12)
# ---------------------------------------------------------------------------

_BASE64URL_SET = frozenset(BASE64URL)
_ALPHANUMERIC_SET = frozenset(ALPHANUMERIC)
_PRINTABLE_SET = frozenset(PRINTABLE)

_CHAR_TO_IDX: dict[str, dict[str, int]] = {
    cs: {c: i for i, c in enumerate(cs)} for cs in (ALPHANUMERIC, BASE64URL, PRINTABLE)
}


def _detect_charset(body: str, provider: str | None = None) -> str:
    """Determine the minimal charset that covers all characters in *body*.

    Tries provider-specific charset first, then falls back to broader
    charsets.
    """
    if provider and provider in PROVIDER_CHARSETS:
        cs = PROVIDER_CHARSETS[provider]
        if all(c in _CHAR_TO_IDX[cs] for c in body):
            return cs

    for cs, cs_set in (
        (ALPHANUMERIC, _ALPHANUMERIC_SET),
        (BASE64URL, _BASE64URL_SET),
        (PRINTABLE, _PRINTABLE_SET),
    ):
        if all(c in cs_set for c in body):
            return cs

    # SR-04: don't leak key characters in exceptions
    raise ValueError(
        f"Key body contains {sum(1 for c in body if c not in _PRINTABLE_SET)} "
        "character(s) outside printable ASCII"
    )


# ---------------------------------------------------------------------------
# HMAC commitment helpers (shared by FP and legacy paths)
# ---------------------------------------------------------------------------


def _make_commitment(payload: bytes | bytearray) -> tuple[bytearray, bytearray]:
    """Create an HMAC-SHA256 commitment over the given payload.

    Returns (commitment, nonce).
    """
    # mutmut: skip — token_bytes(None) defaults to 32; equivalent mutant
    nonce = bytearray(secrets.token_bytes(32))
    commitment = bytearray(
        hmac.new(nonce, payload, hashlib.sha256).digest()  # nosec B303 — HMAC-SHA256
    )
    return commitment, nonce


def _verify_commitment(
    payload: bytes | bytearray,
    commitment: bytes | bytearray,
    nonce: bytes | bytearray,
) -> None:
    """Verify HMAC-SHA256 commitment. Raises ShardTamperedError on mismatch."""
    expected = bytearray(
        hmac.new(nonce, payload, hashlib.sha256).digest()  # nosec B303 — HMAC-SHA256
    )
    try:
        if not hmac.compare_digest(expected, commitment):
            raise ShardTamperedError("HMAC verification failed: shard data has been tampered with")
    finally:
        zero_buf(expected)


# ---------------------------------------------------------------------------
# Format-preserving split/reconstruct (SR-12)
# ---------------------------------------------------------------------------


def split_key_fp(
    api_key: str, prefix: str, provider: str | None = None
) -> FormatPreservingSplitResult:
    """Split an API key using format-preserving modular arithmetic.

    Shard-A preserves the key's prefix, charset, and length — it is
    indistinguishable from a real API key (SR-12).  The split uses a
    one-time pad over Z/N where N is the charset size.

    Args:
        api_key: The full API key string (e.g. "sk-proj-abc123...").
        prefix: The prefix to preserve verbatim (e.g. "sk-proj-").
        provider: Provider name for charset selection (optional).

    Returns:
        A FormatPreservingSplitResult with format-valid shard_a and shard_b.
    """
    if not api_key:
        raise ValueError("Cannot split an empty key")
    if not api_key.startswith(prefix):
        raise ValueError(f"Key does not start with prefix {prefix!r}")

    body = api_key[len(prefix) :]
    if not body:
        raise ValueError("Key body is empty after prefix removal")

    charset = _detect_charset(body, provider)
    n = len(charset)
    char_to_idx = _CHAR_TO_IDX[charset]

    shard_a_chars: list[str] = []
    shard_b_chars: list[str] = []
    for char in body:
        original_idx = char_to_idx[char]
        mask = secrets.randbelow(n)
        shard_a_chars.append(charset[(original_idx - mask) % n])
        shard_b_chars.append(charset[mask])

    shard_a_str = prefix + "".join(shard_a_chars)
    shard_b_str = "".join(shard_b_chars)

    commitment, nonce = _make_commitment(bytearray(api_key.encode("utf-8")))

    return FormatPreservingSplitResult(
        shard_a=bytearray(shard_a_str.encode("utf-8")),
        shard_b=bytearray(shard_b_str.encode("utf-8")),
        commitment=commitment,
        nonce=nonce,
        prefix=prefix,
        charset=charset,
    )


def derive_shard_a_fp(
    api_key: str,
    shard_b: bytes | bytearray,
    prefix: str,
    charset: str,
) -> bytearray:
    """Derive the shard-A that pairs with *shard_b* to reconstruct *api_key*.

    Re-lock path: when an alias is already enrolled (shard-B stored), locking
    the same real key in a new .env must recreate the format-preserving
    shard-A rather than leaving the real key in the file. Inverting the
    modular split over charset index space: shard_a_idx = (key_idx - shard_b_idx) mod N.
    """
    body = api_key[len(prefix) :]
    b_body = bytes(shard_b).decode("utf-8")

    if len(body) != len(b_body):
        raise ValueError(f"Shard-B length mismatch: key_body={len(body)}, shard_b={len(b_body)}")

    n = len(charset)
    char_to_idx = _CHAR_TO_IDX[charset]

    shard_a_chars: list[str] = []
    for k_char, b_char in zip(body, b_body, strict=True):
        shard_a_chars.append(charset[(char_to_idx[k_char] - char_to_idx[b_char]) % n])

    return bytearray((prefix + "".join(shard_a_chars)).encode("utf-8"))


def reconstruct_key_fp(
    shard_a: bytes | bytearray,
    shard_b: bytes | bytearray,
    commitment: bytes | bytearray,
    nonce: bytes | bytearray,
    prefix: str,
    charset: str,
) -> bytearray:
    """Reconstruct the original API key from format-preserving shards.

    Verifies the HMAC commitment before returning.  If verification fails,
    all intermediate material is zeroed and ShardTamperedError is raised.

    Args:
        shard_a: UTF-8 encoded shard-A (prefix + randomized body).
        shard_b: UTF-8 encoded shard-B (body only).
        commitment: HMAC commitment from split.
        nonce: Nonce used for commitment.
        prefix: The preserved prefix string.
        charset: The charset used for the split.

    Returns:
        A mutable bytearray containing the reconstructed key (UTF-8).
    """
    # bytearray.decode() avoids an intermediate un-zeroable bytes copy.
    # When input is already bytearray, decode directly. Otherwise copy
    # into a temporary bytearray, decode, then zero the copy.
    if isinstance(shard_a, bytearray):
        shard_a_str = shard_a.decode("utf-8")
    else:
        tmp = bytearray(shard_a)
        shard_a_str = tmp.decode("utf-8")
        tmp[:] = b"\x00" * len(tmp)

    if isinstance(shard_b, bytearray):
        shard_b_str = shard_b.decode("utf-8")
    else:
        tmp = bytearray(shard_b)
        shard_b_str = tmp.decode("utf-8")
        tmp[:] = b"\x00" * len(tmp)

    if not shard_a_str.startswith(prefix):
        raise ValueError(f"Shard-A does not start with expected prefix {prefix!r}")

    a_body = shard_a_str[len(prefix) :]
    b_body = shard_b_str

    if len(a_body) != len(b_body):
        raise ValueError(f"Shard body length mismatch: a={len(a_body)}, b={len(b_body)}")

    n = len(charset)
    char_to_idx = _CHAR_TO_IDX[charset]

    # Build key directly as bytearray to avoid un-zeroable intermediate str.
    # All API key chars are ASCII so ord() gives the correct UTF-8 byte.
    prefix_bytes = prefix.encode("utf-8")
    key = bytearray(len(prefix_bytes) + len(a_body))
    key[: len(prefix_bytes)] = prefix_bytes
    for i, (a_char, b_char) in enumerate(zip(a_body, b_body, strict=True)):
        original_idx = (char_to_idx[a_char] + char_to_idx[b_char]) % n
        key[len(prefix_bytes) + i] = ord(charset[original_idx])

    try:
        _verify_commitment(key, commitment, nonce)
    except Exception:
        zero_buf(key)
        raise

    return key


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

    mask = bytearray(secrets.token_bytes(len(api_key)))
    shard_a = bytearray(a ^ b for a, b in zip(api_key, mask, strict=True))

    commitment, nonce = _make_commitment(bytearray(api_key))

    return SplitResult(
        shard_a=shard_a,
        shard_b=mask,
        commitment=commitment,
        nonce=nonce,
    )


def reconstruct_key(
    shard_a: bytes | bytearray,
    shard_b: bytes | bytearray,
    commitment: bytes | bytearray,
    nonce: bytes | bytearray,
) -> bytearray:
    """Reconstruct the original API key from two XOR shards.

    Verifies the HMAC commitment before returning the key.  If verification
    fails, all intermediate material is zeroed and ShardTamperedError is raised.

    Accepts both ``bytes`` and ``bytearray`` for all inputs — callers are not
    forced to pre-convert, but note that immutable ``bytes`` inputs cannot be
    zeroed by this function (caller's responsibility per SR-01).

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
        raise ValueError(f"Shard length mismatch: shard_a={len(shard_a)}, shard_b={len(shard_b)}")

    key = bytearray(a ^ b for a, b in zip(shard_a, shard_b, strict=True))

    try:
        _verify_commitment(key, commitment, nonce)
    except Exception:
        zero_buf(key)
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
    if not isinstance(key_buf, bytearray):  # type: ignore[reportUnnecessaryIsInstance] — runtime guard for untyped callers
        raise TypeError(f"secure_key requires a bytearray, got {type(key_buf).__name__}")
    try:
        yield key_buf
    finally:
        zero_buf(key_buf)
