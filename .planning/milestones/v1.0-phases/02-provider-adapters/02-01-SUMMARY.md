---
phase: 02-provider-adapters
plan: 01
subsystem: api
tags: [httpx, adapters, openai, anthropic, protocol, proxy]

requires:
  - phase: none
    provides: greenfield — no prior phase dependency
provides:
  - AdapterRequest/AdapterResponse dataclasses for upstream request/response
  - OpenAIAdapter with Bearer auth and URL transform
  - AnthropicAdapter with x-api-key, anthropic-version, and URL transform
  - Path-based adapter registry (get_adapter)
  - ProviderAdapter Protocol for future adapters
affects: [02-02-streaming, 03-proxy-service]

tech-stack:
  added: [httpx>=0.28, pytest, pytest-asyncio, ruff]
  patterns: [frozen-dataclass-contracts, protocol-based-adapters, stateless-transformers]

key-files:
  created:
    - src/worthless/adapters/types.py
    - src/worthless/adapters/openai.py
    - src/worthless/adapters/anthropic.py
    - src/worthless/adapters/registry.py
    - tests/test_adapters.py
    - tests/conftest.py
    - pyproject.toml
  modified:
    - src/worthless/adapters/__init__.py

key-decisions:
  - "Frozen dataclasses for AdapterRequest/AdapterResponse (immutable, simple, no pydantic needed)"
  - "Header keys lowercased during prepare_request for consistent downstream handling"
  - "x-worthless-* header stripping as denylist pattern (not allowlist) for maximum passthrough"

patterns-established:
  - "Protocol-based adapter interface: prepare_request (sync) + relay_response (async)"
  - "Stateless adapters: no instance state, all config via method args"
  - "Registry as dict lookup returning Optional adapter"

requirements-completed: [PROX-01, PROX-02]

duration: 4min
completed: 2026-03-15
---

# Phase 2 Plan 01: Adapter Contracts Summary

**Stateless OpenAI and Anthropic request/response adapters with frozen-dataclass contracts, path-based registry, and 11 passing unit tests**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-15T20:59:22Z
- **Completed:** 2026-03-15T21:03:30Z
- **Tasks:** 2
- **Files modified:** 10

## Accomplishments
- Defined ProviderAdapter protocol with AdapterRequest/AdapterResponse frozen dataclasses
- Implemented OpenAI adapter (Bearer auth, URL transform, transparent response relay)
- Implemented Anthropic adapter (x-api-key, anthropic-version default/preserve, transparent relay)
- Built path-based registry mapping /v1/chat/completions and /v1/messages to adapters
- All 11 unit tests green, ruff lint clean, public API imports verified

## Task Commits

Each task was committed atomically:

1. **Task 1: Define adapter contracts and write failing tests** - `4f48f4a` (test)
2. **Task 2: Implement adapters and registry to make tests pass** - `f09cdc5` (feat)

## Files Created/Modified
- `pyproject.toml` - Project configuration with httpx, pytest, ruff dependencies
- `src/worthless/__init__.py` - Package init
- `src/worthless/adapters/__init__.py` - Public API exports (6 symbols)
- `src/worthless/adapters/types.py` - AdapterRequest, AdapterResponse, ProviderAdapter Protocol
- `src/worthless/adapters/openai.py` - OpenAI adapter with Bearer auth transform
- `src/worthless/adapters/anthropic.py` - Anthropic adapter with x-api-key and version header
- `src/worthless/adapters/registry.py` - Path-based adapter lookup
- `tests/__init__.py` - Test package init
- `tests/conftest.py` - Shared fixtures (sample bodies, API key)
- `tests/test_adapters.py` - 11 unit tests covering all adapter behaviors

## Decisions Made
- Frozen dataclasses for request/response types (immutable, simple, no pydantic overhead)
- Header keys lowercased during prepare_request for consistent downstream handling
- Denylist pattern for header stripping (x-worthless-*) rather than allowlist for maximum passthrough
- hatchling as build backend with src layout

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed hatchling build backend path**
- **Found during:** Task 1 (project setup)
- **Issue:** Used `hatchling.backends` instead of `hatchling.build` as build-backend
- **Fix:** Corrected to `hatchling.build`
- **Files modified:** pyproject.toml
- **Verification:** uv sync succeeds
- **Committed in:** 4f48f4a (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Trivial typo fix, no scope creep.

## Issues Encountered
None beyond the build-backend typo.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Adapter contracts ready for 02-02 (SSE streaming relay)
- Phase 3 proxy service can import get_adapter, OpenAIAdapter, AnthropicAdapter
- ProviderAdapter Protocol established for adding future providers

---
*Phase: 02-provider-adapters*
*Completed: 2026-03-15*
