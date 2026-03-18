# Worthless

## What This Is

Worthless makes API keys worthless to steal. It splits keys using XOR secret sharing so the complete key never exists anywhere stealable, and enforces hard spend caps at the proxy level before the key ever reconstructs. Built for solo devs, small teams, and OpenClaw users who can't afford a $82K surprise bill. Open source, UX-led, stack-agnostic — working in 90 seconds.

## Core Value

A developer installs Worthless and goes back to work with a quiet mind. Their API keys are architecturally worthless to anyone who steals them.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] XOR split-key proxy — key is split client-side, reconstructed per-request server-side, never exists as a complete string at rest
- [ ] CLI enrollment — `worthless enroll` splits a key into Shard A (client) + Shard B (server), confirms protection
- [ ] CLI wrap — `worthless wrap` configures env vars so API calls route through the proxy transparently
- [ ] Local proxy — runs on localhost, in-process reconstruction, zero cloud dependency for dogfood
- [ ] Terminal confirmation — after install, clear output: "N keys found, all protected. Done." then silence
- [ ] Stack-agnostic — works for Python, Node, Go, Rust, any language that makes HTTP calls to LLM providers
- [ ] OpenAI + Anthropic provider support
- [ ] Three architectural invariants enforced: client-side splitting, gate before reconstruction, server-side direct upstream call

### Out of Scope

- Hosted spend cap enforcement — future work, not in initial scope
- MCP server — Claude Code / Cursor integration (after core proxy works)
- Dashboard UI — future work
- Team management — future work
- Docker Compose / Railway / Render deploy — after local works
- Anomaly detection — future work
- SSO/SAML, response caching, load balancing, content filtering
- Gemini support — stretch goal

## Context

- **Origin:** $82,314 Gemini API key theft (February 2026). Three-person team in Mexico, $180/month bill, 48 hours later facing bankruptcy. Google cited shared responsibility model.
- **Market gap:** Every competitor (Portkey, Helicone, LiteLLM, Infisical, Vault) protects the key. None eliminate it. "Zero Standing API Keys" is unoccupied territory.
- **Brand:** The name IS the pitch. "Worthless" is the strategic asset — the negative word is the value proposition. Simultaneously a honeypot and actual production key that's worth nothing.
- **Positioning:** "Stolen? So what." / "No key. No breach. No bill." / "They protect the key. We eliminate it."
- **Target:** Solo dev dogfood first → OpenClaw user second → small teams third.
- **Build order:** PoC (Python + SQLite) → Harden (Rust reconstruction) → Attack (pen-test).
- **PRD:** Maintained separately — read-only reference, GSD phases are source of truth.

## Constraints

- **Language**: Python 3.12 / FastAPI for proxy and CLI (PoC phase). Rust for reconstruction service (hardening phase).
- **Security**: Three architectural invariants are non-negotiable (see CLAUDE.md). Any violation requires full security review.
- **UX**: 90-second install target. Terminal output confirmation, not dashboards. Peace of mind, not features.
- **Logging**: API keys, cert private keys, base64 strings > 32 chars, prompt/response content, raw IPs must NEVER appear in any log.
- **Scope**: Dogfood-first. Local proxy only. No hosted infrastructure until core works.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Python-first PoC, Rust later | Ship fast, harden later — build order from CLAUDE.md | — Pending |
| Local-only for dogfood | Simplest path to validation, no infra dependency | — Pending |
| Split-key + proxy, not just proxy | Eliminating the key is the differentiator, not just capping spend | — Pending |
| No spend cap in dogfood | Cap requires hosted proxy — honest about what local can/can't do | — Pending |

---
*Last updated: 2026-03-14 after initialization*
