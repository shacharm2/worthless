"""Direct unit tests for types.py — strip_internal_headers, relay_response, dataclasses."""

from __future__ import annotations

import httpx
import pytest

from tests.helpers import make_streaming_response

from worthless.adapters.types import (
    INTERNAL_HEADER_PREFIX,
    SSE_RESPONSE_HEADERS,
    AdapterRequest,
    AdapterResponse,
    relay_response,
    strip_internal_headers,
)


# ---------------------------------------------------------------------------
# strip_internal_headers
# ---------------------------------------------------------------------------


class TestStripInternalHeaders:
    def test_removes_x_worthless_headers(self) -> None:
        headers = {
            "content-type": "application/json",
            "x-worthless-trace-id": "abc",
            "x-worthless-session": "xyz",
        }
        result = strip_internal_headers(headers)
        assert "content-type" in result
        assert "x-worthless-trace-id" not in result
        assert "x-worthless-session" not in result

    def test_mixed_case_internal_headers_stripped(self) -> None:
        """Internal headers are stripped regardless of casing."""
        headers = {
            "X-Worthless-Trace-Id": "abc",
            "X-WORTHLESS-SESSION": "xyz",
            "Accept": "application/json",
        }
        result = strip_internal_headers(headers)
        assert len(result) == 1
        assert "accept" in result

    def test_empty_headers(self) -> None:
        result = strip_internal_headers({})
        assert result == {}

    def test_no_internal_headers(self) -> None:
        headers = {"content-type": "application/json", "authorization": "Bearer key"}
        result = strip_internal_headers(headers)
        assert len(result) == 2

    def test_keys_are_lowercased(self) -> None:
        headers = {"Content-Type": "application/json", "Accept": "text/html"}
        result = strip_internal_headers(headers)
        assert "content-type" in result
        assert "accept" in result
        assert "Content-Type" not in result
        assert "Accept" not in result

    def test_only_internal_headers(self) -> None:
        """If all headers are internal, result is empty."""
        headers = {
            "x-worthless-a": "1",
            "x-worthless-b": "2",
        }
        result = strip_internal_headers(headers)
        assert result == {}

    def test_prefix_constant_value(self) -> None:
        assert INTERNAL_HEADER_PREFIX == "x-worthless-"

    def test_strips_hop_by_hop_headers(self) -> None:
        """Hop-by-hop headers must not be forwarded to upstream."""
        hop_by_hop = [
            "connection",
            "transfer-encoding",
            "te",
            "upgrade",
            "proxy-authorization",
            "host",
            "keep-alive",
            "trailer",
            "proxy-connection",
        ]
        headers = {h: "some-value" for h in hop_by_hop}
        headers["content-type"] = "application/json"
        result = strip_internal_headers(headers)
        assert list(result.keys()) == ["content-type"]

    def test_strips_hop_by_hop_mixed_case(self) -> None:
        """Hop-by-hop stripping is case-insensitive."""
        headers = {
            "Transfer-Encoding": "chunked",
            "Host": "evil.example.com",
            "Accept": "application/json",
        }
        result = strip_internal_headers(headers)
        assert "accept" in result
        assert len(result) == 1

    def test_strips_both_internal_and_hop_by_hop(self) -> None:
        headers = {
            "x-worthless-trace": "abc",
            "connection": "close",
            "accept": "application/json",
        }
        result = strip_internal_headers(headers)
        assert result == {"accept": "application/json"}


# ---------------------------------------------------------------------------
# relay_response (direct)
# ---------------------------------------------------------------------------


class TestRelayResponseDirect:
    @pytest.mark.asyncio
    async def test_non_streaming_response(self) -> None:
        body = b'{"result": "ok"}'
        upstream = httpx.Response(
            status_code=200,
            content=body,
            headers={"content-type": "application/json"},
        )
        resp = await relay_response(upstream)
        assert resp.status_code == 200
        assert resp.body == body
        assert resp.is_streaming is False
        assert resp.stream is None

    @pytest.mark.asyncio
    async def test_streaming_response(self) -> None:
        chunks = [b"data: hello\n\n", b"data: world\n\n"]
        upstream = make_streaming_response(chunks)
        resp = await relay_response(upstream)
        assert resp.is_streaming is True
        assert resp.stream is not None
        assert resp.body == b""

    @pytest.mark.asyncio
    async def test_streaming_response_headers_match_sse_constants(self) -> None:
        chunks = [b"data: test\n\n"]
        upstream = make_streaming_response(chunks)
        resp = await relay_response(upstream)
        for key, value in SSE_RESPONSE_HEADERS.items():
            assert resp.headers[key] == value

    @pytest.mark.asyncio
    async def test_non_streaming_preserves_upstream_headers(self) -> None:
        upstream = httpx.Response(
            status_code=200,
            content=b"{}",
            headers={
                "content-type": "application/json",
                "x-request-id": "req-123",
            },
        )
        resp = await relay_response(upstream)
        assert resp.headers["x-request-id"] == "req-123"

    @pytest.mark.asyncio
    async def test_error_non_streaming(self) -> None:
        body = b'{"error": "bad request"}'
        upstream = httpx.Response(
            status_code=400,
            content=body,
            headers={"content-type": "application/json"},
        )
        resp = await relay_response(upstream)
        assert resp.status_code == 400
        assert resp.body == body
        assert resp.is_streaming is False

    @pytest.mark.asyncio
    async def test_content_type_with_extra_params(self) -> None:
        """text/event-stream with additional params still detected as streaming."""
        chunks = [b"data: test\n\n"]
        upstream = make_streaming_response(
            chunks,
            headers={"content-type": "text/event-stream; charset=utf-8"},
        )
        resp = await relay_response(upstream)
        assert resp.is_streaming is True

    @pytest.mark.asyncio
    async def test_empty_body_non_streaming(self) -> None:
        upstream = httpx.Response(
            status_code=204,
            content=b"",
            headers={"content-type": "application/json"},
        )
        resp = await relay_response(upstream)
        assert resp.status_code == 204
        assert resp.body == b""
        assert resp.is_streaming is False

    @pytest.mark.asyncio
    async def test_missing_content_type_defaults_non_streaming(self) -> None:
        """If no content-type header, treat as non-streaming."""
        upstream = httpx.Response(
            status_code=200,
            content=b"raw bytes",
        )
        resp = await relay_response(upstream)
        assert resp.is_streaming is False

    @pytest.mark.asyncio
    async def test_content_type_substring_false_positive(self) -> None:
        """A content-type containing 'text/event-stream' as substring should not match.

        e.g. 'application/not-text/event-stream-really' should be non-streaming.
        """
        upstream = httpx.Response(
            status_code=200,
            content=b"not a stream",
            headers={"content-type": "application/vnd.text/event-stream.wrapper"},
        )
        resp = await relay_response(upstream)
        assert resp.is_streaming is False

    @pytest.mark.asyncio
    async def test_content_type_exact_match_streaming(self) -> None:
        """Exact 'text/event-stream' (no params) is streaming."""
        chunks = [b"data: test\n\n"]
        upstream = make_streaming_response(
            chunks, headers={"content-type": "text/event-stream"}
        )
        resp = await relay_response(upstream)
        assert resp.is_streaming is True


# ---------------------------------------------------------------------------
# Dataclass behavior
# ---------------------------------------------------------------------------


class TestAdapterRequest:
    def test_frozen(self) -> None:
        req = AdapterRequest(url="http://x", headers={}, body=b"")
        with pytest.raises(AttributeError):
            req.url = "http://changed"  # type: ignore[misc]

    def test_equality(self) -> None:
        a = AdapterRequest(url="http://x", headers={"a": "1"}, body=b"hi")
        b = AdapterRequest(url="http://x", headers={"a": "1"}, body=b"hi")
        assert a == b

    def test_inequality(self) -> None:
        a = AdapterRequest(url="http://x", headers={}, body=b"")
        b = AdapterRequest(url="http://y", headers={}, body=b"")
        assert a != b

    def test_repr_redacts_authorization(self) -> None:
        req = AdapterRequest(
            url="http://x",
            headers={"authorization": "Bearer sk-secret-key", "content-type": "application/json"},
            body=b"{}",
        )
        r = repr(req)
        assert "sk-secret-key" not in r
        assert "authorization" in r
        assert "***" in r or "REDACTED" in r
        # Non-sensitive headers should show values
        assert "application/json" in r

    def test_repr_redacts_x_api_key(self) -> None:
        req = AdapterRequest(
            url="http://x",
            headers={"x-api-key": "anthropic-secret", "accept": "text/plain"},
            body=b"{}",
        )
        r = repr(req)
        assert "anthropic-secret" not in r
        assert "x-api-key" in r
        assert "text/plain" in r

    def test_repr_redacts_case_insensitive(self) -> None:
        req = AdapterRequest(
            url="http://x",
            headers={"Authorization": "Bearer secret", "X-Api-Key": "also-secret"},
            body=b"{}",
        )
        r = repr(req)
        assert "secret" not in r.lower() or "redacted" in r.lower()

    def test_repr_no_headers(self) -> None:
        """repr works with empty headers."""
        req = AdapterRequest(url="http://x", headers={}, body=b"")
        r = repr(req)
        assert "AdapterRequest" in r


class TestAdapterResponse:
    def test_defaults(self) -> None:
        resp = AdapterResponse(status_code=200, headers={}, body=b"")
        assert resp.is_streaming is False
        assert resp.stream is None

    def test_frozen(self) -> None:
        resp = AdapterResponse(status_code=200, headers={}, body=b"")
        with pytest.raises(AttributeError):
            resp.status_code = 500  # type: ignore[misc]

    def test_equality_ignores_stream(self) -> None:
        """stream field is excluded from comparison (compare=False)."""
        a = AdapterResponse(status_code=200, headers={}, body=b"", stream=None)
        b = AdapterResponse(status_code=200, headers={}, body=b"", stream=iter([]))  # type: ignore[arg-type]
        assert a == b


# ---------------------------------------------------------------------------
# SSE_RESPONSE_HEADERS constant
# ---------------------------------------------------------------------------


class TestSSEResponseHeaders:
    def test_contains_required_keys(self) -> None:
        assert "Content-Type" in SSE_RESPONSE_HEADERS
        assert "Cache-Control" in SSE_RESPONSE_HEADERS
        assert "X-Accel-Buffering" in SSE_RESPONSE_HEADERS
        assert "Connection" in SSE_RESPONSE_HEADERS

    def test_connection_keep_alive(self) -> None:
        assert SSE_RESPONSE_HEADERS["Connection"] == "keep-alive"
