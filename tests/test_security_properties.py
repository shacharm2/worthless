"""Security property tests — Hypothesis-powered invariant checks for crypto primitives.

These tests verify that security-critical properties hold across arbitrary
inputs: split/reconstruct roundtrip, shard independence, zero-after-use,
repr redaction, and tamper detection.
"""

from __future__ import annotations

import hmac as _hmac

from hypothesis import given
from hypothesis import strategies as st

from worthless.crypto.splitter import reconstruct_key, secure_key, split_key
from worthless.crypto.types import SplitResult, _zero_buf
from worthless.exceptions import ShardTamperedError
from worthless.storage.repository import EncryptedShard, StoredShard

import pytest

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# API keys: 8-128 printable ASCII bytes (real keys are 32-64 chars)
_API_KEYS = st.binary(min_size=8, max_size=128).filter(lambda b: b == b"".join(
    bytes([c]) for c in b if 32 <= c < 127
))

# Simpler: just use text-based keys
_KEY_TEXT = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_",
    min_size=8,
    max_size=128,
).map(lambda s: s.encode())


# ---------------------------------------------------------------------------
# Split / Reconstruct roundtrip
# ---------------------------------------------------------------------------


class TestSplitReconstructRoundtrip:
    """split_key → reconstruct_key always returns the original key."""

    @given(key=_KEY_TEXT)
    def test_roundtrip_identity(self, key: bytes) -> None:
        sr = split_key(key)
        try:
            result = reconstruct_key(sr.shard_a, sr.shard_b, sr.commitment, sr.nonce)
            assert bytes(result) == key
            _zero_buf(result)
        finally:
            sr.zero()

    @given(key=_KEY_TEXT)
    def test_shard_lengths_match_key(self, key: bytes) -> None:
        """Both shards have the same length as the original key."""
        sr = split_key(key)
        try:
            assert len(sr.shard_a) == len(key)
            assert len(sr.shard_b) == len(key)
        finally:
            sr.zero()


# ---------------------------------------------------------------------------
# Shard independence — shard_a XOR shard_b = key, but neither reveals key alone
# ---------------------------------------------------------------------------


class TestShardIndependence:
    """Individual shards should not reveal the key on their own."""

    @given(key=_KEY_TEXT)
    def test_shard_a_differs_from_key(self, key: bytes) -> None:
        """shard_a alone is not the original key (except by vanishing probability)."""
        sr = split_key(key)
        try:
            assert bytes(sr.shard_a) != key
        finally:
            sr.zero()

    @given(key=_KEY_TEXT)
    def test_shard_b_differs_from_key(self, key: bytes) -> None:
        """shard_b alone is not the original key (except by vanishing probability)."""
        sr = split_key(key)
        try:
            assert bytes(sr.shard_b) != key
        finally:
            sr.zero()

    @given(key=_KEY_TEXT)
    def test_xor_reconstruction(self, key: bytes) -> None:
        """shard_a XOR shard_b = original key."""
        sr = split_key(key)
        try:
            xored = bytearray(a ^ b for a, b in zip(sr.shard_a, sr.shard_b))
            assert bytes(xored) == key
        finally:
            sr.zero()


# ---------------------------------------------------------------------------
# Zero-after-use
# ---------------------------------------------------------------------------


class TestZeroAfterUse:
    """Secret material is zeroed when .zero() is called or secure_key exits."""

    @given(key=_KEY_TEXT)
    def test_split_result_zero_clears_all_fields(self, key: bytes) -> None:
        sr = split_key(key)
        sr.zero()
        for field in (sr.shard_a, sr.shard_b, sr.commitment, sr.nonce):
            assert all(b == 0 for b in field)

    @given(key=_KEY_TEXT)
    def test_secure_key_zeros_on_exit(self, key: bytes) -> None:
        sr = split_key(key)
        try:
            result = reconstruct_key(sr.shard_a, sr.shard_b, sr.commitment, sr.nonce)
            with secure_key(result) as k:
                assert bytes(k) == key
            # After context exit, buffer is zeroed
            assert all(b == 0 for b in result)
        finally:
            sr.zero()

    @given(key=_KEY_TEXT)
    def test_secure_key_zeros_on_exception(self, key: bytes) -> None:
        """secure_key zeros even if the block raises."""
        sr = split_key(key)
        try:
            result = reconstruct_key(sr.shard_a, sr.shard_b, sr.commitment, sr.nonce)
            with pytest.raises(ValueError):
                with secure_key(result):
                    raise ValueError("deliberate")
            assert all(b == 0 for b in result)
        finally:
            sr.zero()

    @given(key=_KEY_TEXT)
    def test_stored_shard_zero(self, key: bytes) -> None:
        sr = split_key(key)
        stored = StoredShard(
            shard_b=bytearray(sr.shard_b),
            commitment=bytearray(sr.commitment),
            nonce=bytearray(sr.nonce),
            provider="test",
        )
        sr.zero()
        stored.zero()
        for field in (stored.shard_b, stored.commitment, stored.nonce):
            assert all(b == 0 for b in field)


# ---------------------------------------------------------------------------
# Repr redaction — no secret bytes in string output
# ---------------------------------------------------------------------------


class TestReprRedaction:
    """__repr__ must never leak secret bytes."""

    @given(key=_KEY_TEXT)
    def test_split_result_repr_redacted(self, key: bytes) -> None:
        sr = split_key(key)
        try:
            text = repr(sr)
            assert key.decode() not in text
            assert "redacted" in text.lower()
        finally:
            sr.zero()

    @given(key=_KEY_TEXT)
    def test_split_result_str_redacted(self, key: bytes) -> None:
        sr = split_key(key)
        try:
            text = str(sr)
            assert key.decode() not in text
            assert "redacted" in text.lower()
        finally:
            sr.zero()

    @given(shard_b=st.binary(min_size=8, max_size=64))
    def test_stored_shard_repr_no_bytes(self, shard_b: bytes) -> None:
        stored = StoredShard(
            shard_b=bytearray(shard_b),
            commitment=bytearray(b"\x00" * 32),
            nonce=bytearray(b"\x00" * 32),
            provider="test",
        )
        text = repr(stored)
        assert str(shard_b) not in text
        assert "bytes>" in text  # shows length, not content

    @given(shard_b_enc=st.binary(min_size=8, max_size=64))
    def test_encrypted_shard_repr_no_bytes(self, shard_b_enc: bytes) -> None:
        enc = EncryptedShard(
            shard_b_enc=shard_b_enc,
            commitment=b"\x00" * 32,
            nonce=b"\x00" * 32,
            provider="test",
        )
        text = repr(enc)
        assert str(shard_b_enc) not in text
        assert "bytes>" in text


# ---------------------------------------------------------------------------
# Tamper detection
# ---------------------------------------------------------------------------


class TestTamperDetection:
    """Flipping any bit in shards or commitment must raise ShardTamperedError."""

    @given(key=_KEY_TEXT, bit_pos=st.integers(min_value=0, max_value=1023))
    def test_flipped_shard_a_bit_detected(self, key: bytes, bit_pos: int) -> None:
        sr = split_key(key)
        try:
            tampered_a = bytearray(sr.shard_a)
            idx = bit_pos % len(tampered_a)
            tampered_a[idx] ^= 1 << (bit_pos % 8)
            if tampered_a == sr.shard_a:
                return  # no-op flip (can happen if bit already matched)
            with pytest.raises(ShardTamperedError):
                reconstruct_key(tampered_a, sr.shard_b, sr.commitment, sr.nonce)
        finally:
            sr.zero()

    @given(key=_KEY_TEXT, bit_pos=st.integers(min_value=0, max_value=1023))
    def test_flipped_shard_b_bit_detected(self, key: bytes, bit_pos: int) -> None:
        sr = split_key(key)
        try:
            tampered_b = bytearray(sr.shard_b)
            idx = bit_pos % len(tampered_b)
            tampered_b[idx] ^= 1 << (bit_pos % 8)
            if tampered_b == sr.shard_b:
                return
            with pytest.raises(ShardTamperedError):
                reconstruct_key(sr.shard_a, tampered_b, sr.commitment, sr.nonce)
        finally:
            sr.zero()

    @given(key=_KEY_TEXT, byte_idx=st.integers(min_value=0, max_value=31))
    def test_flipped_commitment_bit_detected(self, key: bytes, byte_idx: int) -> None:
        sr = split_key(key)
        try:
            tampered_commit = bytearray(sr.commitment)
            tampered_commit[byte_idx] ^= 0xFF
            with pytest.raises(ShardTamperedError):
                reconstruct_key(sr.shard_a, sr.shard_b, tampered_commit, sr.nonce)
        finally:
            sr.zero()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Boundary conditions for crypto primitives."""

    def test_empty_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            split_key(b"")

    def test_secure_key_rejects_bytes(self) -> None:
        with pytest.raises(TypeError, match="bytearray"):
            with secure_key(b"not mutable"):  # type: ignore[arg-type]
                pass

    def test_shard_length_mismatch_rejected(self) -> None:
        with pytest.raises(ValueError, match="mismatch"):
            reconstruct_key(
                bytearray(b"short"),
                bytearray(b"longer_shard"),
                bytearray(b"\x00" * 32),
                bytearray(b"\x00" * 32),
            )
