# Phase 5: Security Posture Documentation - Research

**Researched:** 2026-04-02
**Domain:** Security documentation, threat modeling, cryptographic confidence assessment
**Confidence:** HIGH

## Summary

Phase 5 is a documentation-only phase. The deliverable is a single SECURITY_POSTURE.md file that honestly states what Worthless protects, at what confidence level, and what its known limitations are. This is not a security audit -- it is a self-assessment that gives users and auditors an accurate picture of the Python PoC's security guarantees.

The codebase already has strong security infrastructure: 8 security rules (SR-01 through SR-08), 3 architectural invariants enforced by automated tests, Hypothesis property tests for crypto primitives, and AST-based invariant enforcement. The task is to synthesize this into a readable, auditable document.

**Primary recommendation:** Use the CNCF TAG Security self-assessment structure adapted for a cryptographic project, with FIPS 140-inspired confidence levels (not FIPS compliance, just the tiered confidence model). The document should be honest about Python PoC limitations and explicit about the Rust hardening roadmap.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| DOCS-01 | SECURITY_POSTURE.md with protection status, confidence levels, known limitations | Entire research document -- structure, confidence scale, limitation taxonomy |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Markdown | N/A | Document format | Human-readable, renders on GitHub, diffable in PRs |

### Supporting
| Tool | Purpose | When to Use |
|------|---------|-------------|
| Existing test suite (740+ tests) | Evidence for confidence claims | Reference test names as evidence |
| SECURITY_RULES.md | Source of truth for enforcement rules | Cross-reference SR-01 through SR-08 |
| test_invariants.py | Proves architectural invariant enforcement | Cite as automated verification evidence |
| test_security_properties.py | Proves crypto property coverage | Cite as Hypothesis-powered evidence |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Custom confidence scale | FIPS 140 levels | FIPS 140 is for validated modules; we adapt the tiered concept only |
| STRIDE threat model | Invariant-focused model | STRIDE is too broad; our 3 invariants are the right abstraction |
| Full CNCF self-assessment | Adapted subset | Full template has sections irrelevant to a pre-1.0 PoC |

## Architecture Patterns

### Document Structure

```
SECURITY_POSTURE.md
  1. Purpose & Scope
  2. Threat Model (what we defend against, what we don't)
  3. Architectural Invariants (3 invariants, each with status + confidence)
  4. Security Rules Compliance (SR-01 through SR-08, each with status)
  5. Known Limitations (Python PoC specific)
  6. Hardening Roadmap (Rust migration path)
  7. Test Evidence (map claims to test files)
  8. Changelog (track posture changes over time)
```

### Pattern 1: Invariant Assessment Card

**What:** Each architectural invariant gets a structured assessment card with status, confidence level, evidence, and limitations.

**When to use:** For each of the 3 architectural invariants.

**Example:**
```markdown
### Invariant 1: Client-Side Splitting

| Property | Value |
|----------|-------|
| Status | ENFORCED |
| Confidence | HIGH |
| Enforcement | Automated (AST scan + grep scan) |
| Evidence | test_invariants.py::TestSplitKeyNeverServerSide (5 tests) |

**What it means:** The `split_key` function is only imported in client-side
code (cli/, crypto/). Server-side code (proxy/, storage/, adapters/) is
scanned at test time via AST parsing to ensure no import of `split_key`.

**Limitations:**
- Does not catch dynamic imports via string concatenation
- Enforcement is test-time, not compile-time (no Python equivalent of Rust's module visibility)

**Hardening path:** Rust reconstruction service will enforce this at the
process boundary -- split_key will not exist in the reconstruction binary.
```

### Pattern 2: Confidence Level Scale

**What:** A 4-tier confidence scale adapted from FIPS 140's qualitative levels, tailored for a PoC.

| Level | Meaning | Criteria |
|-------|---------|----------|
| HIGH | Enforced by automated tests + code structure | AST/property tests prove the property holds |
| MEDIUM | Implemented correctly, best-effort enforcement | Code follows the rule but enforcement is convention-based |
| LOW | Documented intent, implementation has known gaps | The goal is clear but Python limitations prevent full guarantee |
| PLANNED | Not yet implemented, on the hardening roadmap | Deferred to Rust phase |

**When to use:** Every security claim in the document gets exactly one confidence level.

### Pattern 3: Known Limitation Block

**What:** Honest disclosure of Python PoC limitations with specific technical details.

**Example:**
```markdown
### Limitation: Memory Zeroing is Best-Effort

**Confidence impact:** Reduces SR-02 enforcement from HIGH to MEDIUM

**Technical detail:** Python's garbage collector is non-deterministic.
`bytearray` contents can be zeroed via `buf[:] = bytearray(len(buf))`,
but:
1. Intermediate `bytes` objects created during XOR computation may persist
   in GC-managed memory
2. The interpreter may retain stack/register copies
3. No equivalent to `mlock()` -- memory can be swapped to disk
4. `ctypes.memset` could be used but adds complexity without solving GC issue

**Current mitigation:** All crypto types use `bytearray` (SR-01), explicit
zeroing via `_zero_buf()` (SR-02), `secure_key` context manager ensures
zeroing on both success and exception paths.

**Rust mitigation:** `zeroize` crate + `mlock` + distroless container.
```

### Anti-Patterns to Avoid
- **Overclaiming confidence:** Do NOT claim HIGH confidence for memory safety in Python. The honest answer is MEDIUM (best-effort).
- **Vague limitations:** "Python has GC limitations" is useless. Specify WHICH operations create uncontrolled copies.
- **Missing evidence:** Every claim must cite a test file, code path, or security rule. No unsupported assertions.
- **FIPS/SOC2 language:** Do NOT use compliance language. This is a self-assessment for transparency, not a certification claim.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Threat taxonomy | Custom threat categories | STRIDE/OWASP categories where applicable | Well-understood vocabulary |
| Confidence scale | Ad-hoc "good/bad/meh" | The 4-tier scale defined above (HIGH/MEDIUM/LOW/PLANNED) | Consistent, defensible, maps to evidence |
| Python memory limitations | Original research | Cite CPython source + zeroize-python docs + bugs.python.org#17405 | Well-documented problem, no need to re-derive |
| Invariant evidence | Prose descriptions | Direct test file references with test count | Auditable, machine-verifiable |

**Key insight:** The security posture document is a synthesis of existing evidence, not new analysis. Every claim should be traceable to existing code, tests, or security rules.

## Common Pitfalls

### Pitfall 1: Confidence Level Inflation
**What goes wrong:** Claiming HIGH confidence for properties that depend on Python runtime behavior (memory zeroing, GC timing).
**Why it happens:** The code correctly calls `_zero_buf()` -- the implementation is right, but the guarantee is weak.
**How to avoid:** Separate "implementation correctness" from "guarantee strength." Code can be correct AND the guarantee can be medium-confidence due to runtime limitations.
**Warning signs:** Any claim of HIGH confidence that involves `bytearray` zeroing, GC behavior, or memory layout.

### Pitfall 2: Missing the "What We Don't Protect Against" Section
**What goes wrong:** Document only states what IS protected, creating false impression of completeness.
**Why it happens:** Natural tendency to focus on positives.
**How to avoid:** Explicitly enumerate non-goals: memory forensics, compromised host, side-channel attacks, supply chain attacks on dependencies.
**Warning signs:** No "Non-Goals" or "Out of Scope Threats" section.

### Pitfall 3: Stale Document
**What goes wrong:** Security posture doc written once, never updated as code changes.
**Why it happens:** No process trigger for updates.
**How to avoid:** Add a "Last Reviewed" date and a changelog section. Reference SECURITY_RULES.md (which is already maintained) as the living enforcement document.
**Warning signs:** No date, no changelog, no cross-references to living documents.

### Pitfall 4: Mixing PoC and Production Claims
**What goes wrong:** Document doesn't clearly distinguish "what the Python PoC guarantees" from "what the hardened Rust version will guarantee."
**Why it happens:** Build order (PoC -> Harden -> Attack) means both states exist conceptually.
**How to avoid:** Two columns or clear section separation: "Current (Python PoC)" vs "Planned (Rust Hardening)."
**Warning signs:** Sentences like "Worthless guarantees..." without specifying which version.

### Pitfall 5: Not Citing Specific Tests
**What goes wrong:** Claims like "verified by automated tests" without naming which tests.
**Why it happens:** Laziness or assumption that readers will find them.
**How to avoid:** Every invariant/rule gets a specific test file + class/function reference.
**Warning signs:** No test_* file paths in the evidence sections.

## Code Examples

These are not code to write -- they are document structure examples.

### Architectural Invariant Assessment
```markdown
## Architectural Invariants

### Invariant 1: Client-Side Splitting
| Property | Value |
|----------|-------|
| Status | ENFORCED |
| Confidence | HIGH |
| Enforcement | Automated AST scan at test time |
| Test evidence | tests/test_invariants.py::TestSplitKeyNeverServerSide (5 tests) |
| Known gaps | Dynamic imports via string concatenation not caught |

[Narrative explanation...]

### Invariant 2: Gate Before Reconstruction
| Property | Value |
|----------|-------|
| Status | ENFORCED |
| Confidence | HIGH |
| Enforcement | Source-order analysis + Hypothesis property tests |
| Test evidence | tests/test_security_properties.py::TestGateBeforeDecrypt (4 tests) |
| Code path | proxy/app.py L304-323: evaluate() at L305, decrypt_shard() at L323 |

[Narrative explanation...]

### Invariant 3: Server-Side Direct Upstream Call
| Property | Value |
|----------|-------|
| Status | ENFORCED |
| Confidence | HIGH |
| Enforcement | Code structure (secure_key context manager) |
| Test evidence | tests/test_invariants.py::test_proxy_app_uses_secure_key |
| Code path | proxy/app.py L340: secure_key wraps upstream dispatch |

[Narrative explanation...]
```

### Security Rule Compliance Table
```markdown
## Security Rules Compliance

| Rule | Description | Status | Confidence | Evidence |
|------|-------------|--------|------------|----------|
| SR-01 | bytearray for secrets | Enforced | HIGH | types.py uses bytearray; lint rule bans bytes for key fields |
| SR-02 | Explicit zeroing | Enforced | MEDIUM | _zero_buf() called; GC may retain copies (see Limitations) |
| SR-03 | Gate before reconstruct | Enforced | HIGH | AST test proves evaluate() before decrypt_shard() |
| SR-04 | No telemetry on secrets | Enforced | HIGH | __repr__ redaction on SplitResult, StoredShard, EncryptedShard |
| SR-05 | Logging denylist | Enforced | MEDIUM | Gitleaks pre-commit; runtime logging not yet audited |
| SR-06 | Sidecar isolation | Partial | LOW | In-process in Python PoC; true isolation deferred to Rust |
| SR-07 | Constant-time compare | Enforced | HIGH | hmac.compare_digest used exclusively |
| SR-08 | CSPRNG only | Enforced | HIGH | secrets.token_bytes; random module banned via Ruff TID251 |
```

### Known Limitations Section
```markdown
## Known Limitations (Python PoC)

### Memory Safety

| Limitation | Impact | Mitigation | Rust Fix |
|------------|--------|------------|----------|
| GC non-determinism | Zeroed bytearrays may have copies in GC heap | _zero_buf() + secure_key context manager | zeroize crate |
| No mlock | Key material can be swapped to disk | None in Python | mlock() on reconstruction buffer |
| Intermediate bytes objects | XOR computation creates transient bytes | Minimized but unavoidable in CPython | Stack-allocated buffers |
| No compiler barrier | Optimizer may elide zeroing | bytearray slice assignment (not optimized away in CPython) | explicit_bzero / volatile writes |

### Process Isolation

| Limitation | Impact | Mitigation | Rust Fix |
|------------|--------|------------|----------|
| In-process reconstruction | Proxy process has access to reconstructed key | secure_key zeroes immediately after dispatch | Separate distroless container |
| Shared memory space | No hardware isolation between gate and reconstruction | Architectural separation (different modules) | Process boundary + seccomp |
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Ad-hoc security claims | Structured self-assessment (CNCF model) | 2024-2025 | Auditable, consistent format |
| Binary secure/insecure | Tiered confidence levels | Industry trend | Honest about partial guarantees |
| Security through obscurity | Transparent limitation disclosure | Industry norm | Builds trust, invites community review |

**Relevant prior art:**
- CNCF TAG Security self-assessment template (adapted for crypto focus)
- FIPS 140-3 security level concept (4 tiers, adapted for self-assessment)
- Python bugs.python.org#17405 (_Py_memset_s discussion, still open)
- zeroize-python (Rust-backed secure zeroing for Python, validates our Rust roadmap)

## Open Questions

1. **Should SECURITY_POSTURE.md reference specific line numbers in source?**
   - What we know: Line numbers change frequently; test names are more stable
   - What's unclear: Whether auditors prefer line-level precision
   - Recommendation: Reference test files and function names, not line numbers. Add code path descriptions for context.

2. **Should we document dependency supply chain posture?**
   - What we know: pip-audit and bandit are in the CI pipeline; pre-commit hooks run gitleaks
   - What's unclear: Whether Phase 5 scope includes supply chain or just crypto posture
   - Recommendation: Brief section acknowledging CI tooling exists, defer detailed supply chain analysis to a future phase. DOCS-01 focuses on protection status and confidence levels.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.x + hypothesis |
| Config file | pyproject.toml [tool.pytest] |
| Quick run command | `uv run pytest tests/test_invariants.py tests/test_security_properties.py -x` |
| Full suite command | `uv run pytest` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| DOCS-01 | SECURITY_POSTURE.md exists with required sections | smoke | `test -f SECURITY_POSTURE.md` | No -- Wave 0 |
| DOCS-01 | Document references all 3 invariants | smoke | `grep -c "Invariant" SECURITY_POSTURE.md` | No -- Wave 0 |
| DOCS-01 | Confidence levels are from defined scale | manual-only | Human review | N/A |
| DOCS-01 | Known limitations section exists | smoke | `grep -c "Known Limitation" SECURITY_POSTURE.md` | No -- Wave 0 |
| DOCS-01 | Rust mitigation path documented | smoke | `grep -c "Rust" SECURITY_POSTURE.md` | No -- Wave 0 |

### Sampling Rate
- **Per task commit:** `test -f SECURITY_POSTURE.md && grep -q "Invariant" SECURITY_POSTURE.md`
- **Per wave merge:** Quick run command above (existing security tests still pass)
- **Phase gate:** Full suite green + manual document review

### Wave 0 Gaps
- [ ] `tests/test_security_posture.py` -- smoke test that SECURITY_POSTURE.md exists and contains required sections
- [ ] No framework install needed -- existing pytest infrastructure sufficient

## Sources

### Primary (HIGH confidence)
- Existing codebase: src/worthless/crypto/ (splitter.py, types.py) -- direct inspection
- Existing tests: tests/test_invariants.py, tests/test_security_properties.py -- direct inspection
- SECURITY_RULES.md -- project source of truth for enforcement rules
- proxy/app.py gate-before-reconstruct code path (L304-323) -- direct inspection

### Secondary (MEDIUM confidence)
- [CNCF TAG Security self-assessment template](https://github.com/cncf/tag-security/blob/main/community/assessments/guide/self-assessment.md) -- document structure reference
- [FIPS 140-3](https://csrc.nist.gov/pubs/fips/140-3/final) -- confidence level tier concept (adapted, not claimed)
- [Python bugs.python.org#17405](https://bugs.python.org/issue17405) -- _Py_memset_s discussion, confirms Python memory zeroing limitations
- [zeroize-python](https://github.com/radumarias/zeroize-python) -- Rust-backed zeroing validates hardening roadmap approach

### Tertiary (LOW confidence)
- [OpenSSF OSPS Baseline](https://openssf.org/press-release/2025/02/25/openssf-announces-initial-release-of-the-open-source-project-security-baseline/) -- general OSS security baseline (not crypto-specific)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- this is a documentation task; the "stack" is markdown + existing test evidence
- Architecture: HIGH -- document structure is well-understood from CNCF/FIPS prior art
- Pitfalls: HIGH -- common documentation pitfalls are well-known and specific to this codebase's constraints

**Research date:** 2026-04-02
**Valid until:** 2026-05-02 (stable -- documentation patterns don't change rapidly)
