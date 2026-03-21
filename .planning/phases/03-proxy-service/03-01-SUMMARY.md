---
phase: 03-proxy-service
plan: 01
subsystem: proxy
tags: [rules-engine, metering, rate-limit, spend-cap, bytearray, sr-01, sr-03]

# Dependency graph
requires:
  - phase: 01-crypto-core-and-storage
    provides: "Storage schema (shards, metadata tables), crypto splitter with bytearray output"
  - phase: 02-provider-adapters
    provides: "ProviderAdapter protocol, OpenAI/Anthropic adapters, SSE streaming relay"
provides:
  - "Rules engine (Rule protocol, RulesEngine chain, SpendCapRule, RateLimitRule)"
  - "Token extraction from OpenAI JSON/SSE and Anthropic SSE responses"
  - "Provider-compatible error response factories (401, 402, 429)"
  - "ProxySettings config from environment variables"
  - "spend_log and enrollment_config schema tables"
  - "Adapter api_key migrated from str to bytearray (SR-01)"
affects: [03-proxy-service, 04-cli-and-enrollment]

# Tech tracking
tech-stack:
  added: [aiosqlite]
  patterns: [gate-before-reconstruct, sliding-window-rate-limit, provider-format-errors]

key-files:
  created:
    - src/worthless/proxy/__init__.py
    - src/worthless/proxy/config.py
    - src/worthless/proxy/errors.py
    - src/worthless/proxy/rules.py
    - src/worthless/proxy/metering.py
    - tests/test_rules.py
    - tests/test_metering.py
    - tests/test_adapter_bytearray.py
  modified:
    - src/worthless/storage/schema.py
    - src/worthless/adapters/types.py
    - src/worthless/adapters/openai.py
    - src/worthless/adapters/anthropic.py
    - tests/test_adapters.py
    - tests/conftest.py

key-decisions:
  - "ErrorResponse is a lightweight frozen dataclass, not a FastAPI JSONResponse -- keeps rules engine testable without web framework dependency"
  - "RateLimitRule uses in-memory sliding window (not SQLite) for sub-millisecond evaluation"
  - "SpendCapRule queries spend_log SUM per request -- acceptable for PoC, may need Redis hot path later"
  - "Adapter api_key decode happens only at header insertion point, never stored as str"

patterns-established:
  - "Rule protocol: async evaluate(alias, request) -> ErrorResponse | None"
  - "Gate-before-reconstruct: rules engine runs before any key material is touched"
  - "Provider-format errors: error factories accept provider param for OpenAI/Anthropic format switching"

requirements-completed: [CRYP-05]

# Metrics
duration: 26min
completed: 2026-03-20
---

# Phase 3 Plan 1: Proxy Foundation Summary

**Rules engine with spend-cap/rate-limit gates, OpenAI/Anthropic metering extractors, and adapter bytearray migration (SR-01/SR-03)**

## Performance

- **Duration:** 26 min
- **Started:** 2026-03-20T18:54:23Z
- **Completed:** 2026-03-20T19:20:11Z
- **Tasks:** 2
- **Files modified:** 14

## Accomplishments
- Gate-before-reconstruct pipeline: RulesEngine short-circuits on first denial, SpendCapRule (402), RateLimitRule (429)
- Token extraction from OpenAI JSON/SSE and Anthropic SSE with graceful fallback to 0
- Adapter breaking change (api_key: str -> bytearray) with zero test regressions across 37 adapter tests
- 57 total tests passing across rules, metering, adapters, and streaming

## Task Commits

Each task was committed atomically:

1. **Task 1: Rules engine, metering, errors, config, schema** - `d8a321e` (test: RED) -> `413b2c9` (feat: GREEN)
2. **Task 2: Adapter bytearray migration** - `fc90cff` (test: RED) -> `2affe99` (feat: GREEN)

_TDD: each task has separate RED (failing test) and GREEN (implementation) commits_

## Files Created/Modified
- `src/worthless/proxy/__init__.py` - Package init
- `src/worthless/proxy/config.py` - ProxySettings from env vars
- `src/worthless/proxy/errors.py` - Provider-compatible error factories (401/402/429)
- `src/worthless/proxy/rules.py` - Rule protocol, RulesEngine, SpendCapRule, RateLimitRule
- `src/worthless/proxy/metering.py` - Token extraction and spend recording
- `src/worthless/storage/schema.py` - Added spend_log and enrollment_config tables
- `src/worthless/adapters/types.py` - api_key: str -> bytearray in ProviderAdapter protocol
- `src/worthless/adapters/openai.py` - bytearray decode at header insertion
- `src/worthless/adapters/anthropic.py` - bytearray decode at header insertion
- `tests/test_rules.py` - 10 tests for rules engine pipeline
- `tests/test_metering.py` - 10 tests for token extraction and spend recording
- `tests/test_adapter_bytearray.py` - 5 tests for bytearray adapter migration
- `tests/test_adapters.py` - Updated fixture type from str to bytearray
- `tests/conftest.py` - sample_api_key fixture returns bytearray

## Decisions Made
- ErrorResponse is a lightweight frozen dataclass (no FastAPI dependency) -- keeps rules engine independently testable
- RateLimitRule uses in-memory sliding window keyed by (alias, client_ip) for performance
- SpendCapRule queries SQLite per request (acceptable for PoC, Redis hot path deferred to metering plan)
- Adapter bytearray decode happens only at the header insertion point per SR-01

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Rules engine ready for integration into FastAPI request lifecycle (03-02)
- Metering extractors ready for post-response token counting
- Adapters ready for bytearray keys from reconstruct_key()
- Schema tables ready for enrollment and spend tracking

---
*Phase: 03-proxy-service*
*Completed: 2026-03-20*
