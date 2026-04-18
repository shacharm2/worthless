# WOR-196: OpenClaw Integration Test

> "Does the shard-A trick actually work when OpenClaw is the one making the API call?"

Worthless replaces real API keys with cryptographically useless shard-A values and routes
requests through a local proxy that reconstructs the key. OpenClaw reads API keys from
standard env vars and supports custom `baseUrl` providers. In theory, drop-in compatible.
This ticket proves it in practice.

## Research Findings

### OpenClaw Key Resolution (verified)

1. `~/.openclaw/openclaw.json` → `models.providers.<id>.apiKey`
2. Per-skill injection → `skills.entries.<skill>.env`
3. Host env vars → `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` (standard SDK names)
4. Shell import when `OPENCLAW_LOAD_SHELL_ENV=1`

BYOK model — skills declare `requires.env` in SKILL.md, never carry keys.

### OpenClaw Proxy Routing (verified)

Custom provider in `openclaw.json`:
```json
{
  "models": {
    "providers": {
      "worthless-proxy": {
        "baseUrl": "http://worthless-proxy:8787/v1",
        "apiKey": "<shard-A-value>",
        "api": "openai-completions",
        "models": [{"id": "gpt-4o", "name": "GPT-4o via Worthless"}]
      }
    }
  }
}
```

Also supports `OPENAI_BASE_URL` env var.

### OpenClaw Headless Trigger (verified)

OpenClaw Gateway exposes OpenAI-compatible endpoint at `http://localhost:18789/v1/chat/completions`.
Standard `httpx.post()` with `Authorization: Bearer <token>` triggers a completion through
the configured provider. No interactive session needed.

### Existing Test Patterns (from codebase)

| Pattern | File | Reuse for OpenClaw? |
|---------|------|---------------------|
| Fixture-based enrollment | `conftest.py` (`home_with_key`) | Yes — same split/store flow |
| Subprocess wrap + child | `test_e2e.py` | No — OpenClaw is the "child", not a script |
| Docker container lifecycle | `test_docker_e2e.py` | Yes — same build/run/exec/cleanup |
| Real provider transit | `test_e2e_live.py` | Stretch goal — hit real API through OpenClaw+Worthless |
| Mock upstream assertion | `test_e2e.py` tier 2 | Yes — mock captures Authorization header |

## Test Architecture

### Three containers

```
┌──────────────┐     ┌──────────────────┐     ┌────────────────┐
│   Test Host   │────▶│  OpenClaw        │────▶│ Worthless      │────▶│ Mock Upstream │
│  (httpx POST) │     │  :18789          │     │ Proxy :8787    │     │ :9999        │
│               │     │  baseUrl →       │     │ shard-B stored │     │ logs headers │
│               │     │  worthless:8787  │     │ reconstructs   │     │ returns 200  │
└──────────────┘     └──────────────────┘     └────────────────┘
                      apiKey = shard-A                                  ▲
                                                                        │
                                                          Authorization: Bearer <REAL-KEY>
```

### Mock Upstream

Tiny FastAPI app that:
- Accepts `POST /v1/chat/completions`
- Logs the `Authorization` header to a file or returns it in the response body
- Returns a valid OpenAI chat completion response

This is the assertion point: if the mock sees the **real key** (not shard-A), the full
chain works.

### Docker Compose (`tests/openclaw/docker-compose.yml`)

```yaml
services:
  mock-upstream:
    build: ./mock-upstream
    ports: ["9999:9999"]

  worthless-proxy:
    build: ../../
    environment:
      WORTHLESS_ALLOW_INSECURE: "true"
      PORT: "8787"
    volumes:
      - worthless-data:/data
      - worthless-secrets:/secrets
    depends_on:
      mock-upstream:
        condition: service_started

  openclaw:
    image: ghcr.io/openclaw/openclaw:latest
    environment:
      OPENCLAW_ACCEPT_TERMS: "yes"
    volumes:
      - ./openclaw-config:/home/node/.openclaw
    ports: ["18789:18789"]
    depends_on:
      worthless-proxy:
        condition: service_healthy

volumes:
  worthless-data:
  worthless-secrets:
```

### Test Flow

1. **Setup** (once per session):
   - `docker compose up -d`
   - Wait for all 3 services healthy
   - Generate test key, split it → shard-A + shard-B
   - Enroll shard-B into Worthless proxy via `docker exec worthless-proxy worthless enroll --key-stdin`
   - Write `openclaw.json` with `baseUrl: http://worthless-proxy:8787/v1` and `apiKey: <shard-A>`

2. **Test** (per test case):
   - `httpx.post("http://localhost:18789/v1/chat/completions", json={...})`
   - Or `docker exec openclaw acpx --prompt "hello" --no-wait`
   - Mock upstream logs the `Authorization` header it received

3. **Assert**:
   - Mock upstream received `Authorization: Bearer <REAL-KEY>` (not shard-A)
   - Worthless proxy logged a successful reconstruct
   - OpenClaw returned a valid completion response

4. **Teardown**:
   - `docker compose down -v`

## Deliverables

### 1. Automated test suite (`tests/test_openclaw_e2e.py`)

Pytest, `@pytest.mark.openclaw` + `@pytest.mark.docker`.
Follows `test_docker_e2e.py` patterns: session-scoped compose stack, per-test assertions.

Tests:
- `test_openclaw_shard_a_reconstructs` — core proof: shard-A in, real key out
- `test_openclaw_spend_cap_blocks` — rules engine denies before reconstruct
- `test_openclaw_base_url_env_var` — env var path (not just openclaw.json)
- `test_openclaw_streaming` — SSE streaming through full chain
- `test_openclaw_shard_a_leak_safe` — even if OpenClaw logs the key, it's just shard-A

### 2. Manual validation script (`tests/openclaw/run-test.sh`)

One-shot script for manual validation:
```bash
./tests/openclaw/run-test.sh
# Builds, starts, enrolls, triggers, asserts, tears down
# Human-readable output: PASS/FAIL with details
```

### 3. Mock upstream (`tests/openclaw/mock-upstream/`)

Reusable for any future provider integration tests. Captures and exposes headers.

## AC

- `pytest -m openclaw` passes in CI with Docker available
- `./tests/openclaw/run-test.sh` exits 0 on a clean machine with Docker
- Mock upstream proves the real key (not shard-A) reached the "provider"
