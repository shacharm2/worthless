# Architecture Patterns

**Domain:** Split-key API proxy for LLM provider credentials
**Researched:** 2026-03-14

## Recommended Architecture

Worthless is a **split-key reverse proxy** -- a specialized LLM gateway where the defining constraint is that the complete API key never exists at rest anywhere. The architecture has five distinct components with strict trust boundaries between them.

```
                         CLIENT SIDE                    |              SERVER SIDE
                                                        |
  Developer App                                         |
       |                                                |
  [env: OPENAI_API_KEY=worthless://...]                 |
       |                                                |
  CLI Sidecar (localhost)                               |
       |  attaches Shard A + nonce                      |
       v                                                |
  +-----------+    Shard A in header     +-----------+  |  +-------------------+
  |  HTTP     | ----------------------> |   Proxy   |  |  | Reconstruction    |
  |  Client   |                         |  (FastAPI) | --> |  Service (Rust)   |
  |  (any     | <---------------------- |  Gate +    |  |  | XOR + upstream    |
  |  language)|    proxied response     |  Meter     |  |  | call + zeroize    |
  +-----------+                         +-----------+  |  +-------------------+
                                              |         |         |
                                         +---------+   |    LLM Provider
                                         | SQLite  |   |   (OpenAI/Anthropic)
                                         | (shards,|   |
                                         |  rules, |   |
                                         |  usage) |   |
                                         +---------+   |
```

### Trust Boundary Model

| Zone | Has Access To | Never Sees |
|------|---------------|------------|
| Client / CLI Sidecar | Shard A, nonce, commitment | Shard B, full key |
| Proxy (FastAPI) | Shard B (encrypted), rules, usage data | Shard A, full key |
| Reconstruction (Rust) | Both shards (ephemerally), full key (ephemerally) | Nothing persisted -- zeroized after upstream call |
| SQLite store | Shard B (encrypted at rest), rules, usage logs | Shard A, full key, plaintext Shard B |
| LLM Provider | Full key (in Authorization header over TLS) | Shards, internal architecture |

This is the critical insight: **no single component has enough information to reconstruct the key except the Reconstruction Service, and it holds the key only in volatile memory for the duration of one upstream HTTP call.**

## Component Boundaries

### 1. CLI / Sidecar (Python)

**Responsibility:** Key splitting, enrollment, transparent request interception.

| Subcomponent | What It Does |
|---|---|
| `enroll` command | Takes a plaintext API key, generates random Shard A, XORs to produce Shard B, stores Shard A in OS keychain, sends Shard B + commitment + nonce to proxy |
| `wrap` command | Sets environment variables so `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` point to the local sidecar URL instead of the real provider |
| Sidecar daemon | Localhost HTTP server that intercepts outbound LLM calls, attaches Shard A + nonce in a custom header (`X-Worthless-Shard`), forwards to proxy |

**Communicates with:** Proxy (HTTP, Shard A delivery per-request), OS Keychain (Shard A storage).

**Key design decision:** The sidecar runs on localhost only. It is a thin passthrough -- it does NOT parse or modify request/response bodies. It adds one header and forwards. This keeps it language-agnostic: any HTTP client in any language works unchanged.

### 2. Proxy Service (Python / FastAPI)

**Responsibility:** Gate enforcement, metering, request routing. This is the policy engine.

| Subcomponent | What It Does |
|---|---|
| Auth middleware | Validates the incoming request has a valid Shard A header and matches a known enrollment (via commitment check) |
| Rules engine | Evaluates spend caps, rate limits, model allowlists, token budgets, time windows BEFORE any reconstruction happens |
| Metering | Tracks token usage and estimated cost per enrollment, per time window |
| Router | Determines which LLM provider to call based on the request path (`/v1/chat/completions` -> OpenAI, `/v1/messages` -> Anthropic) |
| SSE relay | Streams responses back to the client using `EventSourceResponse` for streaming LLM completions |

**Communicates with:** CLI Sidecar (receives requests), Reconstruction Service (forwards approved requests), SQLite (reads rules, writes usage).

**Architectural invariant enforced here:** Gate before reconstruction. The rules engine runs BEFORE the proxy touches Shard B or calls the reconstruction service. A denied request means zero cryptographic operations, zero key material touched.

### 3. Reconstruction Service (Rust, PoC: Python)

**Responsibility:** Ephemeral key reconstruction and direct upstream call. This is the security-critical hot path.

| Subcomponent | What It Does |
|---|---|
| Shard combiner | XORs Shard A + decrypted Shard B to reconstruct the full API key |
| Upstream caller | Makes the actual HTTP request to the LLM provider with the reconstructed key in the Authorization header |
| Memory zeroing | Immediately zeroizes the reconstructed key from memory after the upstream call completes (Rust: `zeroize` crate; PoC Python: `ctypes` memset) |
| Response relay | Streams the provider response back to the proxy (which relays to the client) |

**Communicates with:** Proxy (receives approved requests with both shards), LLM Provider (direct HTTPS call), nothing else.

**Architectural invariant enforced here:** Server-side direct upstream call. The reconstructed key NEVER returns to the proxy. The reconstruction service calls the provider directly and streams the response back. The key exists only in the reconstruction service's volatile memory.

**PoC vs Production:**
- **PoC (Phase 1):** Python, in-process with the proxy (same FastAPI app, separate module). Simpler to build, still enforces the invariants logically even without process isolation.
- **Production (Phase 2+):** Rust, separate process/container, distroless image, `zeroize` crate for guaranteed memory clearing, no logging of key material. Process isolation means a proxy compromise does not leak keys.

### 4. Storage Layer (SQLite)

**Responsibility:** Persistent state for enrollments, rules, and usage data.

| Table | Contents | Security |
|---|---|---|
| `enrollments` | Shard B (encrypted), commitment hash, nonce, provider type, created_at | Shard B encrypted with server-side encryption key |
| `rules` | spend_cap, rate_limit, model_allowlist, token_budget, time_window per enrollment | Plaintext (policy data, not secrets) |
| `usage` | Token counts, estimated cost, timestamps per enrollment | Plaintext (operational data) |

**Why SQLite:** Local-only dogfood mode. No network database dependency. Single file, easy backup, WAL mode for concurrent reads. Upgrade path to PostgreSQL for hosted mode is straightforward via SQLAlchemy.

### 5. Provider Adapters

**Responsibility:** Translate between the proxy's internal request format and each LLM provider's API.

| Provider | Base URL | Auth Header Format | Streaming |
|---|---|---|---|
| OpenAI | `https://api.openai.com/v1/` | `Authorization: Bearer sk-...` | SSE via `stream: true` |
| Anthropic | `https://api.anthropic.com/v1/` | `x-api-key: sk-ant-...` | SSE via `stream: true` |

Each adapter handles: request validation, header formatting, response parsing, streaming relay, error mapping. Adapters are stateless and pure -- they transform data, they do not store it.

## Data Flow: Complete Request Lifecycle

### Happy Path (non-streaming)

```
1. App calls POST https://localhost:8787/v1/chat/completions
   (thinks it's talking to OpenAI because `wrap` set OPENAI_BASE_URL)

2. CLI Sidecar receives request
   - Reads Shard A from OS keychain
   - Attaches X-Worthless-Shard: <shard_a_hex>
   - Attaches X-Worthless-Nonce: <nonce>
   - Forwards to proxy at localhost:8788

3. Proxy receives request
   a. Auth middleware: validates shard + nonce match a known enrollment
   b. Rules engine: checks spend cap, rate limit, model allowlist
      - DENIED? Return 429/403 immediately. No reconstruction. Done.
   c. Metering: estimates token count for this request
   d. Router: determines this is an OpenAI request

4. Proxy calls Reconstruction Service (in-process for PoC)
   - Passes: Shard A (from header), encrypted Shard B (from DB), request body
   - Reconstruction Service:
     a. Decrypts Shard B
     b. XORs Shard A ^ Shard B = full API key
     c. Calls OpenAI directly with full key
     d. Receives response
     e. Zeroizes key from memory
     f. Returns response to proxy

5. Proxy receives response from Reconstruction Service
   - Updates usage metering (tokens used, cost)
   - Returns response to sidecar

6. Sidecar returns response to app (unchanged)
```

### Streaming Path

Same as above, except step 4c-4f becomes:
```
4c. Calls OpenAI with stream:true
4d. For each SSE chunk:
    - Reconstruction Service streams chunk to Proxy
    - Proxy streams chunk to Sidecar via EventSourceResponse
    - Sidecar streams chunk to App
4e. After final chunk: zeroize key
4f. Proxy updates metering with final token count (from stream usage chunk)
```

Streaming uses `httpx.AsyncClient.stream()` on the Rust/Python side and `sse-starlette` / FastAPI's `EventSourceResponse` on the proxy side. Critical: set `X-Accel-Buffering: no` and `Cache-Control: no-cache` headers to prevent intermediate buffering.

## Patterns to Follow

### Pattern 1: Gate-Before-Reconstruct

**What:** Every request must pass through the rules engine before any cryptographic operation occurs.

**Why:** This is a core security invariant. If reconstruction happens before gating, a compromised rules engine could be bypassed -- the key would already be in memory.

**Implementation:**
```python
# In proxy request handler
async def handle_request(request: Request):
    enrollment = await authenticate(request)  # Step 1: who is this?
    await enforce_rules(enrollment, request)   # Step 2: are they allowed?
    # Only AFTER both pass:
    response = await reconstruct_and_call(enrollment, request)  # Step 3: reconstruct
    await update_metering(enrollment, response)  # Step 4: record usage
    return response
```

### Pattern 2: Ephemeral Key Lifetime

**What:** The reconstructed key exists only for the duration of the upstream HTTP call. It is never stored, logged, cached, or returned.

**Implementation (PoC Python):**
```python
import ctypes

async def reconstruct_and_call(shard_a: bytes, shard_b: bytes, request_body: dict):
    key = bytes(a ^ b for a, b in zip(shard_a, shard_b))
    try:
        response = await call_provider(key, request_body)
        return response
    finally:
        # Overwrite key in memory (best-effort in Python)
        ctypes.memset(id(key) + sys.getsizeof(key) - len(key), 0, len(key))
```

**Implementation (Production Rust):**
```rust
use zeroize::Zeroize;

fn reconstruct_and_call(shard_a: &[u8], shard_b: &[u8], body: &[u8]) -> Response {
    let mut key: Vec<u8> = shard_a.iter().zip(shard_b).map(|(a, b)| a ^ b).collect();
    let response = call_provider(&key, body);
    key.zeroize(); // Guaranteed not optimized away
    response
}
```

### Pattern 3: Transparent Proxy via Environment Override

**What:** The sidecar works by overriding `OPENAI_BASE_URL` and `ANTHROPIC_API_URL` to point to localhost. No SDK changes needed in any language.

**Why:** Stack-agnostic. Python, Node, Go, Rust -- any OpenAI/Anthropic SDK respects base URL environment variables.

```bash
# What `worthless wrap` does:
export OPENAI_BASE_URL=http://localhost:8787/v1
export ANTHROPIC_API_URL=http://localhost:8787
# The API key env vars now hold a worthless token (enrollment ID)
export OPENAI_API_KEY=worthless_enroll_abc123
```

### Pattern 4: Commitment Scheme for Shard Verification

**What:** At enrollment, the client computes `commitment = SHA256(shard_a || nonce)` and sends it to the server along with Shard B. On each request, the server verifies `SHA256(received_shard_a || nonce) == stored_commitment` before reconstruction.

**Why:** Prevents shard substitution attacks. Without this, an attacker who compromises the server could send a known Shard A and extract Shard B from the XOR result.

## Anti-Patterns to Avoid

### Anti-Pattern 1: Key Reconstruction in the Proxy Process

**What:** Reconstructing the full key inside the FastAPI proxy process.

**Why bad:** The proxy handles auth, routing, metering, logging -- it has a large attack surface. If compromised, the attacker gets the full key. In PoC this is acceptable (same process, different module with clear boundaries), but production MUST use process isolation.

**Instead:** Separate reconstruction into its own minimal process (Rust distroless container) that has no logging, no admin endpoints, no debugging tools.

### Anti-Pattern 2: Logging Request Headers in Debug Mode

**What:** Using FastAPI middleware that logs all headers (including `X-Worthless-Shard`).

**Why bad:** Shard A in logs = half the key material in plaintext on disk.

**Instead:** Scrub security headers from all log output. Use an allowlist of loggable headers, not a denylist.

### Anti-Pattern 3: Caching Reconstructed Keys

**What:** Caching the full key to avoid XOR computation on repeated requests.

**Why bad:** Eliminates the core security property. A cached key is a stored key. The 90-nanosecond XOR operation is not a bottleneck worth optimizing.

**Instead:** Reconstruct on every request. The XOR is negligible; the HTTP round-trip to the provider dominates latency.

### Anti-Pattern 4: Returning the Reconstructed Key to the Proxy

**What:** Having the reconstruction service return the key to the proxy, which then calls the provider.

**Why bad:** Violates architectural invariant #3. The key transits the network (even localhost) and exists in the proxy's memory.

**Instead:** The reconstruction service makes the upstream call directly and returns only the provider's response.

## Suggested Build Order

The architecture has clear dependency layers. Build bottom-up:

### Layer 1: Storage + Models (no dependencies)
- SQLite schema (enrollments, rules, usage)
- Pydantic models for all data types
- This is the foundation everything else reads/writes

### Layer 2: Crypto Core (depends on: models)
- XOR split/combine functions
- Commitment scheme (SHA256)
- Shard encryption/decryption for at-rest storage
- Unit-testable in isolation, no HTTP needed

### Layer 3: Provider Adapters (depends on: models)
- OpenAI adapter (request transform, response parse, streaming)
- Anthropic adapter (same)
- Can be tested against real APIs with real keys (before proxy exists)

### Layer 4: Reconstruction Service (depends on: crypto, adapters)
- Combines shards, calls provider, zeroizes
- In PoC: a Python module. In production: a Rust binary.
- Test: give it two shards + a request body, verify it calls the right provider

### Layer 5: Proxy / Rules Engine (depends on: storage, reconstruction)
- FastAPI app with auth middleware, rules engine, metering, SSE relay
- This is the largest component but depends on everything below it

### Layer 6: CLI / Sidecar (depends on: crypto, proxy API)
- `enroll` command (uses crypto core for splitting, calls proxy API)
- `wrap` command (sets env vars)
- Sidecar daemon (localhost proxy, attaches shard header)

**Build order implication for phases:** Layers 1-3 can be built in parallel. Layer 4 needs 2+3. Layer 5 needs 1+4. Layer 6 needs 2+5. The critical path is: Models -> Crypto -> Reconstruction -> Proxy -> CLI.

## Scalability Considerations

| Concern | Local Dogfood (1 user) | Self-Hosted Team (10 users) | Hosted SaaS (1000+ users) |
|---|---|---|---|
| Storage | SQLite, single file | SQLite WAL mode or PostgreSQL | PostgreSQL with connection pooling |
| Reconstruction | In-process Python module | Separate Rust process, single instance | Rust service pool, horizontal scale |
| Metering | Synchronous SQLite writes | Async writes, WAL mode | Redis hot path + async DB flush |
| Streaming | Single connection, no buffering issues | uvicorn workers | Load balancer with SSE support (no buffering) |
| Key storage | OS keychain | OS keychain per developer | HSM / KMS for Shard B encryption keys |

The architecture scales by extracting components into separate services: reconstruction becomes a Rust microservice, metering gets a Redis hot path, storage moves to PostgreSQL. The interfaces between components do not change -- only the transport (in-process call becomes HTTP/gRPC call).

## Sources

- [API7.ai: How API Gateways Proxy LLM Requests](https://api7.ai/learning-center/api-gateway-guide/api-gateway-proxy-llm-requests) - LLM gateway architecture patterns
- [Zeroize crate documentation](https://docs.rs/zeroize/latest/zeroize/) - Rust memory zeroing for key material
- [Cryptographic splitting - Wikipedia](https://en.wikipedia.org/wiki/Cryptographic_splitting) - XOR-based secret sharing fundamentals
- [FastAPI SSE documentation](https://fastapi.tiangolo.com/tutorial/server-sent-events/) - Server-Sent Events implementation
- [sse-starlette](https://github.com/sysid/sse-starlette) - Production SSE for FastAPI/Starlette
- [LiteLLM Proxy](https://docs.litellm.ai/docs/simple_proxy) - Reference LLM gateway architecture
- [Split-key encryption (Medium)](https://iampankajsharma.medium.com/split-key-encryption-securing-the-data-at-rest-fcd3e578b3ce) - XOR component generation and storage distribution patterns
- [NIST: Split Knowledge](https://csrc.nist.gov/glossary/term/split_knowledge) - Formal definition of split knowledge in cryptographic systems
