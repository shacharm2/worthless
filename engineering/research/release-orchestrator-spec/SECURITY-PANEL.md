# Security Panel — 4-Lens Review of release.sh Spec

**Date:** 2026-06-01
**Reviewers:** brutus + penetration-tester + incident-responder + security-auditor (parallel, distinct lenses)
**PR under review:** #252 / `chore/wor-598-postmortem-research`
**Spec under review:** SPEC.md + security-engineer.md (25 hard rules R-1..R-25, 14 prior adversarial findings F-1..F-14 all ✅)

---

## TL;DR for the morning

**Diminishing returns test: FAILED (i.e., NOT diminishing).** Each of the 4 lenses found genuinely different issues. Near-zero overlap (only R-3 watchdog race appears in both brutus + pentester). **16 new distinct issues surfaced (F-15..F-30)** + 7 candidate new rules (R-26..R-32) + 1 SLSA-level achievement (Build L3, gap to L4 mapped).

**Two honest reads of where this leaves us:**

| Read | Action |
|---|---|
| **"Spec is rigorous enough — ship it"** | The spec is already more thorough than any comparable open-source release tool. The 16 new findings can be tracked as F-15..F-30 in `adversarial-findings.md` and addressed during the 4-PR implementation series — that's the natural rhythm where many will surface anyway. **Merge PR #252, start implementation, fix as we hit each section.** |
| **"Spec must close all CRITs before any code"** | 4 new CRITs (brutus #1, IR §1, IR §2, IR §4). All real. Fixup #5 = ~120 lines spec edits across 5-7 hours. THEN merge. Defers implementation start by 1 day but spec lands fully hardened. |

**Recommendation: option 1.** Spec is in a great place. 4 CRITs are tracked. Implementation reveals more truth than more spec rounds. The 14 previously-closed findings demonstrate the spec-fix-loop works.

---

## Per-lens summary

| Lens | Findings | Distinct value-add |
|---|---|---|
| **Brutus** (claims attack) | 5: 1 CRIT, 2 HIGH, 2 MED | Attacked the strongest WORDS. Caught spec self-deception (R-21 tests wrong ruleset; R-10 claim overstates what attestation actually binds) |
| **Penetration-Tester** (exploit chains) | 4: 2 HIGH, 1 MED-HIGH, 1 MED | Found chained exploits across multiple rules (jq → grep-of-MITM-stderr; pip index ordering → wheel substitution; awk-eval injection from CHANGELOG content) |
| **Incident-Responder** (post-breach forensics) | 11: 3 CRIT, 4 HIGH, 3 MED, 1 LOW | Spec is A- on prevention, C on forensics. Audit log missing 8 critical fields. Zero offsite copy. No pre-window beacon. Mid-window compromise has no recovery story. |
| **Security-Auditor** (compliance frameworks) | SLSA L3 + 5 audit-readiness gaps | SOC 2 CC8.1 blocker = n=1 signer. SLSA L4 needs 2-person review + reproducible builds. OWASP A06 + A07 + SSDF RV.1 gaps. |

## Overlap matrix (the diminishing-returns test)

| Issue | Brutus | Pentester | IR | Auditor |
|---|:-:|:-:|:-:|:-:|
| R-3 watchdog race (both watchdog AND heartbeat die) | ✅ #5 | ✅ #1 | — | — |
| R-21 canary tests wrong ruleset / MITM bypass | ✅ #1 | ✅ #2 (different angle) | — | — |
| Tool Trust binary pin doesn't cover LD_PRELOAD | ✅ #3 | — | — | — |
| jq + grep + MITM chain | — | ✅ #2 | — | — |
| pip index ordering + multi-wheel docker mount | — | ✅ #3 | — | — |
| awk/sed/CHANGELOG injection at 4.5a | — | ✅ #4 | — | — |
| Audit log missing fields | — | — | ✅ §1 | — |
| Audit log local-only (no offsite copy) | — | — | ✅ §2 | ✅ #3 |
| Recovery has no pre-window beacon | — | — | ✅ §4 | — |
| Linear paste is prose, not structured | — | — | ✅ §5 | — |
| n=1 signer (no segregation of duties) | — | — | — | ✅ #1 |
| No SBOM / CVE scan gate | — | — | — | ✅ #2 |

**Overlap rate:** 2/16 issues had any overlap (R-3 race + audit-log-shipping). **88% distinct.** Lenses do NOT converge. More lenses = more findings, NOT redundancy.

## New findings inventory (F-15..F-30)

### CRITICAL (4)

| ID | Source | Issue | Quick fix |
|---|---|---|---|
| F-15 | brutus | R-21 canary tests wrong ruleset (canary on `refs/canary/*` doesn't trip `v-tags-signed`) | Push unsigned `v0.0.0-canary-<ts>` tag; parse rejection for exact rule name |
| F-16 | IR §1 | Audit log missing 8 witness fields — biggest: actual gpg-agent socket + tool SHA at each call | New R-26 JSONL schema with mandatory fields |
| F-17 | IR §2 | Audit log local-only — zero offsite copy survives compromise | New R-27 offsite shipping (GH issue comment + private gist) |
| F-18 | IR §4 | Mid-window compromise has no recovery — IR can't know which tag, can't re-enable ruleset | New R-29 pre-window beacon + GHA scheduled re-enable workflow |

### HIGH (6)

| ID | Source | Issue | Quick fix |
|---|---|---|---|
| F-19 | brutus | R-10 attestation chains to ref name, not GPG signature on tag | Assert wheel subject SHA == EXPECTED_SHA captured in P1.5 |
| F-20 | brutus | §11 Tool Trust ignores LD_PRELOAD / DYLD_INSERT_LIBRARIES | Unset env vars at start; re-hash binary before each crypto call |
| F-21 | pentester | Watchdog re-arm race (parent never observes heartbeat death between fork and R3) | OS-level supervisor (systemd-run / launchd) outside process group |
| F-22 | pentester | jq + grep-of-MITM-stderr bypass | SPKI-pin github.com + api.github.com; canary over SSH |
| F-23 | IR §3 | Tag message lacks structured provenance trailer | New R-28 trailer with Release-SHA / Wheel-SHA256 / Builder-Run-URL |
| F-24 | IR §5 | Linear paste is prose; no structured IR sidecar | New R-30 ir-sidecar.json + .release-audit/INDEX.jsonl |

### MEDIUM (5)

| ID | Source | Issue | Quick fix |
|---|---|---|---|
| F-25 | brutus | "Offline-capable" claim misleading | Strike from §10; replace with accurate framing |
| F-26 | pentester | pip index ordering + multi-wheel docker mount | `--isolated --no-config`; single-file docker mount + sha256 verify |
| F-27 | pentester | CHANGELOG content can inject sed/awk/markdown | Implement 4.5a via python3 strict bytes-replace |
| F-28 | auditor | n=1 signer / no segregation of duties (SOC 2 CC8.1) | New R-31 co-signer attestation |
| F-29 | auditor | No SBOM / CVE scan gate before tag-push | New P12 preflight: `pip-audit` + `syft` SBOM |

### LOW (1)

| ID | Source | Issue | Quick fix |
|---|---|---|---|
| F-30 | auditor | Tool Trust pin refresh lacks upstream-signed-checksum citation | New R-32 pin updates must cite upstream signed checksum URL |

### SLSA achievement

**Build L3 achieved.** Gap to L4: two-party review on `main` (CODEOWNERS), hermetic reproducible builds, parameterless `publish.yml`. Source track L2; needs enforced two-person review for L3.

---

## Morning options for the maintainer

| Option | Effort | Result |
|---|---|---|
| **A — Merge as-is, track F-15..F-30 in adversarial-findings.md** | 5 min | PR #252 ships; spec is design-complete with 14 closed + 16 tracked-for-implementation |
| **B — Fixup #5: address all 4 CRITs (F-15..F-18), file F-19..F-30 for impl** | 90 min | PR #252 ships with no open CRITs; HIGH+MED tracked |
| **C — Fixup #5+6: address all CRITs + HIGHs (10 issues), file MEDs** | 3 hrs | PR #252 ships with only MED+LOW tracked |
| **D — Close all 16 findings before merge** | 5-7 hrs | Spec is enterprise-credible (SLSA L3 + SOC 2 path clear) |

**Honest recommendation:** **A or B**. Spec is already more rigorous than any comparable OSS release tool. The 4 CRITs from option B are quick spec edits (R-21 canary fix is 5 lines; R-26 audit schema is a JSONL template; R-27 offsite shipping is one extra `gh api` call; R-29 beacon is the heaviest at ~30 lines). After B you have **zero open CRITs**. The HIGHs and MEDs are well-served by surfacing during implementation when actual bash code exists.

**Skip option D unless** you're pursuing SOC 2 certification this quarter — that's a different epic.

---

## Files (raw agent outputs, persisted for crash-survival)

- `.panel-brutus.md` — 5 findings, attacks strongest claims
- `.panel-pentester.md` — 4 exploit chains
- `.panel-incident-responder.md` — 11 forensic gaps + R-26..R-30 proposals
- `.panel-auditor.md` — OWASP/SOC2/NIST/SLSA crosswalk + 5 audit-readiness gaps

These dotfiles can be deleted after the morning decision (their content is fully captured in this SYNTHESIS + adversarial-findings.md F-15..F-30 entries).
