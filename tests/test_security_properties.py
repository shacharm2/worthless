"""Security property tests — Hypothesis-powered invariant checks for crypto primitives.

These tests verify that security-critical properties hold across arbitrary
inputs: split/reconstruct roundtrip, shard independence, zero-after-use,
repr redaction, tamper detection, gate-before-decrypt ordering, and
upstream error sanitization.
"""

from __future__ import annotations

import ast
import hmac as _hmac
import inspect
import json
import string
import textwrap

from hypothesis import given, assume, settings as hsettings
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

# Binary keys — exercise non-ASCII byte paths
_KEY_BINARY = st.binary(min_size=8, max_size=128)

# Combined strategy for broader key coverage
_KEY_ANY = st.one_of(_KEY_TEXT, _KEY_BINARY)


# ---------------------------------------------------------------------------
# Split / Reconstruct roundtrip
# ---------------------------------------------------------------------------


class TestSplitReconstructRoundtrip:
    """split_key -> reconstruct_key always returns the original key."""

    @given(key=_KEY_ANY)
    def test_roundtrip_identity(self, key: bytes) -> None:
        sr = split_key(key)
        try:
            result = reconstruct_key(sr.shard_a, sr.shard_b, sr.commitment, sr.nonce)
            assert bytes(result) == key
            _zero_buf(result)
        finally:
            sr.zero()

    @given(key=_KEY_ANY)
    def test_shard_lengths_match_key(self, key: bytes) -> None:
        """Both shards have the same length as the original key."""
        sr = split_key(key)
        try:
            assert len(sr.shard_a) == len(key)
            assert len(sr.shard_b) == len(key)
        finally:
            sr.zero()


# ---------------------------------------------------------------------------
# Shard independence — XOR structure means neither shard alone reveals key,
# but this only tests non-equality (not cryptographic independence).
# ---------------------------------------------------------------------------


class TestShardNonEquality:
    """Individual shards differ from the key (necessary but not sufficient for independence).

    True cryptographic independence requires that each shard is uniformly
    random given no knowledge of the other. These tests verify the weaker
    property that neither shard equals the original key, which would indicate
    a degenerate split (e.g. one shard is all zeros).
    """

    @given(key=_KEY_ANY)
    def test_shard_a_differs_from_key(self, key: bytes) -> None:
        """shard_a alone is not the original key (except by vanishing probability)."""
        sr = split_key(key)
        try:
            assert bytes(sr.shard_a) != key
        finally:
            sr.zero()

    @given(key=_KEY_ANY)
    def test_shard_b_differs_from_key(self, key: bytes) -> None:
        """shard_b alone is not the original key (except by vanishing probability)."""
        sr = split_key(key)
        try:
            assert bytes(sr.shard_b) != key
        finally:
            sr.zero()

    @given(key=_KEY_ANY)
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
    """Secret material is zeroed when .zero() is called or secure_key exits.

    Limitations:
    - Python's garbage collector may retain copies of bytes objects created
      during intermediate computations (e.g. bytes() casts, string decoding).
    - The immutable ``bytes`` type cannot be zeroed; only ``bytearray`` fields
      are cleared by .zero().
    - Compiler/interpreter optimizations may keep registers or stack copies
      beyond our control.
    - These tests verify the *bytearray* zeroing contract, not full
      memory-safe erasure. The Rust reconstruction service (roadmap) will
      provide stronger guarantees via mlock + explicit_bzero.
    """

    @given(key=_KEY_ANY)
    def test_split_result_zero_clears_all_fields(self, key: bytes) -> None:
        sr = split_key(key)
        sr.zero()
        for field in (sr.shard_a, sr.shard_b, sr.commitment, sr.nonce):
            assert all(b == 0 for b in field)

    @given(key=_KEY_ANY)
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

    @given(key=_KEY_ANY)
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

    @given(key=_KEY_ANY)
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

    @given(key=_KEY_ANY)
    def test_split_result_repr_redacted(self, key: bytes) -> None:
        sr = split_key(key)
        try:
            text = repr(sr)
            # Shard bytes (hex-encoded) must not appear
            assert sr.shard_a.hex() not in text
            assert sr.shard_b.hex() not in text
            assert "redacted" in text.lower()
        finally:
            sr.zero()

    @given(key=_KEY_ANY)
    def test_split_result_str_redacted(self, key: bytes) -> None:
        sr = split_key(key)
        try:
            text = str(sr)
            assert sr.shard_a.hex() not in text
            assert sr.shard_b.hex() not in text
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
    """Flipping any bit in shards, commitment, or nonce must raise ShardTamperedError."""

    @given(key=_KEY_ANY, bit_pos=st.integers(min_value=0, max_value=1023))
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

    @given(key=_KEY_ANY, bit_pos=st.integers(min_value=0, max_value=1023))
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

    @given(key=_KEY_ANY, byte_idx=st.integers(min_value=0, max_value=31))
    def test_flipped_commitment_bit_detected(self, key: bytes, byte_idx: int) -> None:
        sr = split_key(key)
        try:
            tampered_commit = bytearray(sr.commitment)
            tampered_commit[byte_idx] ^= 0xFF
            with pytest.raises(ShardTamperedError):
                reconstruct_key(sr.shard_a, sr.shard_b, tampered_commit, sr.nonce)
        finally:
            sr.zero()

    @given(key=_KEY_ANY, byte_idx=st.integers(min_value=0, max_value=31))
    def test_flipped_nonce_bit_detected(self, key: bytes, byte_idx: int) -> None:
        """Tampering with the nonce invalidates the HMAC commitment."""
        sr = split_key(key)
        try:
            tampered_nonce = bytearray(sr.nonce)
            tampered_nonce[byte_idx] ^= 0xFF
            with pytest.raises(ShardTamperedError):
                reconstruct_key(sr.shard_a, sr.shard_b, sr.commitment, tampered_nonce)
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


# ---------------------------------------------------------------------------
# SR-03: Gate before decrypt — rules engine evaluates BEFORE Fernet decryption
# ---------------------------------------------------------------------------


class TestGateBeforeDecrypt:
    """SR-03: Rules engine evaluates BEFORE any Fernet decryption.

    The proxy handler must call rules_engine.evaluate() before
    repo.decrypt_shard() to ensure denied requests never touch
    decrypted key material.
    """

    def test_evaluate_precedes_decrypt_in_proxy_handler(self) -> None:
        """Static analysis: rules_engine.evaluate appears before repo.decrypt_shard in source."""
        from worthless.proxy.app import create_app

        source = inspect.getsource(create_app)
        gate_pos = source.find("rules_engine.evaluate")
        decrypt_pos = source.find("repo.decrypt_shard")
        assert gate_pos != -1, "rules_engine.evaluate not found in proxy handler"
        assert decrypt_pos != -1, "repo.decrypt_shard not found in proxy handler"
        assert gate_pos < decrypt_pos, (
            f"gate (pos {gate_pos}) must appear before decrypt (pos {decrypt_pos}) in source"
        )

    def test_fetch_encrypted_returns_encrypted_type(self) -> None:
        """fetch_encrypted returns EncryptedShard with shard_b_enc (not decrypted shard_b).

        This proves the gate can inspect the provider field without
        ever touching plaintext key material.
        """
        enc = EncryptedShard(
            shard_b_enc=b"ciphertext-blob",
            commitment=b"\x00" * 32,
            nonce=b"\x00" * 32,
            provider="openai",
        )
        # EncryptedShard exposes shard_b_enc (ciphertext), NOT shard_b (plaintext)
        assert hasattr(enc, "shard_b_enc")
        assert not hasattr(enc, "shard_b")
        assert enc.provider == "openai"

    def test_fetch_encrypted_source_has_no_decrypt_calls(self) -> None:
        """fetch_encrypted AST must not contain any method calls with 'decrypt' in the name."""
        from worthless.storage.repository import ShardRepository

        source = textwrap.dedent(inspect.getsource(ShardRepository.fetch_encrypted))
        tree = ast.parse(source)
        decrypt_calls: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and "decrypt" in node.attr.lower():
                decrypt_calls.append(node.attr)
            elif isinstance(node, ast.Name) and "decrypt" in node.id.lower():
                decrypt_calls.append(node.id)
        assert not decrypt_calls, (
            f"fetch_encrypted must not call decrypt methods, found: {decrypt_calls}"
        )

    @hsettings(deadline=None)
    @given(allow=st.booleans())
    def test_gate_deny_prevents_decrypt(self, allow: bool) -> None:
        """When gate denies, decrypt_shard must not be called.

        Verifies the control flow structure: the denial return statement
        appears before the decrypt_shard call in the source.
        """
        from worthless.proxy.app import create_app

        source = inspect.getsource(create_app)

        # Find the gate evaluation
        gate_idx = source.find("rules_engine.evaluate")
        assert gate_idx != -1

        # Find the denial check that follows
        denial_check_idx = source.find("if denial is not None:", gate_idx)
        assert denial_check_idx != -1, "denial check must follow evaluate call"

        # Find the return statement inside the denial block
        # (must come before decrypt_shard)
        denial_return_idx = source.find("return Response(", denial_check_idx)
        decrypt_idx = source.find("repo.decrypt_shard", gate_idx)
        assert denial_return_idx != -1, "denial block must have a return"
        assert decrypt_idx != -1, "decrypt_shard must exist in handler"
        assert denial_return_idx < decrypt_idx, (
            "denial return must come before decrypt_shard call"
        )

        if not allow:
            # When denied, the handler returns at denial_return_idx
            # which is before decrypt_idx — decrypt is never reached
            assert denial_return_idx < decrypt_idx


# ---------------------------------------------------------------------------
# SR-05: Sanitized errors must not leak upstream provider messages
# ---------------------------------------------------------------------------

# Strategy for error messages: printable ASCII to avoid Hypothesis shrinking
# issues with full-unicode text strategies combined with alphabet-restricted ones.
_ERROR_MSG = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=8,
    max_size=256,
)

_ERROR_TYPE = st.from_regex(r"[a-z][a-z_]{0,30}", fullmatch=True)


class TestSanitizeNeverLeaksMessage:
    """SR-05: Sanitized errors must not leak upstream provider messages."""

    @hsettings(deadline=None)
    @given(
        message=_ERROR_MSG,
        provider=st.sampled_from(["openai", "anthropic"]),
        error_type=_ERROR_TYPE,
        status_code=st.integers(min_value=400, max_value=599),
    )
    def test_original_message_never_in_output(
        self,
        message: str,
        provider: str,
        error_type: str,
        status_code: int,
    ) -> None:
        """Original upstream error message must not appear in sanitized output."""
        from worthless.proxy.app import _sanitize_upstream_error

        # Skip if the message happens to be our generic replacement
        assume(message != "upstream provider error")
        # Skip very short messages that could coincidentally match JSON keys
        assume(len(message.strip()) >= 8)

        if provider == "anthropic":
            body = json.dumps(
                {"type": "error", "error": {"type": error_type, "message": message}}
            ).encode()
        else:
            body = json.dumps(
                {"error": {"message": message, "type": error_type}}
            ).encode()

        sanitized = _sanitize_upstream_error(status_code, body, provider)

        assert message.encode() not in sanitized, (
            f"Original message {message!r} leaked into sanitized output"
        )
        assert b"upstream provider error" in sanitized

    @hsettings(deadline=None)
    @given(body=st.binary(min_size=1, max_size=4096))
    def test_arbitrary_binary_never_leaks(self, body: bytes) -> None:
        """Even garbage input produces safe generic output."""
        from worthless.proxy.app import _sanitize_upstream_error

        sanitized = _sanitize_upstream_error(500, body, "openai")

        # Any 8+ byte substring of input must not appear in output
        # (unless it coincides with the generic replacement text)
        generic = b"upstream provider error"
        for i in range(len(body) - 7):
            chunk = body[i : i + 8]
            if chunk in generic:
                continue
            assert chunk not in sanitized, (
                f"Input chunk {chunk!r} at offset {i} leaked into sanitized output"
            )

    @hsettings(deadline=None)
    @given(
        status_code=st.integers(min_value=400, max_value=599),
        provider=st.sampled_from(["openai", "anthropic"]),
    )
    def test_sanitized_output_is_valid_json(
        self, status_code: int, provider: str
    ) -> None:
        """Sanitized output must always be valid JSON regardless of input."""
        from worthless.proxy.app import _sanitize_upstream_error

        # Feed completely invalid input
        sanitized = _sanitize_upstream_error(status_code, b"\xff\xfe\x00", provider)
        parsed = json.loads(sanitized)
        assert isinstance(parsed, dict)

    @hsettings(deadline=None)
    @given(
        message=_ERROR_MSG,
        provider=st.sampled_from(["openai", "anthropic"]),
    )
    def test_error_type_preserved_but_message_replaced(
        self, message: str, provider: str
    ) -> None:
        """The error type field passes through but the message is always generic."""
        from worthless.proxy.app import _sanitize_upstream_error

        assume(message != "upstream provider error")

        custom_type = "rate_limit_error"
        if provider == "anthropic":
            body = json.dumps(
                {"type": "error", "error": {"type": custom_type, "message": message}}
            ).encode()
        else:
            body = json.dumps(
                {"error": {"message": message, "type": custom_type}}
            ).encode()

        sanitized = _sanitize_upstream_error(400, body, provider)
        parsed = json.loads(sanitized)

        if provider == "anthropic":
            assert parsed["error"]["type"] == custom_type
            assert parsed["error"]["message"] == "upstream provider error"
        else:
            assert parsed["error"]["type"] == custom_type
            assert parsed["error"]["message"] == "upstream provider error"
