---
phase: 3
slug: proxy-service
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-20
---

# Phase 3 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.0+ with pytest-asyncio 0.24+ |
| **Config file** | `pyproject.toml` [tool.pytest.ini_options] |
| **Quick run command** | `uv run pytest tests/test_proxy.py tests/test_rules.py tests/test_metering.py -x` |
| **Full suite command** | `uv run pytest` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/test_proxy.py tests/test_rules.py tests/test_metering.py -x`
- **After every plan wave:** Run `uv run pytest`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 03-01-01 | 01 | 1 | CRYP-05 | unit+integration | `uv run pytest tests/test_proxy.py -k "gate_before_reconstruct" -x` | ❌ W0 | ⬜ pending |
| 03-01-02 | 01 | 1 | CRYP-05 | integration | `uv run pytest tests/test_proxy.py -k "spend_cap_denied" -x` | ❌ W0 | ⬜ pending |
| 03-01-03 | 01 | 1 | CRYP-05 | integration | `uv run pytest tests/test_proxy.py -k "rate_limit_denied" -x` | ❌ W0 | ⬜ pending |
| 03-01-04 | 01 | 1 | PROX-04 | integration | `uv run pytest tests/test_proxy.py -k "transparent_routing" -x` | ❌ W0 | ⬜ pending |
| 03-01-05 | 01 | 1 | PROX-04 | unit | `uv run pytest tests/test_proxy.py -k "adapter_routing" -x` | ❌ W0 | ⬜ pending |
| 03-01-06 | 01 | 1 | PROX-05 | integration | `uv run pytest tests/test_proxy.py -k "key_not_in_response" -x` | ❌ W0 | ⬜ pending |
| 03-01-07 | 01 | 1 | PROX-05 | unit | `uv run pytest tests/test_proxy.py -k "key_zeroed" -x` | ❌ W0 | ⬜ pending |
| 03-02-01 | 02 | 1 | -- | integration | `uv run pytest tests/test_proxy.py -k "auth_uniform_401" -x` | ❌ W0 | ⬜ pending |
| 03-02-02 | 02 | 1 | -- | unit | `uv run pytest tests/test_proxy.py -k "health" -x` | ❌ W0 | ⬜ pending |
| 03-02-03 | 02 | 2 | -- | unit | `uv run pytest tests/test_metering.py -x` | ❌ W0 | ⬜ pending |
| 03-02-04 | 02 | 2 | -- | unit | `uv run pytest tests/test_rules.py -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_proxy.py` — stubs for CRYP-05, PROX-04, PROX-05 integration tests
- [ ] `tests/test_rules.py` — stubs for rules engine unit tests (spend cap, rate limit, pipeline)
- [ ] `tests/test_metering.py` — stubs for token extraction and spend recording
- [ ] `tests/conftest.py` — proxy-layer fixtures (app client, enrolled test key, mock upstream)
- [ ] `fastapi` + `uvicorn` added to `pyproject.toml` dependencies

*Existing infrastructure covers crypto and adapter tests from Phases 1-2.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| mTLS enforcement (non-TLS rejected) | Security constraint | Requires TLS termination setup | Start proxy with TLS cert, verify shard headers rejected over plain HTTP |
| SSE streaming real-time arrival | PROX-04 | Timing-sensitive, hard to assert in CI | `curl --no-buffer` to proxy, verify chunks arrive incrementally |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
