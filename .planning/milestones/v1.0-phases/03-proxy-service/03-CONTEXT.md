# Phase 3: Proxy Service - Context

**Gathered:** 2026-03-20
**Status:** Ready for planning

<domain>
## Phase Boundary

A running FastAPI proxy that enforces the three architectural invariants: client-side splitting, gate before reconstruction, server-side direct upstream call. Ships with spend cap + rate limit rules, post-response metering, and transparent routing for any HTTP client.

</domain>

<decisions>
## Implementation Decisions

### Key Identification
- Client sends `x-worthless-alias` header to identify which enrolled key to use
- Header is mandatory on every request — no fallback to default or single-key mode
- Shard A sent via `x-worthless-shard-a` header (base64-encoded), stripped before upstream
- If header is present, use it; if missing, fall back to file-based loading from `~/.worthless/shard_a/{alias}`
- Header wins over file — supports both sidecar (Phase 4) and pre-sidecar usage
- Alias-only auth for PoC — no separate proxy token required

### Auth Error Handling (Anti-Enumeration)
- All auth failures (missing alias, missing shard, bad shard, unknown alias, failed HMAC commitment) return **identical 401 body**: `"authentication required"` with no distinction between failure reasons
- Prevents attackers from enumerating enrolled keys or probing which authentication step failed
- Status codes differentiate denial TYPE (401 auth / 402 spend / 429 rate), never the reason within a type

### Rules Engine
- Gate hook always runs before reconstruction (SR-03) — with zero rules configured, it passes through
- **Spend cap**: off by default, user sets their own budget per enrollment. Checked against accumulated spend before allowing next request
- **Rate limit**: on by default with generous threshold (~100 req/s per IP). Catches runaway agents and replay attacks
- Both thresholds configurable per enrollment
- Plugin architecture: future rules (anomaly detection, ML spend velocity, model allowlist) drop into the same pipeline
- State stored in SQLite (extends existing ShardRepository DB with metering tables)

### Metering
- **Post-response token counting**: parse `usage.total_tokens` from OpenAI response, `message_stop` event from Anthropic
- Metering write is async/fire-and-forget — runs after response is already streaming back, zero added latency
- Accepted trade-off: one request can overshoot the spend cap (bounded by single-request cost)
- Pre-estimation requires tokenizer dependency and is inaccurate — deferred to backlog

### Error Responses
- Provider-compatible JSON format: mirror OpenAI/Anthropic error schemas so SDK error handling works unchanged
- HTTP status codes: 402 (spend cap exceeded), 429 (rate limit), 401 (all auth failures)
- Rate limit responses include `Retry-After` header
- Upstream errors (400, 500, etc.) passed through transparently — proxy is invisible for provider errors

### Transparent Routing
- Stack-agnostic: works with any HTTP client (curl, SDKs, Go, Node) that sends correct path + x-worthless-* headers
- No thin SDK wrapper — that couples to SDK versions and breaks transparency
- Health endpoints: `/healthz` (liveness) + `/readyz` (DB connected, at least one key enrolled), no auth required
- Configurable upstream timeouts: 120s default for non-streaming, 300s for streaming, via env vars

### Security Constraints (from review)
1. Proxy must refuse shard headers over non-TLS connections — enforces mTLS always
2. Strip `x-worthless-*` from upstream responses to prevent injection
3. Disable redirect following on upstream httpx client
4. File-based shard loader (`~/.worthless/shard_a/`) — never pass shards via CLI flags or env vars (shell history leak)
5. Reject request header keys containing whitespace or null bytes
6. Deny CORS by default
7. Registry needs query-param stripping before path lookup

### Adapter Interface Change
- `api_key: str` parameter in `prepare_request()` must change to `api_key: bytearray` to comply with SR-01 memory zeroing
- This is a breaking change to the Phase 2 adapter interface — required for security correctness

### Minimal Enrollment Stub
- Phase 3 needs a minimal CLI command to seed test keys into the database
- Foundation for Phase 4's full `worthless enroll` command
- Just enough to get shard_b + commitment + nonce into SQLite for testing

### Claude's Discretion
- Logging strategy (structured logging, log levels, what gets logged)
- Graceful shutdown handling
- httpx client lifecycle (connection pooling, keep-alive)
- Request body size limits
- FastAPI app structure (single file vs module)
- Token counting implementation details for each provider's SSE format

</decisions>

<specifics>
## Specific Ideas

- Uniform 401 body is a deliberate anti-enumeration measure — the proxy should never leak which step of auth failed
- Metering should be zero-latency: async write after response is already streaming
- The gate must be architecturally a pipeline/chain so future rules plug in without restructuring
- File-based shard_a loading at `~/.worthless/shard_a/{alias}` for pre-sidecar developer experience

</specifics>

<code_context>
## Existing Code Insights

### Reusable Assets
- `crypto.splitter.reconstruct_key()` + `secure_key()` context manager: XOR reconstruction with HMAC verification and memory zeroing
- `adapters.registry.get_adapter(path)`: maps request path to OpenAI/Anthropic adapter
- `adapters.types.ProviderAdapter` protocol: `prepare_request()` + `relay_response()` — the proxy's core integration point
- `adapters.types.relay_response()`: shared SSE streaming relay with hop-by-hop header stripping
- `storage.repository.ShardRepository`: async SQLite repo with Fernet encryption at rest, `.retrieve(alias)` returns shard_b + commitment + nonce
- `exceptions.ShardTamperedError`: raised on HMAC failure during reconstruction

### Established Patterns
- All crypto uses `bytearray` (SR-01), `secrets.token_bytes()` (SR-08), `hmac.compare_digest()` (SR-07)
- `AdapterRequest.__repr__()` redacts Authorization and x-api-key headers (SR-04)
- `strip_internal_headers()` removes `x-worthless-*` and hop-by-hop headers before upstream
- `aiosqlite` for async DB access, `cryptography.fernet` for encryption at rest
- `httpx` already a dependency for upstream HTTP calls

### Integration Points
- FastAPI app will import `get_adapter()`, `reconstruct_key()`, `secure_key()`, `ShardRepository`
- New metering tables extend existing SQLite schema in `storage/schema.py`
- `pyproject.toml` needs `fastapi` and `uvicorn` added to dependencies

</code_context>

<deferred>
## Deferred Ideas

- Pre-estimation of token costs before sending request (requires tokenizer dependency)
- Pre+post hybrid spend enforcement for tighter budget control
- Model allowlist rule
- Token budget rule
- Time window rule
- Anomaly detection (spend velocity)
- mTLS client certificate auth for proxy access
- Separate proxy bearer token authentication
- Thin SDK wrappers for Python OpenAI/Anthropic clients

</deferred>

---

*Phase: 03-proxy-service*
*Context gathered: 2026-03-20*
