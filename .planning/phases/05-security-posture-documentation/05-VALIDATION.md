---
phase: 5
slug: security-posture-documentation
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-02
---

# Phase 5 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | pyproject.toml |
| **Quick run command** | `uv run pytest tests/ -x -q` |
| **Full suite command** | `uv run pytest --cov=worthless --cov-report=term-missing` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/ -x -q`
- **After every plan wave:** Run `uv run pytest --cov=worthless --cov-report=term-missing`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 05-01-01 | 01 | 1 | DOCS-01 | content validation | `grep -c "## " SECURITY_POSTURE.md` | ❌ W0 | ⬜ pending |
| 05-01-02 | 01 | 1 | DOCS-01 | completeness check | `uv run python -c "import ast; ..."` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] Validation script for SECURITY_POSTURE.md structure (all invariants covered, confidence levels present)
- [ ] Cross-reference check: every SR-XX rule mentioned, every invariant assessed

*Note: This phase is primarily documentation. Validation focuses on completeness and cross-reference accuracy rather than functional tests.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Confidence levels are honest | DOCS-01 | Requires human judgment on accuracy claims | Review each confidence rating against actual test evidence |
| Rust mitigation path is actionable | DOCS-01 | Requires domain judgment | Verify each "PLANNED" item maps to a concrete Rust mechanism |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
