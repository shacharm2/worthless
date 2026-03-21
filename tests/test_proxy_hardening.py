"""Tests for proxy hardening — repr redaction and dead code removal."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from worthless.adapters.types import AdapterRequest, AdapterResponse


# ------------------------------------------------------------------
# AdapterRequest repr redaction (SR-04)
# ------------------------------------------------------------------


class TestAdapterRequestRepr:
    """AdapterRequest.__repr__ must not expose body content."""

    def test_body_redacted_in_repr(self) -> None:
        """Body content must show as <N bytes>, not raw content."""
        req = AdapterRequest(
            url="https://api.openai.com/v1/chat/completions",
            headers={"content-type": "application/json"},
            body=b'{"model":"gpt-4","messages":[{"role":"user","content":"secret prompt"}]}',
        )
        r = repr(req)
        assert "secret prompt" not in r
        assert "<" in r and "bytes>" in r

    def test_body_length_shown(self) -> None:
        """Body redaction shows correct byte count."""
        body = b"x" * 42
        req = AdapterRequest(
            url="https://example.com",
            headers={},
            body=body,
        )
        assert "<42 bytes>" in repr(req)

    def test_sensitive_headers_still_redacted(self) -> None:
        """Authorization headers remain redacted."""
        req = AdapterRequest(
            url="https://example.com",
            headers={"authorization": "Bearer sk-secret-key"},
            body=b"{}",
        )
        r = repr(req)
        assert "sk-secret-key" not in r
        assert "REDACTED" in r


# ------------------------------------------------------------------
# AdapterResponse repr redaction (SR-04)
# ------------------------------------------------------------------


class TestAdapterResponseRepr:
    """AdapterResponse.__repr__ must not expose body or header values."""

    def test_body_redacted_in_repr(self) -> None:
        """Body content must show as <N bytes>."""
        resp = AdapterResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=b'{"choices":[{"message":{"content":"secret response"}}]}',
        )
        r = repr(resp)
        assert "secret response" not in r
        assert "<" in r and "bytes>" in r

    def test_headers_redacted_in_repr(self) -> None:
        """Header values must show as <N entries>."""
        resp = AdapterResponse(
            status_code=200,
            headers={"x-request-id": "abc123", "content-type": "application/json"},
            body=b"{}",
        )
        r = repr(resp)
        assert "abc123" not in r
        assert "<2 entries>" in r

    def test_body_length_shown(self) -> None:
        """Body redaction shows correct byte count."""
        resp = AdapterResponse(
            status_code=200,
            headers={},
            body=b"y" * 99,
        )
        assert "<99 bytes>" in repr(resp)


# ------------------------------------------------------------------
# Dead code removal
# ------------------------------------------------------------------


class TestDeadCodeRemoval:
    """Verify dead code has been removed."""

    def test_uniform_401_removed_from_app(self) -> None:
        """_uniform_401 function must not exist in app module."""
        from worthless.proxy import app as app_module

        assert not hasattr(app_module, "_uniform_401"), (
            "_uniform_401 is dead code and should be removed"
        )

    def test_dependencies_module_removed(self) -> None:
        """dependencies.py must not exist on disk."""
        dep_path = (
            Path(__file__).resolve().parent.parent
            / "src"
            / "worthless"
            / "proxy"
            / "dependencies.py"
        )
        assert not dep_path.exists(), f"Dead code file still exists: {dep_path}"

    def test_dependencies_module_not_importable(self) -> None:
        """dependencies module must not be importable."""
        with pytest.raises(ImportError):
            import worthless.proxy.dependencies  # noqa: F401


# ------------------------------------------------------------------
# Bytearray compliance (SR-01)
# ------------------------------------------------------------------


class TestBytearrayCompliance:
    """StoredShard fields must be bytearray, not bytes."""

    def test_stored_shard_bytearray_fields(self) -> None:
        """StoredShard enforces bytearray type for secret fields."""
        from worthless.storage.repository import StoredShard

        shard = StoredShard(
            shard_b=bytearray(b"shard-b-data"),
            commitment=bytearray(b"commitment-data"),
            nonce=bytearray(b"nonce-data"),
            provider="openai",
        )
        assert isinstance(shard.shard_b, bytearray)
        assert isinstance(shard.commitment, bytearray)
        assert isinstance(shard.nonce, bytearray)
