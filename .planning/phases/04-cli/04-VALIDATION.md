---
phase: 4
slug: cli
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-26
---

# Phase 4 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | `pyproject.toml` ([tool.pytest.ini_options]) |
| **Quick run command** | `uv run pytest tests/unit/ -x -q --tb=short` |
| **Full suite command** | `uv run pytest tests/ -x --tb=short` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/unit/ -x -q --tb=short`
- **After every plan wave:** Run `uv run pytest tests/ -x --tb=short`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 04-01-01 | 01 | 1 | CLI-01 | unit | `uv run pytest tests/unit/test_cli_lock.py -x` | ❌ W0 | ⬜ pending |
| 04-01-02 | 01 | 1 | CLI-01 | unit | `uv run pytest tests/unit/test_env_rewrite.py -x` | ❌ W0 | ⬜ pending |
| 04-01-03 | 01 | 1 | CLI-01 | integration | `uv run pytest tests/integration/test_lock_flow.py -x` | ❌ W0 | ⬜ pending |
| 04-02-01 | 02 | 1 | CLI-02 | unit | `uv run pytest tests/unit/test_cli_wrap.py -x` | ❌ W0 | ⬜ pending |
| 04-02-02 | 02 | 1 | CLI-02 | integration | `uv run pytest tests/integration/test_wrap_flow.py -x` | ❌ W0 | ⬜ pending |
| 04-03-01 | 03 | 2 | CLI-03 | unit | `uv run pytest tests/unit/test_cli_status.py -x` | ❌ W0 | ⬜ pending |
| 04-04-01 | 04 | 2 | CLI-04 | unit | `uv run pytest tests/unit/test_cli_scan.py -x` | ❌ W0 | ⬜ pending |
| 04-04-02 | 04 | 2 | CLI-04 | integration | `uv run pytest tests/integration/test_scan_hook.py -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/unit/test_cli_lock.py` — stubs for CLI-01 lock/unlock/enroll
- [ ] `tests/unit/test_env_rewrite.py` — stubs for prefix-preserving .env rewrite
- [ ] `tests/unit/test_cli_wrap.py` — stubs for CLI-02 wrap/up
- [ ] `tests/unit/test_cli_status.py` — stubs for CLI-03 status
- [ ] `tests/unit/test_cli_scan.py` — stubs for CLI-04 scan
- [ ] `tests/integration/test_lock_flow.py` — stubs for end-to-end lock flow
- [ ] `tests/integration/test_wrap_flow.py` — stubs for wrap process lifecycle
- [ ] `tests/integration/test_scan_hook.py` — stubs for pre-commit hook integration
- [ ] `tests/conftest.py` — shared fixtures (tmp ~/.worthless/, mock .env, mock proxy)

*Existing infrastructure covers pytest framework. Wave 0 adds CLI-specific test stubs.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| 90-second enrollment UX | CLI-01 | Timing depends on human interaction speed | Time `worthless lock` from fresh install with a test API key |
| Signal forwarding (SIGTERM/SIGKILL) | CLI-02 | Requires process signal inspection | Run `worthless wrap sleep 30`, send SIGTERM, verify child and proxy exit |
| Pre-commit hook blocks commit | CLI-04 | Requires actual git commit attempt | Stage .env with real key, run `git commit`, verify rejection |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
