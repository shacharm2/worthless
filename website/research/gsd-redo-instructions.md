# GSD v2.0 Milestone Redo — Step-by-Step Playbook

**Purpose:** Redo `/gsd:new-milestone` from scratch, correcting the "Fernet eliminated" error from the previous run. Light mode (XOR + Fernet) is permanent. v2.0 adds secure mode alongside it.

**Previous run reference:** `docs/research/prev-gsd.md` (4 commits: b079d1a, 69f465b, 28e803f, a0b73d0 on `gsd/v2.0-harden`)

---

## Prerequisites

1. **Planning files on main.** The previous branch `gsd/v2.0-harden` must NOT be merged. If it was, revert it. GSD starts from whatever `.planning/` state is on `main`.

2. **Research docs available locally** in `docs/research/` (gitignored, not on the branch — GSD reads them from the working directory):
   - `fernet-key-bootstrap-problem.md` — **THE KEY INPUT** (Section 0: coexistence table)
   - `spec-analysis/implementation-plan.md` — master build plan (6 phases)
   - `spec-analysis/sidecar-architecture-spec.md` — sidecar spec
   - `spec-analysis/spec-addendum.md` — 7 amendments
   - `spec-analysis/spec-codebase-impact.md` — file-by-file impact
   - `spec-analysis/spec-vs-research-gaps.md` — gap analysis
   - `spec-analysis/ticket-mapping.md` — 71 tickets classified
   - `spec-analysis/linear-review-findings.md` — Karen review findings
   - `shamir-sidecar-architecture.md` — Claude deep research proposal
   - `shamir-sidecar-security-review.md` — security review
   - `shamir-sidecar-verification.md` — crypto claim verification
   - `cross-platform-shard-storage/SYNTHESIS.md` — platform decision matrix

---

## Step 1: Start a Fresh Claude Code Session

```
/clear
```

Start clean. No leftover context from previous attempts.

---

## Step 2: Prime the Session with the Coexistence Principle

Before running GSD, paste this verbatim so it's in context:

> **ARCHITECTURAL CONSTRAINT — read before generating any planning files:**
>
> Light mode (XOR + Fernet, single process) is PERMANENT. It is never removed, replaced, or deprecated. v2.0 adds "secure mode" (Shamir + Rust sidecar) alongside it. The two modes coexist forever.
>
> | | Light Mode | Secure Mode |
> |---|---|---|
> | Split primitive | XOR (kept forever) | XOR or Shamir (user's choice) |
> | Encryption at rest | Fernet | Not needed (process isolation) |
> | Architecture | Single process | Sidecar makes upstream calls |
> | Start command | `worthless up` | `worthless up --secure` or Docker |
> | User | Vibe coder, local dev | Production, Docker, cloud |
>
> This means:
> - PY-10 must NOT say "cryptography dependency removed" — Fernet stays for light mode
> - Phase 4D in implementation-plan.md says "Remove Fernet code paths" — WRONG, they stay
> - No requirement should say "Fernet eliminated entirely"
> - Migration is FROM Fernet TO Shamir for users who CHOOSE secure mode — not forced
> - `cryptography` dependency stays in the project
> - Light mode tests must keep passing unchanged
>
> Source: `docs/research/fernet-key-bootstrap-problem.md` Section 0

---

## Step 3: Run `/gsd:new-milestone`

GSD will ask questions. Here are the answers:

### Q: What is the new milestone?
> **v2.0 — Harden.** Add secure mode (Shamir 2-of-3 + Rust sidecar) alongside the existing light mode (XOR + Fernet). Light mode stays permanent and unchanged. Secure mode is additive.

### Q: What are the high-level goals / scope?
> 1. Shamir 2-of-3 secret sharing (GF(256), Rust implementation)
> 2. Platform credential store backends (macOS Keychain, Windows Credential Manager, Linux kernel keyring, Docker secrets, encrypted file fallback)
> 3. Rust sidecar binary (IPC over Unix socket, vault mode + proxy mode with SSE streaming)
> 4. Sidecar OS-level hardening (seccomp-BPF, Landlock)
> 5. Python layer rewired to use sidecar in secure mode (light mode untouched)
> 6. Migration tool for users who want to move from light mode to secure mode (optional, not forced)
> 7. Distribution via maturin wheels + Docker multi-container
> 8. Security documentation and dependency auditing
>
> CRITICAL: Light mode (XOR + Fernet) is permanent and untouched. Fernet is NOT eliminated. The `cryptography` dependency stays. Secure mode is additive.

### Q: Which categories to include? (if asked)
> All of them. Crypto Core, Sidecar, Shard Store, Python Layer, Migration, Distribution, Docker, Hardening, Performance.

### Q: Research input files? (if asked)
> Point to these files (in order of importance):
> 1. `docs/research/fernet-key-bootstrap-problem.md` — coexistence constraint (Section 0)
> 2. `docs/research/spec-analysis/implementation-plan.md` — master plan (but has errors, see Step 4)
> 3. `docs/research/spec-analysis/sidecar-architecture-spec.md` — sidecar design
> 4. `docs/research/spec-analysis/spec-addendum.md` — 7 amendments
> 5. `docs/research/spec-analysis/spec-codebase-impact.md` — file impact analysis
> 6. `docs/research/cross-platform-shard-storage/SYNTHESIS.md` — platform backends

### Q: Karen and Brutus review? (if asked)
> YES — run both. The previous run's Karen+Brutus findings are still valid (12 added requirements, Phase 11 split into 11A/11B, DOCK-05 deferred). Make sure their findings get incorporated.

---

## Step 4: Watch for These Specific Errors

The previous run produced these mistakes. If you see ANY of them in the generated output, stop and correct immediately:

### CRITICAL — Fernet Elimination Language (10 known locations)

1. **implementation-plan.md line 11 (Goal):** Says "Fernet eliminated entirely" — MUST say "Secure mode added alongside permanent light mode"
2. **implementation-plan.md Phase 4D:** Says "Remove Fernet code paths" — MUST say "Add secure mode code paths alongside existing Fernet paths"
3. **implementation-plan.md Phase 4 Removed section (lines 206-210):** Lists removing `cryptography`, Fernet key generation, fernet.key, Fernet encrypt/decrypt — ALL WRONG. These stay for light mode.
4. **implementation-plan.md Phase 6 (line 253):** Says "Fernet eliminated" — MUST say "Shamir + sidecar added as secure mode"
5. **implementation-plan.md line 298:** Says "Archive (obsoleted by Fernet elimination)" — Fernet is NOT obsoleted
6. **PY-10 in REQUIREMENTS.md:** Previously said "`cryptography` dependency removed (Fernet eliminated)" — MUST say something like "`cryptography` dependency retained for light mode; secure mode uses Shamir via Rust"
7. **ticket-mapping.md line 48 (WOR-135):** Says "Fernet eliminated" — wrong
8. **ticket-mapping.md line 189 (WOR-142):** Says "Fernet eliminated" — wrong
9. **ticket-mapping.md line 191 (WOR-144):** Says "Fernet eliminated" — wrong
10. **spec-codebase-impact.md lines 54, 140:** Says Fernet eliminated from repository.py and cryptography dep — wrong for light mode

### HIGH — Structural Issues

11. **Phase 11 must be split into 11A (Python Proxy Rewire) + 11B (Migration)** — previous Karen/Brutus finding, 16 reqs in one phase is too many
12. **DOCK-05 (K8s CSI) must be deferred to v2.1** — previous Brutus finding
13. **Missing requirements from Karen review:** PY-11 through PY-15, MIG-07, HARD-08 through HARD-10
14. **Missing requirements from Brutus review:** SIDE-07 (SSE streaming), PERF-01 (<50ms p99), PERF-02 (10 concurrent streams), DIST-06 (fallback binary)

### MEDIUM — Tone/Framing

15. **Migration must be OPTIONAL**, not forced — users who want light mode keep it forever
16. **"Replace XOR+Fernet" framing is wrong** — it's "Add Shamir+Sidecar as secure mode"
17. **All references to "eliminating" or "removing" Fernet must become "adding secure mode alongside"**

---

## Step 5: After GSD Finishes — Verify

Run these checks on the generated files before committing:

```bash
# Check for any "Fernet eliminated" language in planning files
grep -ri "fernet.*eliminat\|eliminat.*fernet\|remove.*fernet\|fernet.*remov" .planning/

# Check PY-10 specifically
grep "PY-10" .planning/REQUIREMENTS.md

# Check that Phase 11 is split
grep "Phase 11" .planning/ROADMAP.md

# Check requirement count (should be ~63)
grep -c "^\- \[ \]" .planning/REQUIREMENTS.md

# Check DOCK-05 is deferred
grep "DOCK-05" .planning/REQUIREMENTS.md
```

If any of these show problems, fix them before committing.

---

## Step 6: Apply Karen + Brutus Fixes

If GSD didn't automatically incorporate the Karen+Brutus findings from the previous run, apply them manually. The previous session added:

**Added requirements (12):**
- SIDE-07 (SSE streaming explicit)
- PERF-01 (<50ms p99 latency), PERF-02 (10 concurrent streams)
- PY-11 (wrap preserved), PY-12 (adapter compat), PY-13 (MCP), PY-14 (revoke), PY-15 (httpx cleanup)
- MIG-07 (.env compat)
- DIST-06 (fallback binary distribution)
- HARD-08 (pre-commit hooks), HARD-09 (green tests gate), HARD-10 (90s install)

**Structural changes:**
- Phase 11 split into 11A (proxy rewire, 15 reqs) + 11B (migration, 7 reqs)
- DOCK-05 (K8s CSI) deferred to v2.1
- Execution order: Phase 6 and 7 parallel, then 8 -> 9 -> (10 || 11A) -> 11B -> 12 -> 13

---

## Step 7: Commit and Push

The previous run produced 4 commits on `gsd/v2.0-harden`:
```
b079d1a  docs: start milestone v2.0 Harden
69f465b  docs: define milestone v2.0 requirements
28e803f  docs: create milestone v2.0 roadmap (8 phases)
a0b73d0  docs: apply Karen+Brutus review — add 12 reqs, split Phase 11, defer DOCK-05
```

GSD should handle commits automatically. After it finishes:

```bash
git push -u origin gsd/v2.0-harden
```

Then `/clear` and proceed with `/gsd:discuss-phase 6`.

---

## Summary: What Changed from Previous Run

| Aspect | Previous Run (WRONG) | This Run (CORRECT) |
|--------|---------------------|---------------------|
| Framing | "Replace XOR+Fernet with Shamir" | "Add Shamir+Sidecar as secure mode" |
| Fernet | "Eliminated entirely" | Permanent (light mode) |
| `cryptography` dep | Removed | Retained |
| PY-10 | "cryptography dependency removed" | "cryptography retained for light mode" |
| Migration | Forced upgrade path | Optional — user chooses secure mode |
| Light mode | Implicitly deprecated | Explicitly permanent and supported |
| Phase 4D | "Remove Fernet code paths" | "Add secure mode paths alongside" |
| Goal statement | "Fernet eliminated entirely" | "Secure mode added alongside permanent light mode" |

---

*Written: 2026-04-06*
*Source: prev-gsd.md transcript + fernet-key-bootstrap-problem.md Section 0*
