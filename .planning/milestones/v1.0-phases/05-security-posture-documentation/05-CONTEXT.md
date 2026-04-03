# Phase 5: Security Posture Documentation - Context

**Gathered:** 2026-04-03
**Status:** Ready for planning

<domain>
## Phase Boundary

Deliver SECURITY_POSTURE.md — an honest, auditable self-assessment of what Worthless protects, at what confidence level, and what its known limitations are. Also deliver SECURITY.md (vulnerability disclosure policy). Write structural tests that close enforcement gaps before documenting claims.

This phase is documentation + test gap closure. No new features, no architecture changes.

</domain>

<decisions>
## Implementation Decisions

### Audience & tone
- Two audiences, layered: developers stop after page 1, auditors read everything
- Voice: direct & honest, matching Worthless brand — confident where earned, blunt about limitations
- Brief origin hook (1-2 sentences framing the $82K incident as stakes, not the full story). Must map precisely to the threat model (stolen key → bill runup) or an auditor will use it against us
- No corporate hedging, no marketing language

### Document structure
- **TL;DR** (5 lines, developer stops here) → **Trust boundary diagram** (ASCII, data flow annotations showing what crosses each boundary) → **Confidence summary table** (3 invariants + tiers, auditor gets full picture in 1 page) → **Scope & limitations** → **Per-invariant cards**
- **Table of contents** with anchor links — mandatory for a doc this long
- **Glossary** at top: define Shard A, Shard B, commitment, nonce, gate, enrollment. Outside auditors don't share our vocabulary

### Per-invariant cards (the core)
- Each invariant gets a self-contained card: claim, evidence (cite tests by name), confidence tier, attacker prerequisites (realistic narrative), limitations, Rust mitigation
- Cards ARE the threat model (asset-centric threat analysis). Current: asset-centric analysis via invariant cards. Planned: formal STRIDE analysis during Rust hardening — different depth, not contradictory
- Invariants reference their enforcing SRs inline (e.g., Invariant 1 → SR-01, SR-02, SR-08). No separate SR section

### Confidence scale
- 3 tiers: **Enforced** / **Best-effort** / **Planned**
- Preferred path: write AST-scan tests for SR-07 (constant-time compare) and SR-08 (CSPRNG) during Phase 5, plus Invariant #3 structural test. Then all enforced items are test-enforced and scale stays clean at 3 tiers
- Fallback if tests can't be written: 4 tiers — Enforced (CI) / Enforced (lint) / Best-effort / Planned. Never use asterisk footnotes
- **Confidence tier definitions table** — 3-row table defining what each tier means in terms of test coverage, CI enforcement, and bypass conditions
- **Best-effort hard cap: 3 items max.** More than 3 = architecture needs fixing, not documenting
- Each confidence level cites specific test names as evidence (e.g., `test_invariants::test_gate_before_reconstruct`)
- Each item includes confidence upgrade path: what would change it from Best-effort to Enforced

### Limitations & threat model
- Per-limitation threat cards: what it means, realistic exploit narrative, attacker prerequisites (precise about PoC reality — "code execution in FastAPI process" not "root on proxy host"), risk level, specific Rust mitigation mechanism
- **Non-goals section**: explicit about what Worthless does NOT protect against (compromised client, malicious provider, side-channel timing on Python PoC)
- **Out of scope** for threat model: supply chain attacks, host OS compromise, side-channel timing
- Trust boundary diagram in exec summary with data flow annotations — label what crosses each boundary. Invariant violations should be visually obvious
- **Breach scenario**: what if Shard B DB is compromised? Document: re-enroll all affected keys, no bulk rotation in V1
- **Residual risk summary table** at the end — accepted risks ranked by severity

### Rust mitigation path
- Name exact mechanisms per limitation: zeroize crate (deterministic zeroing), mlock (page pinning), seccomp (syscall filtering), distroless containers (attack surface)
- No dates in the doc — reference ROADMAP.md for timeline. Security doc owns what will change and how, roadmap owns when
- Each limitation card ends with its specific Rust mitigation, not generic "will be fixed in Rust"

### Key lifecycle
- Document lifecycle clearly: enrollment creates Shard B, re-enrollment overwrites it, no bulk rotation in V1
- If revoke command ships: document it. If not: "key rotation requires manual re-enrollment"
- Breach response: re-enroll all affected keys
- File beads issue for revoke command if not already scoped

### Shard B data-at-rest
- Verify actual implementation state before claiming anything
- Document honestly: if Fernet-encrypted, say so with evidence. If not, say so as limitation

### SR reverse-mapping table
- SR-01 through SR-08, each row: which invariant it enforces, which tests verify it, where in code
- Auditor's primary navigation tool — goes after the invariant cards

### Update cadence & ownership
- Header: "Last verified: [date]" + commit SHA or git tag pinning to actual code state
- Phase triggers: any GSD phase touching crypto/proxy/security MUST update the doc
- Ownership: phase executor flags sections as "needs review", security-reviewer agent confirms/updates during review gate
- Brief changelog at bottom: date, what changed, 2-3 lines per update

### Framework mapping
- Light touch: reference OWASP Top 10 categories inline where they naturally fit
- Explicit disclaimer in exec summary: "This document is not a compliance certification. Worthless is not SOC 2 audited. OWASP references show which vulnerability classes the architecture addresses. Full framework mapping ships with enterprise tier."

### Forensic logging
- Claude decides based on existing code: document what IS logged, not aspirational claims
- Check for: enrollment events, gate denials, spend cap triggers
- Add recommendations for gaps if found

### Cryptographic agility
- Check if protocol is versioned (shard schema version field)
- If not versioned: file beads issue — this is a real gap given XOR → Rust → MPC upgrade path. Small code change, big credibility gain
- Doc states versioning status honestly and references MPC upgrade path as why it matters

### Supply chain
- One line: "Dependency auditing: pip-audit runs in CI. Full SBOM and supply chain policy ships with enterprise tier."

### License
- Brief implications paragraph (2-3 sentences) for enterprise evaluators assessing adoption

### SECURITY.md (disclosure policy)
- Separate file at repo root (GitHub standard, surfaces in Security tab)
- Contents: responsible disclosure timeline (90-day), contact method, response SLA (48hr ack, 7-day triage), scope definition, safe harbor statement
- SECURITY_POSTURE.md links to it: "To report a vulnerability, see SECURITY.md"

### Claude's Discretion
- Exact ASCII art for trust boundary diagram
- Ordering of limitation cards by severity
- Exact wording of non-goals
- How to present the SR reverse-mapping table (matrix vs list)
- Forensic logging: what to recommend for gaps
- Glossary ordering and detail level

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `tests/test_invariants.py` — existing invariant tests (Inv 1 and 2 have AST enforcement, Inv 3 does not)
- `tests/test_security_properties.py` — security property tests
- `SECURITY_RULES.md` — SR-01 through SR-08 definitions, source material for the doc
- `.pre-commit-config.yaml` — existing enforcement infrastructure

### Established Patterns
- AST-based enforcement tests (existing pattern for Invariants 1, 2) — extend to Invariant 3, SR-07, SR-08
- Security rules have traceability table format — doc can mirror this structure

### Integration Points
- SECURITY_POSTURE.md at repo root (new file)
- SECURITY.md at repo root (new file)
- tests/test_invariants.py (extend with Invariant 3 structural test)
- tests/test_security_properties.py (extend with SR-07, SR-08 AST tests)

### Verification prerequisite
- Must verify Shard B encryption-at-rest state before writing claims
- Must verify protocol versioning state before writing crypto agility section
- Must verify what's currently logged before writing forensic logging section

</code_context>

<specifics>
## Specific Ideas

- Per-invariant cards as self-contained units — auditor reviews one at a time
- Trust boundary diagram must label data crossing each boundary: "Shard B + commitment + nonce" client→server, "reconstructed key" never crossing reconstruction→proxy
- Brutus stress-test findings integrated: Inv #3 test gap, precise attacker prerequisites, tier splitting, $82K story mapping, best-effort ceiling
- "Stolen? So what." brand voice carries into the doc — this isn't a defensive document, it's a confident one that earns trust through honesty

</specifics>

<deferred>
## Deferred Ideas

- Full STRIDE analysis and attack trees — Rust hardening phase
- Full framework compliance mapping (SOC 2, CIS) — enterprise tier
- Full SBOM and supply chain policy — enterprise tier
- Bulk key rotation tooling — future release
- Automated rotation — V2

</deferred>

---

*Phase: 05-security-posture-documentation*
*Context gathered: 2026-04-03*
