# Phase 4.1 Session Tokens — Brutus Stress Test

**Date:** 2026-03-26
**Verdict:** MODIFY — concept is sound but timing and design are wrong

## 1. Is the hotel card model even needed?

**Attack:** Adding a token layer on top of shard_a auth is complexity theater. The proxy already reads shard_a from the filesystem when no header is present (`app.py:257-260`). Session tokens don't improve the security posture — they add a second credential type alongside shard_a.

**The concrete threat session tokens supposedly mitigate:** An attacker who can read process environment (ps aux, /proc/PID/environ) gets the shard_a value from the `X-Worthless-Shard-A` header. A 5-minute session token limits the blast radius.

**Counter:** If the attacker can read /proc/PID/environ, they can also read the session token from the same environment. Time-limiting doesn't help when the attacker has persistent process access. The real fix is Unix domain sockets (no env var needed).

## 2. Dual-path bypass is fatal

If `X-Worthless-Shard-A` header auth coexists with session tokens, attackers just use the header. Session tokens become optional complexity. You must kill one path, not add a path.

**Options:**
- Kill shard_a header auth when session tokens ship (breaking change)
- Never add session tokens (keep shard_a)
- Make session tokens the ONLY path from day one of 4.1 (clean but aggressive)

## 3. Localhost is not a security boundary

Docker `--network=host`, GitHub Codespaces, WSL2, SSH tunnels, VMs with bridged networking all break the 127.0.0.1 assumption. A no-auth session endpoint on TCP localhost is a container escape waiting to happen.

**The fix:** Unix domain socket with 0600 perms. File permissions are a real security boundary. TCP binding is not.

## 4. Token proliferation

Multiple agents × multiple aliases × 5-min expiry × auto-renewal = unbounded token count. 20 enrolled keys × 5 agents = 100 active tokens. In-memory: fine. SQLite: unnecessary write amplification for ephemeral data.

**Not a real problem** for solo dev use case. But it's a sign that the design is solving a team problem for a solo user.

## 5. The MCP server problem

The MCP server is Node.js. The proxy is Python. The MCP server needs to obtain session tokens and proxy requests. This is wrapping a proxy with another proxy.

**Alternative:** MCP server reads port from `worthless status --json`, sends requests to proxy, proxy reads shard_a from disk. Zero new infrastructure. The MCP server doesn't need tokens — it needs the proxy URL.

## 6. The killer reframe

**Session tokens aren't an auth layer — they're an alias routing mechanism.** The proxy already has the shard_a on disk. The token just tells the proxy which alias to use. Once you see it that way, an `X-Worthless-Alias` header (containing the non-secret alias name) solves the same problem with zero token lifecycle complexity.

Multi-alias concurrent access (two OpenAI keys simultaneously) is the ONLY use case session tokens uniquely solve. That's a team feature, not a solo dev feature.

## 7. You don't need this for v1

The target user is a solo dev dogfooding locally. Multi-agent scenarios (Claude Code + Cursor simultaneously) are real but solvable with alias headers. Session tokens are premature abstraction.

## Recommendation

Ship v0.4.0 and v0.4.1 WITHOUT session tokens. Validate multi-alias concurrent access with real users. If real demand exists, ship in v0.5.0 with:
- Unix domain sockets (real security boundary)
- Single auth path (kill shard_a header, tokens only)
- Token = alias routing + time-limited access, not a security improvement

Don't build auth infrastructure for a problem that doesn't exist yet.
