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

## 6. Linear Ticket Audit — Bundle Into Phase 4

From parallel terminal audit of all 20 WOR-* tickets:

**Bundle into Phase 4 planning:**
- **WOR-12** (Medium) — Spend cap TOCTOU reservation. Proxy touch during CLI integration.
- **WOR-17** (Low) — Chunked body size enforcement. Harden proxy before CLI builds on it.
- **WOR-14** (Low) — Integration/E2E test suite. CLI needs E2E coverage anyway.

**Quick standalone (not Phase 4):**
- **WOR-19** — Document CRLF non-risk. Just a doc note, `/gsd:quick`.

**Deferred post-v1:**
- WOR-21, WOR-20, WOR-16, WOR-15, WOR-13, WOR-10, WOR-11

**Beads issues relevant to Phase 4:**
- `worthless-1aj` — Audit log for enrollment actions (CLI-adjacent, SOC2)
- `worthless-ecb` — Compliance & Privacy epic (parent)
- `worthless-y7j` — Flaky Hypothesis test (fix opportunistically)

## 7. Version Target

This phase = **v0.4.0** per `VERSIONING.md`.

## 8. Discussion Decisions (from session 2026-03-25)

### Decided

1. **`lock` as primary command** — one command does everything. `enroll`/`wrap` exist as lower-level primitives for scripting.
2. **No vault** — killed. Provider dashboard is recovery.
3. **Shard A storage: file with 0600 perms** — `~/.worthless/shard_a/{alias}`. Keychain is Harden milestone.
4. **.env key discovery: auto-scan** — `lock` scans `.env`/`.env.local` for known patterns (`sk-*`, `anthropic-*`, etc.). Pipe/flag as fallback.
5. **No deletion, immediate delete** — key destroyed from `.env` as soon as shards confirmed stored.
6. **Bootstrap: auto-bootstrap storage, no proxy start** — `lock` creates Fernet key, DB, dirs silently. Proxy is separate (`wrap`/`up`).
7. **First-run magic** — first `lock` bootstraps + splits + prints next steps (no interactive prompt). Subsequent runs = user picks mode.

### .env Rewrite Strategy: Prefix-Preserving XOR Decoy

**Key insight:** API key prefixes (`sk-proj-`, `anthropic-`, `AIza`) are public information, not secret. XOR only the suffix, keep the prefix:

```
Before: OPENAI_API_KEY=sk-proj-abc123realkeysecret
After:  OPENAI_API_KEY=sk-proj-7f3a9b2e1d8c4f00aa  (prefix kept, suffix is XOR shard)
```

- Zero entropy leaked (prefix was never secret)
- Looks like a real key to attackers → they try it → fails → think it's revoked
- `wrap` ignores `.env` value, loads real shards from `~/.worthless/`
- Not a cosmetic wrapper — it's the actual shard_a with preserved prefix
- Implementation lives in Phase 4 CLI, not crypto core. `split_key()` unchanged.

### Runtime Modes (v0.4.0 ships two access methods + three proxy lifecycles)

**Access methods — how the app authenticates with the proxy:**

| Method | How it works | Best for |
|---|---|---|
| **Wrap** (env injection) | `worthless wrap python main.py` — spawns child with `OPENAI_BASE_URL=http://127.0.0.1:{port}` injected. Child sees proxy URL, no code changes. Stack-agnostic. | Solo dev, scripts, any language |
| **Session token** (hotel card) | Agent calls `GET /v1/session/{alias}` → gets `{"base_url": "...", "token": "wls_sess_...", "expires_in": 300}`. Agent sets base_url + bearer token, talks to proxy. Key never leaves proxy. | AI agents (Claude Code, Cursor), non-Python apps, MCP server |

Both methods route through the proxy. Key never leaves. All three architectural invariants hold.

**Phase scoping:** Wrap ships in v0.4.0. Session tokens ship in v0.4.1.
- v0.4.0 interim: `up` mode uses shard_a in request header as credential (existing design). Curl users do `cat ~/.worthless/shard_a/alias` to get the credential.
- v0.4.1: Session token endpoint replaces raw shard_a auth. Open design question: **how does the session endpoint itself authenticate?** Options: (a) shard_a as the credential to request a session token, (b) static admin token generated during `lock`, (c) localhost-only, no auth. Decide during 4.1 planning.

**Proxy lifecycles — how the proxy runs:**

| Lifecycle | Command | Best for |
|---|---|---|
| Ephemeral | Started by `wrap`, dies with subprocess | Local dev |
| Foreground | `worthless up` (Ctrl+C to stop) | Long-running services |
| Daemon | `worthless up -d` | Power users |
| Docker | `docker compose up` | v0.6.0 (deploy phase) |
| System service | launchd/systemd | v1.x (Harden milestone) |

### Security: Wrap Mode Auth (from security reviewer)

- **Per-session localhost auth token** (HIGH priority) — random 256-bit token injected into both proxy and child env. Proxy rejects requests without it. Prevents local process piggyback.
- **Bind 127.0.0.1 only** — never `0.0.0.0`. Explicit in both modes.
- Unix domain socket considered for `wrap` mode (cleaner than TCP).

### Agent Discovery Stack (from architect)

6-layer discovery, each catches agents that miss the previous:

1. **MCP server** — transparent proxying, richest experience
2. **direnv integration** — `worthless lock` writes `.envrc` with `eval "$(worthless env)"`
3. **Agent rule files** — `.cursor/rules/worthless.mdc`, CLAUDE.md append, `.windsurfrules`
4. **SKILL.md** — shipped with pip package + `.worthless/SKILL.md` generated by `lock`
5. **Error messages** — 401 responses include `worthless wrap` instructions
6. **`worthless wrap`** — explicit fallback

### UX Findings (from UX review)

- No interactive prompts in `lock` — print next-steps block instead
- `lock` on already-locked key must be idempotent or give clear error
- `dotenv.load_dotenv()` inside wrapped commands will overwrite proxy URL — SKILL.md must warn
- Consider `worthless unlock` that exports reconstructed key (requires both shards)

## Open Decisions for Discussion

1. ~~`lock` vs separate~~ → Decided: `lock` as primary.
2. ~~Vault~~ → Killed.
3. ~~Shard A storage~~ → File with 0600.
4. `scan` scope: working tree only, or also git history?
5. Honeypot shard: backlog (prefix-preserving XOR decoy ships in v0.4.0 instead).
6. Should Phase 4 be split into sub-phases (CLI core, lock lifecycle, scan)?
7. Bundle WOR-12/WOR-17/WOR-14 into Phase 4 or keep separate?
