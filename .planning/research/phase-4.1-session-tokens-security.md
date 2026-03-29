# Phase 4.1 Session Tokens — Security Threat Analysis

**Date:** 2026-03-26

## 1. Token Generation

- **256 bits** via `secrets.token_urlsafe(32)`
- **Format:** `wls_sess_{base64url}` — prefix is safe, does not leak info, enables scanning/denylist matching
- CSPRNG compliant (SR-08)

## 2. Token Storage — In-Memory Recommended

- **In-memory strongly recommended.** Restart = implicit revocation. This is correct behavior for ephemeral credentials
- SQLite expands attack surface for ephemeral data — disk forensics, WAL files, backup exposure
- Dict keyed by `SHA-256(token)` so raw tokens never stored server-side
- Bounded by `max_active_sessions` — no unbounded growth

## 3. Session Endpoint Authentication — Split by Mode

### Wrap mode
- **No auth needed on session endpoint.** Wrap pre-creates token, injects into child env. Session endpoint never exposed to child process directly
- The wrap auth token already authenticates the session

### Up / Up -d mode
- **shard_a as credential (option a).** Natural upgrade from current per-request shard_a design. The session endpoint becomes the one place shard_a is used, then all subsequent requests use the short-lived session token
- **Static admin token (option b) rejected.** Creates a higher-value target — a single credential that can mint unlimited session tokens. If compromised, attacker has persistent access. shard_a is already on disk with 0600, no new secret to manage
- **Localhost-only no-auth (option c) rejected for up mode.** Security regression — any local process (malware, compromised dependency, other user) can mint tokens freely. Acceptable only if Unix domain socket with 0600 perms is used (not TCP)

## 4. Token Theft — Strictly Better Than Raw shard_a

Session tokens are strictly better than raw shard_a exposure:
- **Time-limited** (300s default) — stolen token expires
- **Revocable** — can be killed immediately
- **Subject to rules engine** — spend caps still enforced
- **Same-machine attacker** can already read shard_a directly from `~/.worthless/shard_a/` — session tokens create no NEW attack surface
- **Blast radius:** stolen session token = 5 minutes of access to one alias. Stolen shard_a = unlimited access until re-enrolled

## 5. Replay Attacks

- **No IP or PID binding.** Localhost-only makes both useless (always 127.0.0.1, PIDs are spoofable)
- **Defenses:** Short TTL (300s) + rate limiting + spend caps
- Token is bearer-only — possession = access. This is acceptable for localhost-only, short-lived tokens
- If Worthless ever goes remote (hosted proxy), token binding becomes critical — defer to that phase

## 6. Revocation — Four Mechanisms

1. **Self-revoke:** `DELETE /v1/sessions/{token_id}` — agent cleans up after itself
2. **Admin-revoke:** Same endpoint with shard_a/admin auth — revoke any session
3. **CLI command:** `worthless sessions revoke --alias openai-prod` — kills all sessions for an alias
4. **Implicit on restart:** In-memory store = all sessions invalidated on proxy restart

## 7. Security Rules Compliance

| Rule | Compliance | Notes |
|------|-----------|-------|
| SR-01 (bytearray for secrets) | **Accepted deviation** | Session tokens are auth credentials, not key material. `str` is acceptable. Rationale: tokens are ephemeral (300s), not cryptographic shards |
| SR-02 (explicit zeroing) | **Not required** | No key material in token lifecycle |
| SR-03 (gate before reconstruct) | **Fully preserved** | Token validation → rules engine → reconstruction. Adding token lookup is a pre-gate step |
| SR-04 (zero telemetry on secrets) | **Action required** | Add `wls_sess_` to logging denylist patterns. Token hash is loggable, raw token is not |
| SR-05 (logging denylist) | **Action required** | Add `wls_sess_*`, `wls_admin_*`, `wls_wrap_*` to denylist scan patterns |
| SR-07 (constant-time compare) | **Addressed** | Hash-keyed store: `hmac.compare_digest(sha256(presented), stored_hash)`. Never `==` on raw tokens |
| SR-08 (CSPRNG only) | **Compliant** | `secrets.token_urlsafe(32)` |

## Summary: Mandatory for 4.1 Implementation

| Item | Effort | Priority |
|------|--------|----------|
| In-memory token store with SHA-256 keying | M | Required |
| shard_a auth for session endpoint (up mode) | S | Required |
| Wrap token doubles as session credential | S | Required |
| Add wls_* patterns to logging denylist | S | Required |
| Constant-time token comparison via hash lookup | S | Required |
| Token expiry + lazy eviction + periodic sweep | M | Required |
| Revocation endpoints (DELETE) | S | Required |
| `worthless sessions` CLI subcommand | M | Nice to have |

## Key Disagreement with Architect

The architect recommends option (b) static admin token. This analysis recommends option (a) shard_a as credential for the session endpoint.

**Rationale:** An admin token is a new high-value secret that must be generated, stored, rotated, and protected. shard_a already exists, already has 0600 perms, and is already the trust anchor. Using shard_a to mint session tokens means the session endpoint is just a "trade long-lived credential for short-lived credential" exchange — a well-understood security pattern (cf. OAuth client_credentials flow).

**The counter-argument:** shard_a transits the wire on every session request. But it's localhost only (127.0.0.1), and it only happens once per 5-minute window (not per API call). The risk is minimal compared to the operational complexity of managing a separate admin token.
