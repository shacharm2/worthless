# Phase 3: Proxy Service - Research

**Researched:** 2026-03-20
**Domain:** FastAPI reverse proxy with gate-before-reconstruct security model
**Confidence:** HIGH

## Summary

Phase 3 builds a FastAPI reverse proxy that sits between any HTTP client and OpenAI/Anthropic APIs. The proxy enforces the three architectural invariants: (1) client sends shard_a via header, never the full key; (2) the rules engine evaluates every request BEFORE shard_b is decrypted; (3) the reconstructed key is used server-side for the upstream call and never returned to the client.

The existing codebase provides all building blocks: `reconstruct_key()` + `secure_key()` for XOR reconstruction with memory zeroing, `get_adapter()` for path-based provider routing, `relay_response()` for SSE streaming, and `ShardRepository` for encrypted shard storage. The proxy's job is to wire these together with a rules engine gate, metering, and a FastAPI HTTP layer.

**Primary recommendation:** Build a thin FastAPI app with a catch-all route that follows a strict pipeline: extract alias -> load shard_b -> run rules gate -> reconstruct key -> prepare upstream request -> stream response -> async meter tokens. Use `httpx.AsyncClient` as a long-lived connection pool for upstream calls, and `StreamingResponse` with `BackgroundTask` for cleanup.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Client sends `x-worthless-alias` header (mandatory) to identify enrolled key
- Shard A sent via `x-worthless-shard-a` header (base64), falls back to file at `~/.worthless/shard_a/{alias}`
- Header wins over file for shard_a loading
- Alias-only auth for PoC -- no separate proxy token
- All auth failures return identical 401 body: `"authentication required"` (anti-enumeration)
- Status codes: 401 auth / 402 spend / 429 rate -- never reveal reason within a type
- Rules engine gate always runs before reconstruction (SR-03), passes through with zero rules
- Spend cap: off by default, per-enrollment budget
- Rate limit: on by default ~100 req/s per IP
- Both thresholds configurable per enrollment
- Plugin architecture for future rules
- State in SQLite (extends ShardRepository DB with metering tables)
- Post-response token counting from provider SSE/JSON responses
- Metering write is async/fire-and-forget -- zero added latency
- Accepted: one request can overshoot spend cap (bounded by single-request cost)
- Provider-compatible error JSON (mirror OpenAI/Anthropic error schemas)
- Rate limit responses include `Retry-After` header
- Upstream errors passed through transparently
- Stack-agnostic: works with any HTTP client sending correct path + headers
- Health endpoints: `/healthz` + `/readyz` (no auth)
- Configurable upstream timeouts: 120s non-streaming, 300s streaming via env vars
- Refuse shard headers over non-TLS connections
- Strip `x-worthless-*` from upstream responses
- Disable redirect following on upstream httpx client
- File-based shard loader -- never pass shards via CLI flags or env vars
- Reject request headers with whitespace or null bytes
- Deny CORS by default
- Query-param stripping before registry path lookup
- `api_key: str` -> `api_key: bytearray` in adapter `prepare_request()` (breaking change from Phase 2)
- Minimal enrollment CLI stub to seed test keys into DB

### Claude's Discretion
- Logging strategy (structured logging, log levels, what gets logged)
- Graceful shutdown handling
- httpx client lifecycle (connection pooling, keep-alive)
- Request body size limits
- FastAPI app structure (single file vs module)
- Token counting implementation details for each provider's SSE format

### Deferred Ideas (OUT OF SCOPE)
- Pre-estimation of token costs before sending request
- Pre+post hybrid spend enforcement
- Model allowlist rule
- Token budget rule
- Time window rule
- Anomaly detection (spend velocity)
- mTLS client certificate auth for proxy access
- Separate proxy bearer token authentication
- Thin SDK wrappers for Python OpenAI/Anthropic clients
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| CRYP-05 | Rules engine evaluates request BEFORE Shard B is decrypted (gate-before-reconstruct) | Pipeline architecture with rules gate as first stage; reconstruct_key() only called after gate passes. Rules engine is a chain of async callables that can short-circuit with 402/429. |
| PROX-04 | Stack-agnostic via BASE_URL env var rewriting (no SDK import needed) | Catch-all route on `/{path:path}` with path-based adapter lookup; any HTTP client that sets BASE_URL to proxy address and sends x-worthless-* headers works transparently |
| PROX-05 | Reconstruction happens server-side, key never returns to client | reconstruct_key() runs inside `secure_key()` context manager on the proxy; key is injected into adapter's prepare_request(), used for upstream call, then zeroed. Never serialized to any response. |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| fastapi | >=0.115 | HTTP framework | ASGI-native, async-first, OpenAPI auto-gen, Starlette underpinnings |
| uvicorn | >=0.34 | ASGI server | Standard production server for FastAPI apps |
| httpx | >=0.28 | Upstream HTTP client | Already a dependency; async, connection pooling, streaming |
| aiosqlite | >=0.22.1 | Async SQLite | Already a dependency for ShardRepository |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| starlette | (via fastapi) | StreamingResponse, BackgroundTask | SSE relay, cleanup after streaming |
| structlog | >=24.0 | Structured logging | Claude's discretion -- recommended for JSON logs with redaction |

### Already Available (no new deps)
| Library | In pyproject.toml | Purpose |
|---------|-------------------|---------|
| cryptography | Yes | Fernet encryption at rest |
| httpx | Yes | Upstream HTTP calls |
| aiosqlite | Yes | Async SQLite access |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| structlog | stdlib logging | structlog gives processor pipeline for redaction; stdlib is zero-dep but manual |
| uvicorn | hypercorn | uvicorn is the FastAPI default; hypercorn adds HTTP/2 but not needed for PoC |

**Installation:**
```bash
uv add fastapi uvicorn[standard]
# Optional:
uv add structlog
```

## Architecture Patterns

### Recommended Project Structure
```
src/worthless/
  proxy/
    __init__.py          # FastAPI app factory
    app.py               # create_app(), lifespan, catch-all route
    dependencies.py      # FastAPI Depends: repo, httpx client, rules engine
    rules.py             # RulesEngine: spend_cap, rate_limit pipeline
    metering.py          # Async token counting + spend recording
    errors.py            # Provider-compatible error responses
    config.py            # Settings from env vars (timeouts, rate limits)
  adapters/
    types.py             # api_key: str -> bytearray (BREAKING CHANGE)
    openai.py            # Update prepare_request signature
    anthropic.py         # Update prepare_request signature
  storage/
    schema.py            # Add metering tables (spend_log, rate_limit_buckets)
    repository.py        # Existing (unchanged)
  cli/
    enroll_stub.py       # Minimal enrollment for testing
```

### Pattern 1: Request Pipeline (Gate-Before-Reconstruct)

**What:** Every request follows a strict pipeline: authenticate -> gate -> reconstruct -> proxy -> meter
**When to use:** Every proxied request
**Example:**
```python
async def proxy_request(request: Request, path: str):
    # 1. Extract alias + shard_a (authenticate)
    alias = request.headers.get("x-worthless-alias")
    if not alias:
        return auth_error_response()  # uniform 401

    # 2. Load shard_b from DB
    stored = await repo.retrieve(alias)
    if stored is None:
        return auth_error_response()  # same 401, no enumeration

    # 3. Load shard_a (header or file)
    shard_a = extract_shard_a(request, alias)
    if shard_a is None:
        return auth_error_response()  # same 401

    # 4. GATE: Rules engine runs BEFORE reconstruction (SR-03, CRYP-05)
    denial = await rules_engine.evaluate(alias, request)
    if denial is not None:
        return denial  # 402 or 429, key never touched

    # 5. Reconstruct key (server-side only, PROX-05)
    adapter = get_adapter(clean_path)
    if adapter is None:
        return error_response(404, "unknown_endpoint")

    key_buf = reconstruct_key(shard_a, stored.shard_b, stored.commitment, stored.nonce)
    with secure_key(key_buf) as api_key:
        upstream_req = adapter.prepare_request(
            body=body, headers=clean_headers, api_key=api_key
        )
    # api_key is now zeroed

    # 6. Proxy upstream call
    upstream_resp = await httpx_client.send(built_request, stream=True)
    adapter_resp = await adapter.relay_response(upstream_resp)

    # 7. Return response + async metering
    if adapter_resp.is_streaming:
        return StreamingResponse(
            metering_wrapper(adapter_resp.stream, alias, stored.provider),
            status_code=adapter_resp.status_code,
            headers=strip_worthless_headers(adapter_resp.headers),
            background=BackgroundTask(upstream_resp.aclose),
        )
```

### Pattern 2: Rules Engine Plugin Architecture

**What:** A chain of async rule callables that can short-circuit
**When to use:** Pre-reconstruction gate
**Example:**
```python
from dataclasses import dataclass
from typing import Protocol

class Rule(Protocol):
    async def evaluate(self, alias: str, request: Request) -> Response | None:
        """Return None to pass, or a Response to deny."""
        ...

@dataclass
class RulesEngine:
    rules: list[Rule]

    async def evaluate(self, alias: str, request: Request) -> Response | None:
        for rule in self.rules:
            denial = await rule.evaluate(alias, request)
            if denial is not None:
                return denial
        return None  # all rules passed

class SpendCapRule:
    def __init__(self, db_path: str): ...
    async def evaluate(self, alias: str, request: Request) -> Response | None:
        # Check accumulated spend vs configured cap
        # Return 402 if exceeded, None otherwise
        ...

class RateLimitRule:
    def __init__(self, default_rps: float = 100.0): ...
    async def evaluate(self, alias: str, request: Request) -> Response | None:
        # Sliding window counter per IP
        # Return 429 with Retry-After if exceeded, None otherwise
        ...
```

### Pattern 3: Token Counting from SSE Streams

**What:** Extract usage data from provider responses without blocking the stream
**When to use:** Post-response metering
**Example:**
```python
async def metering_wrapper(
    stream: AsyncIterator[bytes],
    alias: str,
    provider: str,
) -> AsyncIterator[bytes]:
    """Wrap SSE stream, yield bytes unchanged, extract usage at end."""
    usage_tokens = 0
    buffer = b""
    async for chunk in stream:
        yield chunk  # client gets data immediately
        # Accumulate for usage extraction
        buffer += chunk

    # After stream ends, parse usage from accumulated data
    usage_tokens = extract_usage(buffer, provider)
    # Fire-and-forget metering write
    asyncio.create_task(record_spend(alias, usage_tokens, provider))

def extract_usage_openai(data: bytes) -> int:
    """Parse usage.total_tokens from final SSE chunk.
    OpenAI includes usage in the last chunk when stream_options.include_usage=true.
    For pass-through proxy, parse from the raw SSE data."""
    # Look for data: {...,"usage":{"total_tokens":N}...} in SSE lines
    ...

def extract_usage_anthropic(data: bytes) -> int:
    """Parse usage from message_delta event.
    Anthropic sends output_tokens in the message_delta event before message_stop."""
    # Look for event: message_delta with usage.output_tokens
    ...
```

### Pattern 4: Provider-Compatible Error Responses

**What:** Error responses that match OpenAI/Anthropic SDK expectations
**When to use:** All proxy-generated errors (401/402/429)
**Example:**
```python
def openai_error_response(status: int, message: str, error_type: str) -> JSONResponse:
    """OpenAI-compatible error format."""
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "message": message,
                "type": error_type,
                "param": None,
                "code": None,
            }
        },
    )

def anthropic_error_response(status: int, message: str, error_type: str) -> JSONResponse:
    """Anthropic-compatible error format."""
    return JSONResponse(
        status_code=status,
        content={
            "type": "error",
            "error": {
                "type": error_type,
                "message": message,
            },
        },
    )
```

### Anti-Patterns to Avoid
- **Reconstructing before gate check:** The entire security model depends on SR-03. The key must NEVER be reconstructed if the rules engine denies the request.
- **Returning key material to client:** The reconstructed key lives only in the `secure_key()` context manager and is zeroed immediately after the upstream request is prepared.
- **Blocking metering:** Token counting and spend recording happen asynchronously after the response is streaming. Never block the response on metering writes.
- **Different 401 bodies for different auth failures:** All auth failures (missing alias, bad shard, unknown alias, HMAC failure) return identical `{"error": {"message": "authentication required"}}` -- anti-enumeration.
- **Using `bytes` for api_key in adapter:** Must use `bytearray` per SR-01 so memory can be zeroed.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Rate limiting | Custom counter with timestamps | Sliding window counter in SQLite or in-memory dict with expiry | Edge cases: clock skew, burst handling, per-IP vs per-alias |
| SSE streaming relay | Custom SSE parser | Raw byte passthrough via `aiter_bytes()` (already in Phase 2) | SSE parsing is fragile; byte passthrough preserves provider format exactly |
| Connection pooling | Manual socket management | `httpx.AsyncClient` with limits | httpx handles keep-alive, retries, timeouts natively |
| Error response format | Ad-hoc JSON | Dedicated error factory matching provider schemas | SDKs expect exact error shapes; mismatches break client error handling |

**Key insight:** The proxy should be as thin as possible -- it wires together existing crypto, adapter, and storage modules. The only new logic is the rules engine, metering, and HTTP plumbing.

## Common Pitfalls

### Pitfall 1: httpx Response Not Closed After Streaming
**What goes wrong:** Memory leak and connection pool exhaustion if `httpx.Response.aclose()` is not called after streaming completes.
**Why it happens:** `StreamingResponse` consumes the iterator but doesn't know about the underlying httpx response.
**How to avoid:** Pass `BackgroundTask(upstream_resp.aclose)` to `StreamingResponse`. This runs cleanup after the response is fully sent.
**Warning signs:** Connection pool warnings in httpx logs, increasing memory usage.

### Pitfall 2: Race Between Metering and Spend Check
**What goes wrong:** Two concurrent requests both pass the spend cap check, both proceed, both overshoot.
**Why it happens:** Post-response metering means the spend isn't recorded until after the response streams.
**How to avoid:** This is an accepted trade-off (documented in CONTEXT.md). The overshoot is bounded by the cost of a single request. Document this in the API and configuration.
**Warning signs:** N/A -- this is by design.

### Pitfall 3: Shard A Handling in File-Based Fallback
**What goes wrong:** Reading shard_a from `~/.worthless/shard_a/{alias}` with a malicious alias like `../../etc/passwd`.
**Why it happens:** Path traversal if alias is not sanitized.
**How to avoid:** Validate alias is alphanumeric/dash/underscore only. Resolve the full path and verify it's within the expected directory. Never construct paths by string concatenation alone.
**Warning signs:** Aliases containing `/`, `..`, or null bytes.

### Pitfall 4: ASGITransport Does Not Stream in Tests
**What goes wrong:** `httpx.AsyncClient(transport=ASGITransport(app))` buffers the entire response; `aiter_bytes()` returns everything at once.
**Why it happens:** Known httpx limitation (issue #2186). ASGITransport collects the full body before returning.
**How to avoid:** For SSE streaming tests, test the streaming wrapper separately (unit test the async generator). For integration tests, accept that the transport buffers but verify the full response content is correct. For true streaming tests, spin up uvicorn in a fixture.
**Warning signs:** Tests pass but streaming behavior isn't actually verified.

### Pitfall 5: Forgetting to Strip x-worthless-* from Response Headers
**What goes wrong:** If upstream somehow echoes back custom headers, they could be injected into the client response.
**Why it happens:** Only stripping on request side, not response side.
**How to avoid:** Apply `strip_internal_headers()` to both upstream request headers AND response headers back to the client.
**Warning signs:** `x-worthless-*` headers appearing in client responses.

### Pitfall 6: bytearray in Authorization Header
**What goes wrong:** `bytearray` cannot be directly used as a string in HTTP headers.
**Why it happens:** SR-01 requires `bytearray` for api_key, but HTTP headers need strings.
**How to avoid:** Decode `bytearray` to `str` only at the moment of header insertion inside `prepare_request()`, then immediately zero the bytearray. The string will be short-lived (used only for the single httpx request).
**Warning signs:** TypeError when setting headers, or accidental persistence of the string.

## Code Examples

### FastAPI App Factory with Lifespan
```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
import httpx

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create shared resources
    app.state.httpx_client = httpx.AsyncClient(
        follow_redirects=False,  # Security: no redirect following
        timeout=httpx.Timeout(
            connect=10.0,
            read=120.0,  # Non-streaming default
            write=10.0,
            pool=10.0,
        ),
        limits=httpx.Limits(
            max_connections=100,
            max_keepalive_connections=20,
        ),
    )
    app.state.repo = ShardRepository(db_path, fernet_key)
    await app.state.repo.initialize()

    yield

    # Shutdown: cleanup
    await app.state.httpx_client.aclose()

def create_app(db_path: str, fernet_key: bytes) -> FastAPI:
    app = FastAPI(title="Worthless Proxy", docs_url=None, redoc_url=None)
    # ... register routes, middleware
    return app
```

### Catch-All Route with Path Matching
```python
from starlette.requests import Request
from starlette.responses import StreamingResponse, JSONResponse
from starlette.background import BackgroundTask

@app.api_route("/{path:path}", methods=["POST", "GET", "PUT", "DELETE", "PATCH"])
async def proxy(request: Request, path: str):
    clean_path = "/" + path.split("?")[0]  # Strip query params
    # ... pipeline as shown in Pattern 1
```

### Health Endpoints
```python
@app.get("/healthz")
async def healthz():
    return {"status": "ok"}

@app.get("/readyz")
async def readyz(repo: ShardRepository = Depends(get_repo)):
    keys = await repo.list_keys()
    if not keys:
        return JSONResponse({"status": "not ready", "reason": "no keys enrolled"}, 503)
    return {"status": "ready", "enrolled_keys": len(keys)}
```

### Metering Schema Extension
```sql
CREATE TABLE IF NOT EXISTS spend_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key_alias   TEXT NOT NULL REFERENCES shards(key_alias),
    tokens      INTEGER NOT NULL,
    model       TEXT,
    provider    TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS rate_limit_state (
    key_alias   TEXT NOT NULL,
    ip_address  TEXT NOT NULL,
    window_start TEXT NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (key_alias, ip_address, window_start)
);

CREATE TABLE IF NOT EXISTS enrollment_config (
    key_alias       TEXT PRIMARY KEY REFERENCES shards(key_alias),
    spend_cap       REAL,           -- NULL = no cap (off by default)
    rate_limit_rps  REAL DEFAULT 100.0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `TestClient` (sync) | `httpx.AsyncClient` + `ASGITransport` | FastAPI 0.100+ | Async tests run natively; no sync wrapper needed |
| Manual SSE parsing | Raw byte passthrough via `aiter_bytes()` | Phase 2 decision | Proxy doesn't parse SSE -- just relays bytes |
| `startup`/`shutdown` events | `lifespan` context manager | FastAPI 0.93+ / Starlette 0.26 | Cleaner resource management |
| OpenAI streaming without usage | `stream_options.include_usage: true` | 2024 | Enables token counting from streaming responses |

**Deprecated/outdated:**
- `@app.on_event("startup")` / `@app.on_event("shutdown")`: Replaced by `lifespan` parameter
- `TestClient` for async apps: Use `httpx.AsyncClient(transport=ASGITransport(app))` instead

## Open Questions

1. **Token counting accuracy for pass-through proxy**
   - What we know: OpenAI includes usage in final SSE chunk when `stream_options.include_usage` is set. Anthropic sends usage in `message_delta` event.
   - What's unclear: The proxy passes through the client's original request body -- it can't inject `stream_options.include_usage` without modifying the request. For non-streaming requests, usage is in the response body.
   - Recommendation: For non-streaming responses, parse `usage.total_tokens` from the JSON body. For streaming, extract from final SSE chunks. If the client didn't request usage in streaming, count is unavailable -- log a warning and skip metering for that request. Consider always injecting `stream_options.include_usage: true` into OpenAI streaming requests.

2. **Rate limit state storage**
   - What we know: SQLite works for PoC. The rate is ~100 req/s per IP.
   - What's unclear: SQLite write contention under concurrent requests could slow the rate limiter.
   - Recommendation: Use in-memory dict with sliding window for rate limiting (fast, no I/O). SQLite only for spend tracking (less frequent writes). Rate limit state is ephemeral and acceptable to lose on restart.

3. **TLS enforcement for shard headers**
   - What we know: CONTEXT.md says "refuse shard headers over non-TLS connections."
   - What's unclear: In PoC development, TLS won't always be available. How strictly to enforce?
   - Recommendation: Check for `X-Forwarded-Proto: https` or direct TLS. Add an env var `WORTHLESS_ALLOW_INSECURE=1` for local development that skips this check, with a loud startup warning.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.0+ with pytest-asyncio 0.24+ |
| Config file | `pyproject.toml` [tool.pytest.ini_options] |
| Quick run command | `uv run pytest tests/test_proxy.py -x` |
| Full suite command | `uv run pytest` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| CRYP-05 | Gate runs before reconstruction; denied request never calls reconstruct_key | unit + integration | `uv run pytest tests/test_proxy.py -k "gate_before_reconstruct" -x` | Wave 0 |
| CRYP-05 | Spend cap exceeded returns 402, key never reconstructed | integration | `uv run pytest tests/test_proxy.py -k "spend_cap_denied" -x` | Wave 0 |
| CRYP-05 | Rate limit exceeded returns 429 with Retry-After, key never reconstructed | integration | `uv run pytest tests/test_proxy.py -k "rate_limit_denied" -x` | Wave 0 |
| PROX-04 | Any HTTP client with BASE_URL + headers routes through proxy transparently | integration | `uv run pytest tests/test_proxy.py -k "transparent_routing" -x` | Wave 0 |
| PROX-04 | Path-based adapter lookup works for OpenAI and Anthropic endpoints | unit | `uv run pytest tests/test_proxy.py -k "adapter_routing" -x` | Wave 0 |
| PROX-05 | Reconstructed key never appears in response headers or body | integration | `uv run pytest tests/test_proxy.py -k "key_not_in_response" -x` | Wave 0 |
| PROX-05 | Key is zeroed after upstream request is prepared | unit | `uv run pytest tests/test_proxy.py -k "key_zeroed" -x` | Wave 0 |
| -- | All auth failures return identical 401 body | integration | `uv run pytest tests/test_proxy.py -k "auth_uniform_401" -x` | Wave 0 |
| -- | Health endpoints respond without auth | unit | `uv run pytest tests/test_proxy.py -k "health" -x` | Wave 0 |
| -- | Token counting from OpenAI/Anthropic responses | unit | `uv run pytest tests/test_metering.py -x` | Wave 0 |
| -- | Rules engine plugin chain short-circuits on denial | unit | `uv run pytest tests/test_rules.py -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/test_proxy.py tests/test_rules.py tests/test_metering.py -x`
- **Per wave merge:** `uv run pytest`
- **Phase gate:** Full suite green + proxy/API lane commands from TESTING.md

### Wave 0 Gaps
- [ ] `tests/test_proxy.py` -- covers CRYP-05, PROX-04, PROX-05 integration tests
- [ ] `tests/test_rules.py` -- covers rules engine unit tests (spend cap, rate limit, pipeline)
- [ ] `tests/test_metering.py` -- covers token extraction and spend recording
- [ ] `tests/conftest.py` -- needs proxy-layer fixtures (app client, enrolled test key, mock upstream)
- [ ] `fastapi` + `uvicorn` added to `pyproject.toml` dependencies
- [ ] `httpx[http2]` not needed -- HTTP/1.1 is sufficient for PoC

## Sources

### Primary (HIGH confidence)
- Existing codebase: `src/worthless/adapters/types.py`, `crypto/splitter.py`, `storage/repository.py` -- read directly
- CONTEXT.md -- locked decisions from user discussion
- SECURITY_RULES.md -- SR-01 through SR-08 constraints
- TESTING.md -- test lanes and package matrix

### Secondary (MEDIUM confidence)
- [FastAPI Proxy Discussion #9599](https://github.com/fastapi/fastapi/discussions/9599) -- reverse proxy patterns with StreamingResponse + BackgroundTask
- [httpx ASGITransport streaming issue #2186](https://github.com/encode/httpx/issues/2186) -- known limitation for SSE testing
- [OpenAI streaming usage](https://community.openai.com/t/openai-api-get-usage-tokens-in-response-when-set-stream-true/141866) -- stream_options.include_usage for token counting
- [Anthropic streaming docs](https://docs.anthropic.com/en/api/messages-streaming) -- message_delta event with usage.output_tokens

### Tertiary (LOW confidence)
- Rate limit in-memory vs SQLite tradeoff -- engineering judgment, no authoritative source

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - FastAPI + httpx + aiosqlite are already in use or standard for this domain
- Architecture: HIGH - pipeline pattern directly maps to the three invariants; existing code provides all building blocks
- Pitfalls: HIGH - well-documented issues (httpx cleanup, ASGITransport buffering, path traversal)
- Metering/token counting: MEDIUM - provider SSE formats are known but pass-through proxy injection of stream_options needs validation

**Research date:** 2026-03-20
**Valid until:** 2026-04-20 (stable domain, no fast-moving dependencies)
