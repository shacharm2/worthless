# Phase 4 CLI — Expert Findings for Discussion

Consolidated expert opinions gathered 2026-03-25 to inform `/gsd:discuss-phase 4`.

## 1. Architect: Key Lock Mechanism

**Placement:** `lock` is the keystone of Phase 4 CLI — the orchestrator that finds the key, calls enroll, rewrites `.env`, and confirms. CLI subcommand composing existing primitives, not new crypto.

**Shards:** 2 (XOR), not 3. Matches single-proxy threat model. 3-of-3 (Shamir) is a v2 team/escrow concern.

**Key deletion:** Soft-delete with time-bombed hard-delete:
1. Split key, store shards, rewrite `.env` with proxy URL
2. Move original key to `~/.worthless/vault/<key-id>.enc` (Fernet-encrypted)
3. Verify round-trip works (reconstruct, call provider health endpoint)
4. After N successful requests OR explicit `worthless purge`, hard-delete vault copy
5. `worthless unlock` reverses the process before purge

**Atomic rollback:** If any step fails mid-lock, `.env` stays untouched. No partial states.

**`scan` independence:** Separate command, different lifecycle. Shares key-detection regex with `lock`. `scan` output should suggest `lock` when it finds unprotected keys.

**Critical tests:**
- Atomic rollback on failure
- Vault encryption round-trip
- `.env` rewrite preserves formatting (comments, ordering, other vars)
- Key not in any log during lock (SR-04/SR-05)
- `lock` then `unlock` round-trip
- `lock` on already-locked key (idempotent or clear error)
- Transition window verification (proxy health check after split)
- `purge` zeros vault file (overwrite then delete)

## 2. Security Reviewer: Threat Model

Full threat model at `.planning/security/key-lock-threat-model.md`.

**Critical findings:**

| ID | Threat | Severity |
|----|--------|----------|
| T-1 | `enroll_stub` reads key as `str` — can't be zeroed. Must be `bytearray` at read boundary | High |
| T-2 | Four residue locations: `.env` (SSD wear-leveling), editor swap files, shell history, git history. `scan` must check `git log -p` and reflog | CRITICAL |
| T-3 | Crash between shard storage and `.env` rewrite = key in both forms. Needs WAL/lock-file crash recovery | High |
| T-4 | Recommends no-recovery as default. Re-enrollment with new provider key is the recovery path. Time-limited escrow is v2 | Medium |
| T-7 | Shard A currently plaintext file on disk. Should default to OS keychain; file fallback with 0600 + warnings | High |

**Gap:** The enrollment lifecycle around the solid crypto primitives — the moment between reading a key and destroying it has no crash safety, no memory hygiene at the boundary, and no disk forensics awareness.

## 3. PRD Gap Analysis

**What exists:**
- `enroll_stub.py` — splits key, stores Shard B, writes Shard A to file (test helper only)
- Phase 4 ROADMAP entries for `scan`, `wrap`, `enroll` — zero code
- Typer chosen as CLI framework
- Pre-commit framework (>=4.0) planned for `scan`

**What's missing from PRD:**
- No "lock" command — `enroll` + `wrap` are separate, no atomic lifecycle
- No `.env` file manipulation logic
- No key deletion/rotation mechanism
- No vault/recovery concept
- No secret scanning implementation (just requirements)
- No crash recovery for the transition moment

## 4. Brutus: Honeypot Shard / "Attack the Attacker"

**Verdict: MODIFY — kernel of value, naive implementation is a crypto regression**

**The fatal flaw:** Constraining XOR output to look like `sk-...` leaks key prefix bits. If you fix shard_a's prefix to match the original key's prefix, an attacker who steals shard_a AND knows the scheme can derive `key_prefix = shard_a_prefix`. This converts information-theoretically secure OTP into a partially-leaked split. **Cryptographic regression in a security product.**

**What works instead:**
1. **Cosmetic wrapper, not XOR constraint.** Split normally (full-entropy mask). Encode shard_a as `sk-wtls-<base64(shard_a_bytes)>`. Strip prefix at reconstruction. Zero entropy lost. ~15 lines of code.
2. **Drop canary/honeypot for V1.** You don't control provider auth endpoints. Detection requires provider cooperation that doesn't exist.
3. **If you want canary, build it yourself.** Stand up a Worthless-operated endpoint, embed callback URL in shard metadata. This is Thinkst Canarytoken architecture — separate product feature.

**Priority: Backlog.** Not Phase 4. Ship after core is battle-tested. Revisit when customers ask "what happens when someone steals my shard?"

## 5. User Decision: No Vault (Killed)

**Decision:** No local vault, no soft-delete, no recovery file. Not in dev, not in prod, not behind a flag.

**Why:**
- A dev-only vault means two code paths, two test matrices, flag gating, and vault encryption logic — bloat for a safety net used once a month
- Any local backup of key material is another attack surface (supply chain, disk forensics, SSD wear-leveling)
- The problem it solves barely exists: regenerating a key from the provider dashboard takes 30 seconds
- The atomic `.env` rewrite (write-to-temp + `os.replace()`) is the real safety net — if it fails, the original `.env` is untouched
- Recovery path = provider dashboard. That's it. No Worthless code involved.

## Open Decisions for Discussion

1. `lock` vs keeping `enroll` + `wrap` separate — atomic lifecycle or composable commands?
2. ~~Soft-delete vault~~ → User leans no-recovery. Confirm during discussion.
3. Shard A storage: OS keychain default or file-based with warnings?
4. `scan` scope: working tree only, or also git history?
5. Honeypot shard: backlog or never?
6. Should Phase 4 be split into sub-phases (CLI core, lock lifecycle, scan)?
