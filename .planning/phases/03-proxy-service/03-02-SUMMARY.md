---
phase: 03-proxy-service
plan: 02
subsystem: proxy
tags: [fastapi, httpx, gate-before-reconstruct, transparent-routing, server-side-reconstruction]

# Dependency graph
requires:
  - phase: 01-crypto-core-and-storage
    provides: "split_key, reconstruct_key, secure_key, ShardRepository"
  - phase: 02-provider-adapters
    provides: "ProviderAdapter protocol, OpenAI/Anthropic adapters, adapter registry"
  - phase: 03-proxy-service-plan-01
    provides: "RulesEngine, SpendCapRule, RateLimitRule, metering, error responses, ProxySettings"
provides:
  - "FastAPI proxy app with create_app() factory"
  - "Gate-before-reconstruct pipeline enforcing CRYP-05/SR-03"
  - "Transparent routing for OpenAI and Anthropic (PROX-04)"
  - "Server-side-only reconstruction (PROX-05)"
  - "Enrollment stub for seeding test keys"
  - "FastAPI dependency injection utilities"
affects: [04-cli-and-packaging, 05-documentation]

# Tech tracking
tech-stack:
  added: [fastapi, uvicorn]
  patterns: [app-factory-with-lifespan, asgi-transport-testing, gate-before-reconstruct-pipeline]

key-files:
  created:
    - src/worthless/proxy/app.py
    - src/worthless/proxy/dependencies.py
    - src/worthless/cli/__init__.py
    - src/worthless/cli/enroll_stub.py
    - tests/test_proxy.py
  modified:
    - src/worthless/proxy/__init__.py
    - pyproject.toml

key-decisions:
  - "ASGITransport does not run lifespan -- tests manually set app.state"
  - "Pre-computed uniform 401 body ensures byte-identical anti-enumeration responses"
  - "Streaming responses use BackgroundTask for metering after stream completes"
  - "Non-streaming responses use asyncio.create_task for fire-and-forget metering"

patterns-established:
  - "App factory pattern: create_app(settings) returns configured FastAPI instance"
  - "Manual state setup in tests: proxy_app fixture populates app.state since ASGITransport skips lifespan"
  - "Pipeline order enforcement: auth -> validate -> load -> GATE -> adapter -> reconstruct -> upstream"

requirements-completed: [CRYP-05, PROX-04, PROX-05]

# Metrics
duration: 15min
completed: 2026-03-20
---

# Phase 3 Plan 02: Proxy App Summary

**FastAPI proxy with gate-before-reconstruct pipeline, transparent OpenAI/Anthropic routing, and 20 integration tests proving all three architectural invariants**

## Performance

- **Duration:** 15 min
- **Started:** 2026-03-20T19:33:05Z
- **Completed:** 2026-03-20T21:47:50Z
- **Tasks:** 1 (TDD: RED + GREEN)
- **Files modified:** 7

## Accomplishments
- Gate-before-reconstruct proven: rules engine denials skip reconstruct_key entirely (mock-verified)
- Transparent routing: OpenAI (/v1/chat/completions) and Anthropic (/v1/messages) paths route to correct upstreams
- Key never in response: reconstructed key absent from all response headers and bodies
- Uniform 401: all auth failures (missing alias, unknown alias, missing shard_a, path traversal) return identical body
- Health endpoints (/healthz, /readyz) respond without authentication
- Enrollment stub enables seeding test keys for Phase 4

## Task Commits

Each task was committed atomically:

1. **Task 1 (RED): Proxy integration tests** - `387dbd7` (test)
2. **Task 1 (GREEN): FastAPI proxy implementation** - `210be48` (feat)

## Files Created/Modified
- `src/worthless/proxy/app.py` - FastAPI app factory with catch-all route, gate-before-reconstruct pipeline
- `src/worthless/proxy/dependencies.py` - FastAPI Depends for repo, httpx client, rules engine, settings
- `src/worthless/proxy/__init__.py` - Exports create_app
- `src/worthless/cli/__init__.py` - CLI package init
- `src/worthless/cli/enroll_stub.py` - Minimal enrollment function for test key seeding
- `tests/test_proxy.py` - 20 integration tests covering all invariants
- `pyproject.toml` - Added fastapi and uvicorn dependencies

## Decisions Made
- **ASGITransport testing pattern:** httpx.ASGITransport does not run ASGI lifespan events, so tests manually populate app.state via a proxy_app fixture. This is cleaner than adding asgi-lifespan dependency.
- **Pre-computed 401 body:** The uniform 401 response body is computed once at module level, ensuring byte-identical responses across all auth failure paths (anti-enumeration).
- **Streaming metering via BackgroundTask:** Streaming responses collect chunks and record spend in a BackgroundTask after the stream completes, avoiding blocking the response.
- **Non-streaming metering via create_task:** Non-streaming responses fire-and-forget the spend recording to avoid adding latency.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] ASGITransport lifespan not running**
- **Found during:** Task 1 GREEN phase
- **Issue:** httpx.ASGITransport does not run ASGI lifespan events, so app.state.repo/rules_engine/httpx_client were not initialized
- **Fix:** Created proxy_app fixture that manually initializes app.state, used by proxy_client fixture
- **Files modified:** tests/test_proxy.py
- **Verification:** All 20 tests pass
- **Committed in:** 210be48

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Test infrastructure adaptation only. No scope creep.

## Issues Encountered
- Pre-existing test_properties.py failure (Hypothesis passes str instead of bytearray for api_key) -- not caused by this plan's changes, confirmed by running against clean HEAD.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Full proxy pipeline operational with all three invariants proven
- Enrollment stub available for CLI phase (Phase 4) to build upon
- All 137 non-property tests pass across crypto, storage, adapters, streaming, proxy, rules, and metering

---
*Phase: 03-proxy-service*
*Completed: 2026-03-20*
