# Adversarial review — release.sh spec (open findings)

**Source:** independent security-engineer pass, 2026-05-30
**Against:** SPEC.md + security-engineer.md + deployment-engineer.md
**PR:** https://github.com/shacharm2/worthless/pull/252
**Status legend:** ⬜ open · 🟡 fixup-pending · ✅ fixed (with commit SHA)

> Crash-survival surface: this file IS the work-tracking surface for the 14 findings while bd is offline. Cross off entries as fixup commits land. Source of truth for fixup #3 / #4 scope.

## Already addressed in fixup #2 (commit `b99d933`)

| Finding | Status | Where |
|---|---|---|
| Self-found Fix 1 — TOCTOU between 4.2 and 4.2b | ✅ b99d933 | R-10 + SPEC §4 (pinned wheel) |
| Self-found Fix 2 — `gh auth status --show-token` leaks token | ✅ b99d933 | §5 + R-9 |
| Self-found Fix 3 — tag-cut has no expected-commit assertion | ✅ b99d933 | new R-19 + SPEC §3 (P1.5 capture + assert) |
| Self-found Fix 4 — docker step uses fresh `pip install` not verified wheel | ✅ b99d933 | SPEC §4 step 4.4 (mount verified wheel) |
| Self-found Fix 5 — R-14 chicken-and-egg on first commit | ✅ b99d933 | R-14 + §9 bootstrap clause |

## Open — adversarial reviewer findings (14)

### CRITICAL

| ID | Status | Threat (ELI5) | Fix sketch | Amends |
|---|---|---|---|---|
| **F-1** | ✅ fixup #3 | Compromised `gh`/`gpg`/`docker`/`pip` binary on `$PATH` returns "verified" without doing crypto — entire chain is theatre | Preflight P11 added (§2), R-20 added (security-engineer.md), SPEC §11 Tool Trust section added with 9-binary pin table + refresh policy + chicken-and-egg note, §8 gained TDD negative-test row "fake gh on PATH must abort at P11 before any verify call" | R-14+, P11, R-20, §11 |

### HIGH (6)

| ID | Status | Threat | Fix sketch | Amends |
|---|---|---|---|---|
| **F-2** | ✅ fixup #4 | Compromised `gpg-agent` or `GPG_AGENT_INFO` env redirect signs without prompting | Assert socket under `~/.gnupg/`, refuse env override, pin `gpg --version` hash | R-7 |
| **F-3** | ✅ fixup #4 | MITM forges `gh api /rulesets` response showing `active` while actually disabled | Second-channel check (TLS SPKI pin / canary ref ls-remote) | P9, R-4 |
| **F-4** | ✅ fixup #4 | `kill -9 $WATCHDOG_PID` (not parent) silently disarms 120s failsafe | Parent polls `kill -0 $WATCHDOG_PID`, aborts on watchdog death | R-3 |
| **F-7** | ✅ fixup #4 | R5→R6 race: `gh api PUT` returns 200 before edge propagation; R6 reads cached `active` | R6 uses `Cache-Control: no-cache` × 3 polls 2s apart, all must return `active` | R-3 R6 |
| **F-12** | ✅ fixup #4 | Shell alias or zsh completion auto-injects `--allow-ruleset-disable` | Require parallel `WORTHLESS_ALLOW_RULESET_DISABLE=1` env var; refuse if PPID is completion subprocess | R-3 |
| **F-14** | ✅ fixup #4 | `.release-audit/YYYY-MM-DD.log` plain-write — local tamper or buggy rerun corrupts forensics | `chflags uappnd` (mac) / `chattr +a` (Linux) at file creation; GPG-sign daily log; `--verify-audit-log` doctor flag | R-15 |

### MEDIUM (7)

| ID | Status | Threat | Fix sketch | Amends |
|---|---|---|---|---|
| **F-5** | ✅ fixup #4 | Laptop suspend mid-recovery freezes `sleep 120`; ruleset stays disabled for hours | Wall-clock deadline loop (`date +%s` comparison), not single `sleep 120` | R-3 |
| **F-6** | ✅ fixup #4 | `SIGPIPE` (terminal close mid-pipe) + `SIGQUIT` not trapped | Add `PIPE QUIT USR1 USR2` to trap list; explicit `trap '' PIPE` early | R-3 |
| **F-8** | ✅ fixup #4 | GH Release (4.6) created BEFORE CHANGELOG-date PR (4.8) → release body says `TBD` forever | Date-stamp in-memory notes before 4.6, OR reorder 4.8 before 4.6 | §4 step 4.6/4.8 ordering |
| **F-9** | ✅ fixup #4 | "exactly `repo`" branch of R-9 can't reach step 4.7 — `gh workflow run` needs `workflow` scope | R-9 mandates `repo,workflow` unconditionally; preflight P8 positively asserts both | R-9 / P8 |
| **F-10** | ✅ fixup #4 | Docker step 4.4 fresh `pip install` downloads DIFFERENT wheel than 4.2 verified — defeats attestation | Mount the already-verified wheel from 4.2 into the container (overlaps with fix-4 above; deepens it) | §4 step 4.4 |
| **F-11** | ✅ fixup #4 | `.release-state/` + `.release-audit/` not actually in `.gitignore` — markers leak via `git add -A` | P1 asserts `.gitignore` contains `.release-state/` and `.release-audit/` lines | P1 + R-6 |
| **F-13** | ✅ fixup #4 | Tag message can inject markdown into Linear paste (`[click](evil)` lands in release comment) | 4.9 emitter HTML-escapes; R-16 regex forbids `[ ] ( ) < >` outside prefix | R-16 / 4.9 |

## Open — 4-lens security panel findings (F-15..F-30, 2026-06-01)

**See `SECURITY-PANEL.md`** for full synthesis + per-lens overlap matrix + morning-options. **Diminishing-returns test: FAILED** (88% of findings distinct across 4 lenses).

| ID | Sev | Source | Headline | Status |
|---|---|---|---|---|
| F-15 | CRIT | brutus | R-21 canary tests wrong ruleset (`refs/canary/*` doesn't trip `v-tags-signed`) | ⬜ |
| F-16 | CRIT | IR | Audit log missing 8 witness fields (gpg-agent socket + tool SHA per call most damaging) | ⬜ |
| F-17 | CRIT | IR | Audit log local-only — zero offsite copy survives compromise | ⬜ |
| F-18 | CRIT | IR | Mid-window compromise has no recovery — IR can't know which tag mid-push | ⬜ |
| F-19 | HIGH | brutus | R-10 attestation chains to ref name, not GPG signature on tag | ⬜ |
| F-20 | HIGH | brutus | §11 Tool Trust ignores LD_PRELOAD / DYLD_INSERT_LIBRARIES | ⬜ |
| F-21 | HIGH | pentester | Watchdog re-arm race (parent never observes heartbeat death between fork and R3) | ⬜ |
| F-22 | HIGH | pentester | jq + grep-of-MITM-stderr bypass of R-24 | ⬜ |
| F-23 | HIGH | IR | Tag message lacks structured provenance trailer | ⬜ |
| F-24 | HIGH | IR | Linear paste is prose; no structured IR sidecar | ⬜ |
| F-25 | MED | brutus | "Offline-capable" claim misleading | ⬜ |
| F-26 | MED | pentester | pip index ordering + multi-wheel docker mount | ⬜ |
| F-27 | MED | pentester | CHANGELOG content can inject sed/awk/markdown at 4.5a | ⬜ |
| F-28 | MED | auditor | n=1 signer / no segregation of duties (SOC 2 CC8.1 blocker) | ⬜ |
| F-29 | MED | auditor | No SBOM / CVE scan gate before tag-push | ⬜ |
| F-30 | LOW | auditor | Tool Trust pin refresh lacks upstream-signed-checksum citation | ⬜ |

**Candidate new rules:** R-26 witnessed audit schema · R-27 offsite log shipping · R-28 tag provenance trailer · R-29 pre-window beacon · R-30 IR sidecar · R-31 co-signer · R-32 pin sourcing. **New gates:** P12 SBOM/CVE.

**SLSA Build Level 3 achieved** (signed provenance via Trusted Publisher + GHA OIDC + R-10 attestation chains repo+tag). Gap to L4: two-party `main` review + reproducible builds + parameterless `publish.yml`.

**SOC 2 audit-blocker:** CC8.1 change-management has n=1 signer (no segregation of duties). Every external audit sample will fail until R-31 lands.

## Cross-cutting (raised by reviewer)

F-1, F-2, F-9 together: the spec needs an explicit "Tool Trust" section listing **every external binary** the script depends on (`gh`, `gpg`, `docker`, `pip`, `awk`, `jq`, `sha256sum`, `python3`, `curl`) with the pinning strategy per binary. Currently R-14 covers only the 3 release scripts themselves — leaves toolchain as implicit ambient trust. Address as part of F-1 fix (new SPEC §11 will list all binaries).

## Fixup-batch plan

| Batch | Findings | Estimated edits |
|---|---|---|
| **Fixup #3 — CRITICAL** | F-1 only (with §11 Tool Trust) | ~80 lines: SPEC §11 new, P11 in §1/§2, R-20 in security-engineer.md, SR-10 stub note for SECURITY_RULES.md |
| **Fixup #4 — HIGH (6)** | F-2, F-3, F-4, F-7, F-12, F-14 | ~120 lines: tighten R-3 / R-4 / R-7 / R-15; new sub-rules R-21..R-24 as needed |
| **Fixup #5 — MEDIUM (7)** | F-5, F-6, F-8, F-9, F-10, F-11, F-13 | ~80 lines: refine §4 ordering, R-9, R-16, P1, P8; defensive logging changes |

## Working rules

- TDD-style per finding: §8 Test Strategy gets a failing-scenario row BEFORE the rule update lands.
- Each fixup commit message references the F-N IDs it closes.
- This file is updated in the same commit (status ⬜ → ✅ + commit SHA) so the open-set always reflects reality.
- File survives bd-down. When bd is back, can be promoted to 3 beads tracking the 3 fixup batches.
