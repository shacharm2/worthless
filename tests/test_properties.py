"""Property-based tests for adapter invariants."""

from __future__ import annotations

import asyncio
import string

import httpx
from hypothesis import given
from hypothesis import strategies as st

from tests.helpers import make_streaming_response
from worthless.adapters.anthropic import DEFAULT_ANTHROPIC_VERSION, AnthropicAdapter
from worthless.adapters.openai import OpenAIAdapter
from worthless.adapters.types import AdapterRequest, relay_response, strip_internal_headers

_HOP_BY_HOP_HEADERS = (
    "connection",
    "transfer-encoding",
    "te",
    "upgrade",
    "proxy-authorization",
    "host",
    "keep-alive",
    "trailer",
    "proxy-connection",
)

_SAFE_HEADER_KEYS = st.from_regex(r"[a-z][a-z0-9-]{0,20}", fullmatch=True).filter(
    lambda key: not key.startswith("x-worthless-")
    and key not in _HOP_BY_HOP_HEADERS
    and key != "anthropic-version"
)
_HEADER_VALUES = st.text(
    alphabet=string.ascii_letters + string.digits + "-_/;= .",
    min_size=0,
    max_size=32,
)
_SAFE_HEADERS = st.dictionaries(
    keys=_SAFE_HEADER_KEYS,
    values=_HEADER_VALUES,
    max_size=6,
)
_API_KEYS = st.text(
    alphabet=string.ascii_letters + string.digits + "-_",
    min_size=8,
    max_size=48,
)


def _apply_case_mask(text: str, mask: list[bool]) -> str:
    """Apply a boolean mask to a string, uppercasing selected characters."""
    chars: list[str] = []
    for idx, char in enumerate(text):
        should_upper = mask[idx] if idx < len(mask) else False
        chars.append(char.upper() if should_upper else char.lower())
    return "".join(chars)


def _collect_streaming_chunks(response: httpx.Response) -> list[bytes]:
    async def _collect() -> list[bytes]:
        relayed = await relay_response(response)
        assert relayed.is_streaming is True
        assert relayed.stream is not None
        return [chunk async for chunk in relayed.stream]

    return asyncio.run(_collect())


class TestStripInternalHeadersProperties:
    @given(
        suffix=st.text(
            alphabet=string.ascii_letters + string.digits + "-",
            min_size=1,
            max_size=16,
        ),
        safe_headers=_SAFE_HEADERS,
    )
    def test_internal_prefix_is_always_stripped(
        self, suffix: str, safe_headers: dict[str, str]
    ) -> None:
        raw_headers = dict(safe_headers)
        raw_headers[f"X-WorThLeSs-{suffix}"] = "secret"

        stripped = strip_internal_headers(raw_headers)

        assert f"x-worthless-{suffix.lower()}" not in stripped
        for key, value in safe_headers.items():
            assert stripped[key.lower()] == value

    @given(
        hop_header=st.sampled_from(_HOP_BY_HOP_HEADERS),
        mask=st.lists(st.booleans(), min_size=1, max_size=1_024),
        safe_headers=_SAFE_HEADERS,
    )
    def test_hop_by_hop_headers_are_case_insensitively_stripped(
        self, hop_header: str, mask: list[bool], safe_headers: dict[str, str]
    ) -> None:
        header_name = _apply_case_mask(hop_header, mask[: len(hop_header)])
        raw_headers = dict(safe_headers)
        raw_headers[header_name] = "blocked"

        stripped = strip_internal_headers(raw_headers)

        assert hop_header not in stripped
        for key, value in safe_headers.items():
            assert stripped[key.lower()] == value

    @given(headers=_SAFE_HEADERS)
    def test_safe_headers_are_preserved_and_lowercased(
        self, headers: dict[str, str]
    ) -> None:
        stripped = strip_internal_headers(headers)
        assert stripped == {key.lower(): value for key, value in headers.items()}


class TestPrepareRequestProperties:
    @given(body=st.binary(max_size=4096), headers=_SAFE_HEADERS, api_key=_API_KEYS)
    def test_openai_prepare_request_preserves_body_and_sets_auth(
        self, body: bytes, headers: dict[str, str], api_key: str
    ) -> None:
        req = OpenAIAdapter().prepare_request(body=body, headers=headers, api_key=api_key)

        assert req.body == body
        assert req.headers["authorization"] == f"Bearer {api_key}"
        assert req.url.endswith("/v1/chat/completions")

    @given(body=st.binary(max_size=4096), headers=_SAFE_HEADERS, api_key=_API_KEYS)
    def test_anthropic_prepare_request_adds_default_version_when_missing(
        self, body: bytes, headers: dict[str, str], api_key: str
    ) -> None:
        req = AnthropicAdapter().prepare_request(body=body, headers=headers, api_key=api_key)

        assert req.body == body
        assert req.headers["x-api-key"] == api_key
        assert req.headers["anthropic-version"] == DEFAULT_ANTHROPIC_VERSION
        assert req.url.endswith("/v1/messages")

    @given(
        body=st.binary(max_size=4096),
        headers=_SAFE_HEADERS,
        api_key=_API_KEYS,
        version=_HEADER_VALUES.filter(bool),
    )
    def test_anthropic_prepare_request_preserves_explicit_version(
        self, body: bytes, headers: dict[str, str], api_key: str, version: str
    ) -> None:
        raw_headers = dict(headers)
        raw_headers["anthropic-version"] = version

        req = AnthropicAdapter().prepare_request(
            body=body,
            headers=raw_headers,
            api_key=api_key,
        )

        assert req.headers["anthropic-version"] == version


class TestAdapterRequestProperties:
    @given(
        authorization=_API_KEYS,
        anthropic_key=_API_KEYS,
        other_headers=_SAFE_HEADERS,
    )
    def test_repr_redacts_sensitive_headers(
        self,
        authorization: str,
        anthropic_key: str,
        other_headers: dict[str, str],
    ) -> None:
        headers = dict(other_headers)
        headers["authorization"] = authorization
        headers["x-api-key"] = anthropic_key
        req = AdapterRequest(url="https://example.test", headers=headers, body=b"payload")

        text = repr(req)

        assert f"'authorization': '{authorization}'" not in text
        assert f"'x-api-key': '{anthropic_key}'" not in text
        assert text.count("REDACTED") >= 2


class TestRelayResponseProperties:
    @given(
        body=st.binary(max_size=4096),
        content_type=st.sampled_from(
            ["application/json", "text/plain", "application/octet-stream", ""]
        ),
    )
    def test_non_streaming_responses_preserve_body(
        self, body: bytes, content_type: str
    ) -> None:
        headers = {"content-type": content_type} if content_type else {}
        upstream = httpx.Response(status_code=200, content=body, headers=headers)

        relayed = asyncio.run(relay_response(upstream))

        assert relayed.is_streaming is False
        assert relayed.body == body
        assert relayed.stream is None

    @given(
        chunks=st.lists(st.binary(min_size=1, max_size=64), min_size=1, max_size=8),
        extra_param=_HEADER_VALUES,
    )
    def test_event_stream_responses_yield_original_chunks(
        self, chunks: list[bytes], extra_param: str
    ) -> None:
        content_type = "text/event-stream"
        if extra_param:
            content_type = f"text/event-stream; {extra_param}"

        upstream = make_streaming_response(
            chunks,
            headers={"content-type": content_type},
        )

        collected = _collect_streaming_chunks(upstream)

        assert collected == chunks
