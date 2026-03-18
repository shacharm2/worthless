# Phase 2: Provider Adapters - Research

**Researched:** 2026-03-14
**Domain:** OpenAI and Anthropic request/response transformation, SSE streaming relay
**Confidence:** HIGH

## Summary

Phase 2 builds stateless request/response transformers for OpenAI and Anthropic protocols. The key insight is **passthrough architecture** -- the client already sends valid provider requests. The adapter only strips Worthless-specific headers, rewrites the URL to the real provider, and relays the response byte-for-byte. This means SSE streaming "just works" -- forward the byte stream without parsing.

**Primary recommendation:** Use `httpx` for async HTTP upstream calls with `httpx-sse` for SSE parsing (only needed for metering, not relay). Use FastAPI `StreamingResponse` for real-time chunk relay. No provider SDKs -- raw HTTP only.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| PROX-01 | OpenAI-compatible endpoint (`/v1/chat/completions`) | Passthrough: accept request, rewrite URL to `https://api.openai.com/v1/chat/completions`, forward with reconstructed key in `Authorization: Bearer` header |
| PROX-02 | Anthropic-compatible endpoint (`/v1/messages`) | Passthrough: accept request, rewrite URL to `https://api.anthropic.com/v1/messages`, forward with reconstructed key in `x-api-key` header + `anthropic-version` header |
| PROX-03 | SSE streaming relay for both providers | Forward upstream `text/event-stream` response as `StreamingResponse`. No buffering. Set `Cache-Control: no-cache`, `X-Accel-Buffering: no` |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `httpx` | 0.28.x | Async HTTP client for upstream provider calls | FastAPI-recommended, supports streaming, async-native |
| `pydantic` | 2.x | Request/response validation models | Already a FastAPI dependency |
| `fastapi` | 0.115.x | Web framework (shared with Phase 3) | Project mandate |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `pytest-httpx` | latest | Mock httpx responses in tests | Testing adapter without real API calls |
| `respx` | latest | Alternative httpx mocking | If pytest-httpx insufficient |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `httpx` | `aiohttp` | aiohttp works but httpx has cleaner API, better typing, and is FastAPI-recommended |
| Raw HTTP passthrough | Provider SDKs (openai, anthropic) | SDKs add abstraction we don't want -- we need raw HTTP control for transparent proxying |

## Architecture Patterns

### Pattern 1: Provider Detection from Request Path
**What:** Route requests to the correct provider adapter based on URL path.
**When to use:** Every incoming request.
**Key design points:**
- `/v1/chat/completions` → OpenAI adapter
- `/v1/messages` → Anthropic adapter
- Header `x-api-key` presence also signals Anthropic
- Return 404 for unrecognized paths

### Pattern 2: Passthrough Request Transformation
**What:** Minimal transformation -- only change what's necessary.
**When to use:** Every proxied request.
**Key design points:**
- Strip Worthless-specific headers (e.g., `X-Worthless-Key-Alias`)
- Set provider auth header (Bearer for OpenAI, x-api-key for Anthropic)
- Add required headers (anthropic-version for Anthropic)
- Forward body unchanged
- Forward all other headers unchanged

### Pattern 3: SSE Streaming Relay
**What:** Stream upstream SSE response to client in real-time.
**When to use:** When `stream: true` in request body (both providers).
**Key design points:**
- Use `httpx` streaming response: `async with client.stream("POST", url, ...) as response:`
- Yield chunks via `async for chunk in response.aiter_bytes()`
- Wrap in FastAPI `StreamingResponse(media_type="text/event-stream")`
- Set headers: `Cache-Control: no-cache`, `X-Accel-Buffering: no`, `Connection: keep-alive`
- Do NOT set `Content-Length` on streaming responses
- OpenAI terminates with `data: [DONE]`
- Anthropic terminates with `event: message_stop`

### Pattern 4: Non-Streaming Response Relay
**What:** For non-streaming requests, forward response body and status code.
**When to use:** When `stream` is absent or false in request body.
**Key design points:**
- `response = await client.post(url, ...)`
- Return `Response(content=response.content, status_code=response.status_code, headers=dict(response.headers))`

### Anti-Patterns to Avoid
- **Parsing provider responses:** Don't parse JSON to restructure it. Forward as-is.
- **Buffering SSE:** Never accumulate chunks. Yield immediately.
- **Provider SDKs:** Don't use openai or anthropic Python packages. Raw HTTP gives us control.
- **Logging prompt/response content:** Violates CLAUDE.md logging denylist.

## Common Pitfalls

### Pitfall 1: Response Buffering Kills Streaming
**What goes wrong:** SSE chunks arrive at the server but are buffered before reaching the client.
**Why it happens:** Reverse proxies, ASGI middleware, or missing headers cause buffering.
**How to avoid:** Set `Cache-Control: no-cache`, `X-Accel-Buffering: no`. Strip `Content-Length` from streaming responses. Test with `curl --no-buffer`.

### Pitfall 2: Anthropic Requires anthropic-version Header
**What goes wrong:** Anthropic API returns 400 without `anthropic-version` header.
**Why it happens:** It's a required header, not optional.
**How to avoid:** Always include `anthropic-version: 2023-06-01` (or latest). Pass through if client sends it, add default if not.

### Pitfall 3: OpenAI Uses Bearer Auth, Anthropic Uses x-api-key
**What goes wrong:** Using wrong auth header format for the provider.
**Why it happens:** Different conventions per provider.
**How to avoid:** Provider adapter sets the correct header format. OpenAI: `Authorization: Bearer {key}`. Anthropic: `x-api-key: {key}`.

### Pitfall 4: Content-Type Mismatch on SSE
**What goes wrong:** Client doesn't recognize response as SSE stream.
**Why it happens:** Missing or wrong `Content-Type` header.
**How to avoid:** Set `Content-Type: text/event-stream; charset=utf-8` on streaming responses.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio + pytest-httpx |
| Config file | `pyproject.toml` (existing from Phase 1) |
| Quick run command | `uv run pytest tests/test_adapters.py -x -q` |
| Full suite command | `uv run pytest tests/ -v --tb=short` |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command |
|--------|----------|-----------|-------------------|
| PROX-01 | OpenAI request transformation correct | unit | `uv run pytest tests/test_adapters.py::test_openai_request_transform -x` |
| PROX-01 | OpenAI non-streaming response relay | unit | `uv run pytest tests/test_adapters.py::test_openai_response_relay -x` |
| PROX-02 | Anthropic request transformation correct | unit | `uv run pytest tests/test_adapters.py::test_anthropic_request_transform -x` |
| PROX-02 | Anthropic includes anthropic-version header | unit | `uv run pytest tests/test_adapters.py::test_anthropic_version_header -x` |
| PROX-03 | OpenAI SSE streaming relay | integration | `uv run pytest tests/test_streaming.py::test_openai_sse_relay -x` |
| PROX-03 | Anthropic SSE streaming relay | integration | `uv run pytest tests/test_streaming.py::test_anthropic_sse_relay -x` |
| PROX-03 | No buffering: chunks arrive immediately | integration | `uv run pytest tests/test_streaming.py::test_no_buffering -x` |

## Open Questions

1. **Token counting during relay:** Should adapters extract token usage from responses for metering? Recommendation: defer to Phase 3 (proxy service handles metering).
2. **Error response normalization:** Should adapters normalize provider error formats? Recommendation: pass through as-is. Client already expects provider-specific errors.
3. **Tool use / function calling streaming:** Special SSE event types for tool calls. Recommendation: passthrough handles this automatically -- no special parsing needed.

## Sources

### Primary (HIGH confidence)
- OpenAI API reference: request/response formats, SSE protocol, auth headers
- Anthropic API reference: Messages API, SSE events, required headers
- httpx docs: streaming requests, async client usage
- FastAPI docs: StreamingResponse, middleware

### Secondary (MEDIUM confidence)
- LiteLLM source code: proxy pattern reference for multi-provider routing

**Research date:** 2026-03-14
**Valid until:** 2026-04-14
