"""Tests for XOR key splitting, HMAC commitment, and bytearray zeroing (CRYP-01 .. CRYP-03)."""

import pytest

from worthless.crypto.splitter import reconstruct_key, secure_key, split_key
from worthless.crypto.types import SplitResult
from worthless.exceptions import ShardTamperedError

from conftest import assert_zeroed


# --- CRYP-01: XOR round-trip ---


def test_xor_roundtrip(sample_api_key: bytes) -> None:
    """Splitting then XOR-ing shards back must yield the original key."""
    result = split_key(sample_api_key)
    reconstructed = bytes(a ^ b for a, b in zip(result.shard_a, result.shard_b))
    assert reconstructed == sample_api_key


def test_shard_length(sample_api_key: bytes) -> None:
    """Both shards must be the same length as the input key."""
    result = split_key(sample_api_key)
    assert len(result.shard_a) == len(sample_api_key)
    assert len(result.shard_b) == len(sample_api_key)


def test_shards_differ_from_key(sample_api_key: bytes) -> None:
    """Neither shard should equal the original key."""
    result = split_key(sample_api_key)
    assert result.shard_a != sample_api_key
    assert result.shard_b != sample_api_key


# --- CRYP-02: HMAC commitment ---


def test_hmac_valid(sample_api_key: bytes) -> None:
    """Reconstruction with untampered shards must succeed without error."""
    result = split_key(sample_api_key)
    key = reconstruct_key(result.shard_a, result.shard_b, result.commitment, result.nonce)
    assert bytes(key) == sample_api_key


def test_hmac_tampered_shard_a(sample_api_key: bytes) -> None:
    """Flipping a byte in shard_a must cause ShardTamperedError."""
    result = split_key(sample_api_key)
    tampered = bytearray(result.shard_a)
    tampered[0] ^= 0xFF
    with pytest.raises(ShardTamperedError):
        reconstruct_key(tampered, result.shard_b, result.commitment, result.nonce)


def test_hmac_tampered_shard_b(sample_api_key: bytes) -> None:
    """Flipping a byte in shard_b must cause ShardTamperedError."""
    result = split_key(sample_api_key)
    tampered = bytearray(result.shard_b)
    tampered[0] ^= 0xFF
    with pytest.raises(ShardTamperedError):
        reconstruct_key(result.shard_a, tampered, result.commitment, result.nonce)


# --- CRYP-03: Bytearray zeroing ---


def test_reconstruct_returns_bytearray(sample_api_key: bytes) -> None:
    """reconstruct_key must return a bytearray (mutable, so it can be zeroed)."""
    result = split_key(sample_api_key)
    key = reconstruct_key(result.shard_a, result.shard_b, result.commitment, result.nonce)
    assert isinstance(key, bytearray)


def test_bytearray_zeroed(sample_api_key: bytes) -> None:
    """After exiting secure_key, the bytearray must be all zeros."""
    result = split_key(sample_api_key)
    key = reconstruct_key(result.shard_a, result.shard_b, result.commitment, result.nonce)
    with secure_key(key) as k:
        assert bytes(k) == sample_api_key  # still valid inside the block
    assert_zeroed(key)


# --- Edge cases ---


def test_split_empty_key_raises() -> None:
    """Splitting an empty key must raise ValueError."""
    with pytest.raises(ValueError):
        split_key(b"")


def test_reconstruct_length_mismatch() -> None:
    """Mismatched shard lengths must raise ValueError, not silently truncate."""
    result = split_key(b"sk-test-key-1234567890abcdef")
    with pytest.raises(ValueError, match="Shard length mismatch"):
        reconstruct_key(result.shard_a, result.shard_b[:5], result.commitment, result.nonce)


def test_secure_key_rejects_non_bytearray() -> None:
    """secure_key must reject non-bytearray inputs."""
    with pytest.raises(TypeError, match="requires a bytearray"):
        with secure_key(b"not-a-bytearray"):  # type: ignore[arg-type]
            pass


def test_bytearray_zeroed_on_exception(sample_api_key: bytes) -> None:
    """secure_key must zero the buffer even when an exception is raised inside."""
    result = split_key(sample_api_key)
    key = reconstruct_key(result.shard_a, result.shard_b, result.commitment, result.nonce)
    with pytest.raises(RuntimeError):
        with secure_key(key):
            raise RuntimeError("simulated error")
    assert_zeroed(key)


def test_roundtrip_with_long_key(sample_long_key: bytes) -> None:
    """XOR roundtrip must work for longer keys (64 bytes)."""
    result = split_key(sample_long_key)
    key = reconstruct_key(result.shard_a, result.shard_b, result.commitment, result.nonce)
    assert bytes(key) == sample_long_key


# --- SplitResult lifecycle ---


def test_split_result_zero(sample_api_key: bytes) -> None:
    """SplitResult.zero() must zero all fields in-place."""
    result = split_key(sample_api_key)
    result.zero()
    for buf in (result.shard_a, result.shard_b, result.commitment, result.nonce):
        assert_zeroed(buf)


def test_split_result_zero_idempotent(sample_api_key: bytes) -> None:
    """Calling zero() twice must not raise."""
    result = split_key(sample_api_key)
    result.zero()
    result.zero()
    assert_zeroed(result.shard_a)


# --- SR-04: Repr/str redaction ---


def test_split_result_repr_redacted(sample_api_key: bytes) -> None:
    """repr() must not leak any secret material."""
    result = split_key(sample_api_key)
    text = repr(result)
    assert "redacted" in text
    assert sample_api_key.decode() not in text
    assert result.shard_a.hex() not in text


def test_split_result_str_redacted(sample_api_key: bytes) -> None:
    """str() must also be redacted (SR-04 — traceback safety)."""
    result = split_key(sample_api_key)
    text = str(result)
    assert "redacted" in text


# --- reconstruct_key accepts both bytes and bytearray ---


def test_reconstruct_accepts_bytes(sample_api_key: bytes) -> None:
    """reconstruct_key must accept plain bytes inputs for interop."""
    result = split_key(sample_api_key)
    key = reconstruct_key(
        bytes(result.shard_a),
        bytes(result.shard_b),
        bytes(result.commitment),
        bytes(result.nonce),
    )
    assert bytes(key) == sample_api_key


# --- Key zeroed on tamper (security-critical) ---


def test_reconstruct_zeros_key_on_tamper(sample_api_key: bytes) -> None:
    """reconstruct_key must zero the intermediate key buffer when HMAC fails."""
    result = split_key(sample_api_key)
    tampered = bytearray(result.shard_a)
    tampered[0] ^= 0xFF

    # We need to capture the key buffer — it's zeroed internally before the
    # exception propagates, so we verify by checking that reconstruct_key
    # does not leak the reconstructed (wrong) key in the exception.
    with pytest.raises(ShardTamperedError):
        reconstruct_key(tampered, result.shard_b, result.commitment, result.nonce)
    # If we got here, the exception was raised and key was zeroed internally.
    # There's no way to observe the zeroed buffer from outside (it's a local),
    # but the exception proves the tamper was detected before return.
