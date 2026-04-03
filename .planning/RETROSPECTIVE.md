# Project Retrospective

*A living document updated after each milestone. Lessons feed forward into future planning.*

## Milestone: v1.0 — MVP

**Shipped:** 2026-04-03
**Phases:** 8 | **Plans:** 22

### What Was Built
- XOR split-key crypto with HMAC commitment, tamper detection, and memory zeroing
- Encrypted shard storage (Fernet at rest, async CRUD, SQLite)
- Gate-before-reconstruct proxy with transparent OpenAI/Anthropic routing and SSE streaming
- Full CLI: lock, unlock, scan, status, wrap, up — 90-second setup
- 5-tier CI pipeline with coverage gates and mutation testing
- Security posture documentation with evidence-backed confidence tiers

### What Worked
- Bottom-up build order (crypto → adapters → proxy → CLI → docs) meant each layer was solid before the next built on it
- TDD discipline from Phase 1 caught regressions early — adapter bytearray migration had zero test failures
- Inserted decimal phases (03.1, 04.1, 04.2) let us harden without derailing the roadmap sequence
- Security rules (SECURITY_RULES.md) as a living checklist caught issues at review time, not production time
- Multi-agent review rotation caught blind spots — security reviewer flagged repr leaks, code reviewer caught dead code

### What Was Inefficient
- Early phases (01, 02) shipped without VERIFICATION.md — created bookkeeping debt that persisted to milestone end
- README was rewritten twice (Phase 4 then Phase 04.1) because initial docs assumed terminology that changed
- Header rename (x-worthless-alias → x-worthless-key) touched 47 occurrences — could have been caught earlier if naming was locked before Phase 3
- Traceability checkboxes in REQUIREMENTS.md drifted because GSD tooling only updates them with VERIFICATION.md present

### Patterns Established
- lock/unlock as user-facing terminology (not enroll/revoke)
- Evidence-backed security posture tiers: Enforced / Best-effort / Planned
- Frozen dataclasses for protocol-layer types (no Pydantic overhead)
- Gate-before-reconstruct as testable pipeline (fetch_encrypted → rules → decrypt_shard → reconstruct)
- Fail-closed pattern: any DB error in spend cap → 402 denial
- 5-tier CI: push (fast) → PR (coverage) → scheduled (mutation) → pre-release (audit) → manual (benchmarks)

### Key Lessons
1. Lock terminology early. Naming changes after 3+ phases creates cascading rework across docs, tests, and code.
2. Run VERIFICATION.md for every phase, even early ones. The bookkeeping debt compounds — checkboxes drift, audit flags false positives.
3. Decimal phases work well for urgent insertions, but plan for at least one hardening phase per 3 feature phases.
4. Security posture documentation should happen alongside implementation, not as a final phase — evidence gathering is easier when the code is fresh in context.
5. The "human verification" items (walkthrough, UX feel) need to be scheduled explicitly — they don't happen organically.

### Cost Observations
- Model mix: ~70% opus, ~25% sonnet, ~5% haiku
- Average plan execution: 8 min
- Total execution time: ~3 hours across 22 plans
- Notable: Parallel phase execution (worktrees) was available but not used — all phases ran sequentially

---

## Cross-Milestone Trends

### Process Evolution

| Milestone | Phases | Plans | Key Change |
|-----------|--------|-------|------------|
| v1.0 | 8 | 22 | Established GSD + Beads + Linear workflow, multi-agent review gates |

### Cumulative Quality

| Milestone | Source LOC | Test Files | CI Tiers |
|-----------|-----------|------------|----------|
| v1.0 | 4,399 | 15+ | 5 |

### Top Lessons (Verified Across Milestones)

1. Lock terminology and naming conventions before Phase 3 of any milestone
2. Every phase gets a VERIFICATION.md — no exceptions, even for "obvious" phases
