# Why Not — Rejected Ideas and Rationale

Ideas explored and killed with expert analysis. Preserved so future contributors don't re-propose them.

## Killed

### Local Vault / Soft-Delete Recovery
**Idea:** After `lock`, save the original key encrypted in `~/.worthless/vault/` as a safety net.
**Why not:** Another attack surface (supply chain, disk forensics, SSD wear-leveling). Two code paths (dev/prod), two test matrices. The problem barely exists — regenerating a key from the provider dashboard takes 30 seconds. Recovery path = provider dashboard.

### Honeypot Shard (XOR-Constrained)
**Idea:** Constrain XOR output so shard_a looks like a real API key (same prefix).
**Why not:** Fixing shard_a's prefix to match the key's prefix leaks key prefix bits. Converts information-theoretically secure OTP into a partially-leaked split. Cryptographic regression in a security product.
**What shipped instead:** Prefix-preserving XOR — keep the PUBLIC prefix (sk-proj-), XOR only the secret suffix. Zero entropy leaked because the prefix was never secret.

### Mode 2: Direct Inject (`--direct`)
**Idea:** `worthless wrap --direct` reconstructs the real key and injects it into the child's env vars. No proxy, no latency.
**Why not:** Breaks architectural invariant #3 (server-side direct upstream call). Key sits in child's `/proc/environ`, readable by any same-UID process. Gate (spend cap, rate limit) completely bypassed. Makes Worthless a fancy `.env` decryptor — `sops` and `age` already do that better. Brand risk: "Worthless has a mode that makes keys worth stealing."

### Mode 3: Key Vending API
**Idea:** Worthless exposes `GET /v1/keys/{alias}` that returns the real API key to any authenticated caller.
**Why not:** Once the key is returned, the client bypasses the gate entirely. Key transits HTTP (even localhost is sniffable with CAP_NET_RAW). Client holds the real key in memory — identical threat model to `.env`. This is HashiCorp Vault but worse — Vault vends scoped credentials from the provider, Worthless would vend the master key itself. Constraints (TTL, one-time use) reduce the window but don't fix the fundamental break.
**What shipped instead:** Session-scoped proxy tokens (hotel card model). `GET /v1/session/{alias}` returns a temporary proxy credential (`wls_sess_...`), not the real key. Agent uses the credential to talk to the proxy. Proxy validates, runs rules, reconstructs server-side. Key never leaves. All three invariants hold. Same UX convenience as key vending, none of the security breaks.

### 3-of-3 Shard Split (Shamir)
**Idea:** Split the key into 3 shards instead of 2 using Shamir's Secret Sharing.
**Why not:** Adds complexity without security benefit in the single-proxy threat model. 3-of-3 is for team key escrow / multi-party scenarios. Deferred to v2 if team features ship.

### OS Keychain for Shard A (PoC)
**Idea:** Store shard_a in macOS Keychain / Linux secret-service by default.
**Why not for PoC:** Platform-specific complexity (macOS Keychain API, Linux D-Bus secret-service, Windows Credential Manager). File with 0600 perms works everywhere including Docker/CI/headless. Keychain is a Harden milestone upgrade.

### Native OS Service (PoC)
**Idea:** Install Worthless as a launchd/systemd service for daemon mode.
**Why not for PoC:** Each OS needs its own install/uninstall/restart logic, log rotation, crash recovery, auto-start-on-boot. Docker solves the same problem OS-agnostically. Ship `wrap` (ephemeral) + `up` (foreground) for PoC, Docker for deploy phase, native service for Harden milestone.

## Deferred (Not Killed)

### Canary / Honeypot Detection
**Idea:** When an attacker tries a stolen shard, it triggers an alert.
**Why:** You don't control provider auth endpoints. Detection requires provider cooperation (OpenAI doesn't offer "notify when invalid key used") or a Worthless-operated honeypot endpoint (Thinkst Canarytoken architecture).
**When:** Post-V1, if provider partnerships or self-hosted canary infrastructure exists.

### MPC (Multi-Party Computation)
**Idea:** Compute the provider auth header without ever assembling the full key.
**Why deferred:** Requires either provider cooperation or custom protocol. The key still needs to exist in the HTTP Authorization header — MPC just minimizes how long it exists in memory. Research-level for PoC.
**When:** Harden milestone (Rust, register-level zeroing).
**Tracked:** Beads `worthless-6l2`.

### Key Rotation Flag (`--rotate`)
**Idea:** `worthless lock --rotate` atomically swaps old→new key.
**Why deferred:** Manual rotation works fine — put new key in .env, run `lock` again. Old shards overwritten. No security gap (old key already rotated at provider). `--rotate` is a convenience, not a security feature.
**When:** v2 or when users ask.
