# Worthless

## What This Is

Worthless makes API keys worthless to steal. It splits keys using XOR secret sharing so the complete key never exists anywhere stealable, and enforces hard spend caps at the proxy level before the key ever reconstructs. Built for solo devs, small teams, and OpenClaw users who can't afford a $82K surprise bill. Open source, UX-led, stack-agnostic — working in 90 seconds.

## Core Value

A developer installs Worthless and goes back to work with a quiet mind. Their API keys are architecturally worthless to anyone who steals them.

## Current Milestone: v2.0 Harden

**Goal:** Add secure mode (Shamir 2-of-3 + Rust sidecar) alongside the permanent light mode (XOR + Fernet). Light mode stays unchanged forever. Secure mode is purely additive.

**Target features:**
- Shamir 2-of-3 secret sharing (GF(256), Rust implementation) — secure mode splitting primitive
- Rust sidecar binary — IPC over Unix socket, vault mode + proxy mode with SSE streaming
- Sidecar OS-level hardening — seccomp-BPF, Landlock, optional `--hardened` install (separate Unix user)
- Platform credential store backends — macOS Keychain, Windows Credential Manager, Linux kernel keyring, Docker secrets, encrypted file fallback
- Python layer rewired for secure mode — `worthless up --secure` routes through sidecar IPC; `worthless up` stays exactly as v1.0 (XOR + Fernet, single process)
- Optional migration tool — `worthless migrate` converts Fernet enrollments to Shamir per-key with rollback; mixed state (some Fernet, some Shamir) works
- Distribution — maturin wheels (manylinux2014 x86_64/aarch64, macOS universal2, Windows x86_64), Docker multi-container, fallback binary via GitHub Releases
- Security documentation — SECURITY_POSTURE.md updated, cargo audit + cargo vet gates, updated SECURITY_RULES.md for Shamir + sidecar invariants

**Architectural constraint:** Light mode (XOR + Fernet) is PERMANENT. Fernet is NOT eliminated. The `cryptography` dependency stays. Secure mode is additive — the two modes coexist forever. No requirement may say "remove Fernet" or "eliminate cryptography."

## Requirements

### Validated

- ✓ XOR split-key proxy — key is split client-side, reconstructed per-request server-side, never exists as a complete string at rest — v1.0
- ✓ CLI enrollment — `worthless lock` splits a key into Shard A (client) + Shard B (server), confirms protection — v1.0
- ✓ CLI wrap — `worthless wrap` configures env vars so API calls route through the proxy transparently — v1.0
- ✓ Local proxy — runs on localhost, in-process reconstruction, zero cloud dependency — v1.0
- ✓ Terminal confirmation — `worthless status` shows protected keys and proxy health — v1.0
- ✓ Stack-agnostic — works for any language that makes HTTP calls via BASE_URL override — v1.0
- ✓ OpenAI + Anthropic provider support — v1.0
- ✓ Three architectural invariants enforced: client-side splitting, gate before reconstruction, server-side direct upstream call — v1.0 (Enforced tier, evidence-backed)

### Active

- [ ] MCP server — Claude Code / Cursor / Windsurf integration
- [ ] Docker Compose / Railway / Render deploy configs
- [ ] Rules engine: model_allowlist, token_budget, time_window rules
- [ ] `worthless keys` command for listing protected keys
- [ ] `worthless daemon` background proxy mode
- [ ] Redis hot-path metering
- [ ] Email + Slack alerts for spend velocity
- [ ] `worthless scan` pre-commit hook wiring
- [ ] SKILL.md agent discovery file

### Out of Scope

- Dashboard UI — SaaS, worthless-cloud repo
- Team management UI — SaaS, worthless-cloud repo
- Hosted spend cap enforcement — requires cloud infrastructure
- SSO/SAML, response caching, load balancing, content filtering
- Gemini support — stretch goal
- Anomaly detection beyond spend velocity — Pro+ tier

## Context

- **Origin:** $82,314 Gemini API key theft (February 2026). Three-person team in Mexico, $180/month bill, 48 hours later facing bankruptcy. Google cited shared responsibility model.
- **Market gap:** Every competitor (Portkey, Helicone, LiteLLM, Infisical, Vault) protects the key. None eliminate it. "Zero Standing API Keys" is unoccupied territory.
- **Brand:** The name IS the pitch. "Worthless" is the strategic asset — the negative word is the value proposition.
- **Positioning:** "Stolen? So what." / "No key. No breach. No bill." / "They protect the key. We eliminate it."
- **Target:** Solo dev dogfood first → OpenClaw user second → small teams third.
- **Build order:** PoC (Python + SQLite) → Harden (Rust reconstruction) → Attack (pen-test).
- **Current state:** v1.0 shipped (2026-04-03). 4,399 LOC Python, 38 source files. Tech stack: Python 3.12, FastAPI, aiosqlite, cryptography (Fernet). All 3 architectural invariants at Enforced confidence tier. 5-tier CI pipeline with coverage gates.
- **Known limitations:** Python GC non-determinism for memory zeroing (documented in SECURITY_POSTURE.md, Rust sidecar resolves this). `api_key.decode()` creates immutable str copy in proxy (eliminated by sidecar direct-upstream-call).
- **v2.0 research:** Extensive research completed in `docs/research/` — Shamir architecture, sidecar design, platform credential stores, security review, crypto verification, implementation plan (6 phases). Key correction: implementation-plan.md says "Fernet eliminated" in several places — WRONG, light mode is permanent.

## Constraints

- **Language**: Python 3.12 / FastAPI for proxy and CLI (PoC phase). Rust for reconstruction service (hardening phase).
- **Security**: Three architectural invariants are non-negotiable (see CLAUDE.md). Any violation requires full security review.
- **UX**: 90-second install target. Terminal output confirmation, not dashboards. Peace of mind, not features.
- **Logging**: API keys, cert private keys, base64 strings > 32 chars, prompt/response content, raw IPs must NEVER appear in any log.
- **Scope**: Dogfood-first. Local proxy only. No hosted infrastructure until core works.
- **Coexistence**: Light mode (XOR + Fernet) is permanent. Secure mode (Shamir + sidecar) is additive. Both modes coexist forever. Mixed key state (some Fernet, some Shamir) is supported.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Python-first PoC, Rust later | Ship fast, harden later — build order from CLAUDE.md | ✓ Good — shipped in 19 days |
| Local-only for dogfood | Simplest path to validation, no infra dependency | ✓ Good — zero cloud dependency |
| Split-key + proxy, not just proxy | Eliminating the key is the differentiator, not just capping spend | ✓ Good — core differentiator validated |
| No spend cap in dogfood | Cap requires hosted proxy — honest about what local can/can't do | ✓ Good — spend_cap rule works locally with SQLite |
| Frozen dataclasses over Pydantic | Less overhead for adapter layer, immutable by default | ✓ Good |
| Fernet for shard encryption at rest | stdlib-adjacent, good enough for PoC, no key management burden | ✓ Good |
| Gate-before-reconstruct split | fetch_encrypted + decrypt_shard enables rules to deny before any KMS | ✓ Good — core invariant |
| lock/unlock terminology over enroll | User-facing language matches mental model of "locking" a key | ✓ Good |
| 5-tier CI (not 2-tier) | Separate fast gate from full audit prevents developer friction | ✓ Good |
| Evidence-backed security posture | Confidence tiers (Enforced/Best-effort/Planned) with test citations | ✓ Good — honest documentation |

| Light mode permanent, secure mode additive | Fernet bootstrap problem makes elimination wrong; two audiences (vibe coder vs production) need different tradeoffs | — Pending |

---
*Last updated: 2026-04-06 after v2.0 Harden milestone started*
