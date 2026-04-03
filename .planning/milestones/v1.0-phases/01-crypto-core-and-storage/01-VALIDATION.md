---
phase: 1
slug: crypto-core-and-storage
status: validated
nyquist_compliant: true
wave_0_complete: true
created: 2026-03-14
validated: 2026-03-18
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio |
| **Config file** | `pyproject.toml` (`[tool.pytest.ini_options]` section — Wave 0 creates) |
| **Quick run command** | `pytest tests/ -x -q` |
| **Full suite command** | `pytest tests/ -v --tb=short` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/ -x -q`
- **After every plan wave:** Run `pytest tests/ -v --tb=short && ruff check src/ --select TID251`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 01-01-01 | 01 | 0 | CRYP-01 | unit | `pytest tests/test_splitter.py::test_xor_roundtrip -x` | ✅ | ✅ green |
| 01-01-02 | 01 | 0 | CRYP-01 | unit | `pytest tests/test_splitter.py::test_shard_length -x` | ✅ | ✅ green |
| 01-01-03 | 01 | 0 | CRYP-01 | unit | `pytest tests/test_splitter.py::test_shards_differ_from_key -x` | ✅ | ✅ green |
| 01-01-04 | 01 | 0 | CRYP-02 | unit | `pytest tests/test_splitter.py::test_hmac_valid -x` | ✅ | ✅ green |
| 01-01-05 | 01 | 0 | CRYP-02 | unit | `pytest tests/test_splitter.py::test_hmac_tampered_shard_a -x` | ✅ | ✅ green |
| 01-01-06 | 01 | 0 | CRYP-02 | unit | `pytest tests/test_splitter.py::test_hmac_tampered_shard_b -x` | ✅ | ✅ green |
| 01-01-07 | 01 | 0 | CRYP-03 | unit | `pytest tests/test_splitter.py::test_reconstruct_returns_bytearray -x` | ✅ | ✅ green |
| 01-01-08 | 01 | 0 | CRYP-03 | unit | `pytest tests/test_splitter.py::test_bytearray_zeroed -x` | ✅ | ✅ green |
| 01-01-09 | 01 | 0 | CRYP-04 | lint | `ruff check src/ --select TID251` | ✅ | ✅ green |
| 01-02-01 | 02 | 1 | STOR-01 | integration | `pytest tests/test_storage.py::test_shard_roundtrip -x` | ✅ | ✅ green |
| 01-02-02 | 02 | 1 | STOR-01 | integration | `pytest tests/test_storage.py::test_shard_encrypted_at_rest -x` | ✅ | ✅ green |
| 01-02-03 | 02 | 1 | STOR-02 | integration | `pytest tests/test_storage.py::test_metadata_persistence -x` | ✅ | ✅ green |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [x] `pyproject.toml` — project config with dependencies, ruff config, pytest config
- [x] `tests/conftest.py` — shared fixtures (temp SQLite DB, sample API key bytes, Fernet key)
- [x] `tests/test_splitter.py` — 21 tests for CRYP-01, CRYP-02, CRYP-03
- [x] `tests/test_storage.py` — 6 tests for STOR-01, STOR-02
- [x] Framework install: cryptography, aiosqlite, pytest, pytest-asyncio, ruff

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Memory zeroing effectiveness | CRYP-03 | Python GC behavior is non-deterministic — cannot programmatically prove no residual copies | 1. Reconstruct key in debugger 2. Exit context manager 3. Inspect heap for residual bytes 4. Document finding in SECURITY_POSTURE.md |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 10s (27 tests in ~0.13s)
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** validated 2026-03-18

## Validation Audit 2026-03-18
| Metric | Count |
|--------|-------|
| Gaps found | 0 |
| Resolved | 0 |
| Escalated | 0 |
| Total automated tests | 27 (21 splitter + 6 storage) |
| Lint checks | 1 (ruff TID251) |
| Manual-only | 1 (memory zeroing heap inspection) |
