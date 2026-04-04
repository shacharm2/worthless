# Worthless Proxy — TestSprite Test Instructions

You are testing a **split-key API proxy**, not an LLM. A mock upstream
returns static responses. Your target is the proxy on :8000 which handles
auth, routing, and enforcement.

## Base URL

All requests go to `http://localhost:8000`. No API key or Authorization
header is needed — the proxy uses file-based auth with alias inference
(already configured by the test harness).

## Test Areas

### 1. Health Endpoints (no auth required)
- `GET /` → 200, body `{"status": "ok"}`
- `GET /healthz` → 200, body `{"status": "ok"}`
- `GET /readyz` → 200, body `{"status": "ok"}`

### 2. OpenAI Happy Path
- `POST /v1/chat/completions` with `{"model":"gpt-4","messages":[{"role":"user","content":"hi"}]}` → 200
- Response is valid JSON with `choices` array and `usage` object
- Do NOT assert on actual message content (mock upstream)

### 3. Anthropic Happy Path
- `POST /v1/messages` with `{"model":"claude-3-5-sonnet-20241022","messages":[{"role":"user","content":"hi"}],"max_tokens":100}` → 200
- Response is valid JSON with `content` array and `usage` object
- Do NOT assert on actual message content

### 4. Streaming
- `POST /v1/chat/completions` with `"stream": true` → `Content-Type: text/event-stream`
- `POST /v1/messages` with `"stream": true` → `Content-Type: text/event-stream`
- Verify response contains `data:` lines (SSE format)

### 5. Anti-Enumeration (uniform 401)
- `GET /v1/models` → 401
- `GET /nonexistent/path` → 401
- `POST /v1/embeddings` → 401
- All 401 responses have identical JSON: `{"error": {"message": ..., "type": ..., "param": null, "code": null}}`
- The proxy NEVER returns 403 or 404 — every unauthorized/unknown request is 401

### 6. Oversized Request Body
- `POST /v1/chat/completions` with 20 MB body → 413

### 7. Response Header Sanitization
- On any 200 response, no header starts with `x-worthless-`

## Constraints

- **No custom auth headers.** Do not send `Authorization`, `x-api-key`,
  `x-worthless-key`, or `x-worthless-shard-a`. The proxy infers everything.
- **Do not test rate limiting (429) or spend caps (402).** These need
  accumulated state impractical in a short test run.
- **Do not assert on LLM content.** Assert on status codes, JSON structure,
  and headers only.
- **Uniform 401 is intentional.** Do not flag identical error responses or
  try to differentiate auth failure reasons. Anti-enumeration by design.
- **No TLS testing.** Harness runs `allow_insecure=True`. TLS tests would
  pass here but differ in production — skip entirely.
- **No alias enumeration.** Do not brute-force `x-worthless-key` values.
- **No real API keys.** The harness uses generated fake keys.
- **Upstream errors are sanitized.** The proxy strips provider error
  messages and returns generic text. Not a bug.
- **CORS is fully denied.** Browser-origin requests fail by design.
- **No OpenAPI spec.** `docs_url`, `redoc_url`, `openapi_url` are disabled.
  Do not report missing docs endpoints.
