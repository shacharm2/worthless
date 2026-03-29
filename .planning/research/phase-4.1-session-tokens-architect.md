# Phase 4.1 Session Tokens — Architect Analysis

**Date:** 2026-03-26

## Q1: Session Token Authentication

**Recommendation: Option (b) admin token, with mode-aware layering**

- **Wrap mode (ephemeral):** Already has a per-session 256-bit localhost auth token (Phase 4.0 design). Session tokens are unnecessary here — the wrapped child process already has everything injected via env vars. If an agent inside a wrapped process wants to discover available aliases dynamically, it can use the wrap auth token to call the session endpoint. No new credential needed.

- **Up mode (foreground/daemon):** This is where session tokens matter. The proxy is long-running, potentially for hours or days in daemon mode. Option (a) using shard_a as the credential to request a session token is problematic — it means shard_a still transits the wire on every session request, which defeats the goal of keeping shard_a off the network. Option (c) localhost-only with no auth is dangerous for daemon mode — any local process (malware, compromised npm package, another user on shared machines) can mint tokens freely. Option (b) is correct: a static admin token generated during `lock`, stored at `~/.worthless/admin_token` with 0600 perms. The session endpoint requires `Authorization: Bearer wls_admin_...` to mint session tokens.

- **The layering:** In wrap mode, the wrap auth token (already injected) doubles as the admin credential for the session endpoint. In up mode, the admin token from `~/.worthless/admin_token` is the credential. Same endpoint, same validation logic, different token source. Both are 256-bit random tokens (SR-08: CSPRNG only).

## Q2: Token Lifecycle — In-memory, mode-independent

- **Creation:** `POST /v1/sessions` (not GET — it has side effects) with alias in the body. Returns `{"base_url": "http://127.0.0.1:{port}", "token": "wls_sess_{32 random hex chars}", "expires_in": 300, "alias": "openai-prod"}`.
- **Validation:** Token lookup in an in-memory dict (O(1) by token prefix). Check expiry, check revocation flag.
- **Expiry:** Default 300 seconds (5 minutes). Configurable per-request up to `max_session_ttl` (default 3600). Short-lived by design — agents re-request when expired.
- **Revocation:** `DELETE /v1/sessions/{token_id}` or `DELETE /v1/sessions` (revoke all). Immediate effect.
- **Storage:** In-memory `dict[str, SessionRecord]` is sufficient for all modes. Session tokens are ephemeral by design. If the proxy restarts, all sessions are invalidated — agents re-request. This is the correct behavior for a security credential. SQLite persistence would mean session tokens survive proxy restarts, which is a larger attack window. In-memory is simpler and more secure.
- **Daemon mode concern:** In `up -d`, the proxy runs for days. In-memory is still correct — the dict is bounded by `max_active_sessions` (default 100). Expired tokens are lazily evicted on lookup + periodic sweep (every 60 seconds). Memory footprint: ~200 bytes per session x 100 = 20KB.

## Q3: Token Scoping — Alias-scoped, enrollment-level spend caps

- **Each session token is scoped to exactly one alias.** A token minted for "openai-prod" cannot access "anthropic-staging". Principle of least privilege. If an agent needs multiple providers, it requests multiple tokens.
- **Per-token spend limits: defer to v0.4.2.** The enrollment-level spend cap (`enrollment_config.spend_cap`) applies to all requests for that alias regardless of which session token is used. The spend cap protects the key, not the session.
- **Token metadata:** Each `SessionRecord` stores: `token_hash` (SHA-256 of token — never store raw token), `alias`, `created_at`, `expires_at`, `client_label` (optional: "claude-code", "cursor"), `request_count` (observability).

## Q4: MCP Server Integration — MCP server is the session token consumer

**Flow:**
1. User configures MCP server with the admin token (or MCP server reads `~/.worthless/admin_token`)
2. Agent (Claude Code) sends a tool call via MCP protocol to the MCP server
3. MCP server calls `POST /v1/sessions` with admin token to get a session token for the requested alias
4. MCP server uses the session token to proxy the agent's actual API call through Worthless
5. The agent never interacts with Worthless proxy directly — MCP server abstracts this

**Why not let the agent call the session endpoint directly?**
- Agent would need the admin token (another secret in agent's environment)
- MCP server already has trust relationship with proxy (local service)
- MCP server can cache/reuse session tokens across tool calls
- Agent shouldn't know about Worthless internals

**MCP server token management:** Holds at most one active session token per alias. On expiry (or 401 from proxy), transparently re-requests. Agent sees zero Worthless-specific behavior.

## Q5: Multi-Agent Scenarios — Token isolation, shared spend tracking

- **Each agent gets its own session token.** Claude Code and Cursor running simultaneously each mint their own `wls_sess_...` tokens. Independent — revoking one doesn't affect the other.
- **Spend tracking is per-alias, not per-token.** Both agents' requests to "openai-prod" count against the same `enrollment_config.spend_cap`. Correct — spend cap protects the provider key, which is shared.
- **Per-agent auditing (v0.4.2):** Optional `client_label` enables `worthless status` to show "openai-prod: 2 active sessions (claude-code: 1, cursor: 1)". `spend_log` table gets `session_id` column for per-agent attribution after the fact.
- **Token limit:** `max_active_sessions` (default 100). Per-alias limit of 10 active sessions.
- **Race condition:** Two agents hitting session endpoint simultaneously is fine — each gets a different token. No shared mutable state (token = `secrets.token_hex(32)`).

## Q6: Migration Path — Additive, backwards compatible

1. **v0.4.0 ships:** Proxy authenticates via `x-worthless-alias` + `x-worthless-shard-a` headers (or shard_a file fallback). Wrap mode adds per-session localhost auth token.

2. **v0.4.1 adds (does not replace):**
   - `POST /v1/sessions` (mints session tokens, requires admin token)
   - `DELETE /v1/sessions/{token_id}` (revokes)
   - `GET /v1/sessions` (lists active, requires admin token)
   - Proxy catch-all gains new auth path: `Authorization: Bearer wls_sess_...`
   - Admin token generated during `lock`, stored at `~/.worthless/admin_token`

3. **v0.4.1 auth resolution order:**
   ```
   (1) Authorization: Bearer wls_sess_... -> session token path (new)
   (2) x-worthless-alias + x-worthless-shard-a -> legacy path (existing)
   (3) wrap auth token -> wrap mode path (existing from v0.4.0)
   (4) Reject with uniform 401
   ```

4. **Deprecation timeline:**
   - v0.4.1: Both paths work. `x-worthless-shard-a` header logs deprecation warning (once per alias).
   - v0.5.0: `x-worthless-shard-a` header removed. Session tokens are the only external auth.

## Security Rules Compliance

| Rule | Session token compliance |
|------|------------------------|
| SR-01 | Session tokens are not key material — `str` is acceptable |
| SR-02 | No key material in token creation/validation. No zeroing needed |
| SR-03 | Gate-before-reconstruct preserved. Token validation → rules engine → reconstruction |
| SR-04 | Add `wls_sess_` and `wls_admin_` to logging denylist. Token hash is loggable |
| SR-07 | Token comparison: `hmac.compare_digest(sha256(presented), stored_hash)`. Never `==` |
| SR-08 | `secrets.token_hex(32)` — CSPRNG compliant |

## New Config

```python
admin_token: str       # WORTHLESS_ADMIN_TOKEN or ~/.worthless/admin_token
max_session_ttl: int = 3600
max_active_sessions: int = 100
default_session_ttl: int = 300
```

## Token Namespaces

- `wls_wrap_...` — wrap mode per-session token (v0.4.0)
- `wls_sess_...` — session token (v0.4.1)
- `wls_admin_...` — admin token for minting sessions (v0.4.1)

## Open Questions for 4.1 Discussion

1. POST vs GET for session creation? (Recommendation: POST — side effects)
2. Should admin token rotate on re-enrollment? (Recommendation: Stable — separate from key lifecycle)
3. Should wrap token double as admin token? (Recommendation: Yes — same trust level)
4. Token format: hex vs base64url? (Recommendation: hex — simpler, 128 bits sufficient for localhost)
