# Requirements: Worthless

**Defined:** 2026-03-14
**Core Value:** A developer installs Worthless and goes back to work with a quiet mind. Their API keys are architecturally worthless to anyone who steals them.

## v1 Requirements

### Cryptography

- [ ] **CRYP-01**: Key is split into Shard A (client) + Shard B (server) using XOR with `secrets.token_bytes()`
- [ ] **CRYP-02**: HMAC commitment verifies shard integrity on reconstruction
- [ ] **CRYP-03**: Reconstructed key stored in `bytearray`, zeroed after use (documented as best-effort in Python)
- [ ] **CRYP-04**: `secrets` module enforced, `random` module banned via lint rule
- [x] **CRYP-05**: Rules engine evaluates request BEFORE Shard B is decrypted (gate-before-reconstruct)

### Proxy

- [x] **PROX-01**: OpenAI-compatible endpoint (`/v1/chat/completions`)
- [x] **PROX-02**: Anthropic-compatible endpoint (`/v1/messages`)
- [x] **PROX-03**: SSE streaming relay for both providers
- [x] **PROX-04**: Stack-agnostic via `BASE_URL` env var rewriting (no SDK import needed)
- [x] **PROX-05**: Reconstruction happens server-side, key never returns to client

### CLI

- [ ] **CLI-01**: `worthless enroll` splits key, stores Shard A locally, sends Shard B to proxy
- [ ] **CLI-02**: `worthless wrap` sets env vars so API calls route through proxy
- [ ] **CLI-03**: `worthless status` shows protected keys and proxy health
- [ ] **CLI-04**: `worthless scan` pre-commit hook detects leaked keys in code

### Storage

- [ ] **STOR-01**: Shard B encrypted at rest (aiosqlite)
- [ ] **STOR-02**: Enrollment metadata persisted locally

### Documentation

- [ ] **DOCS-01**: SECURITY_POSTURE.md with protection status, confidence levels, known limitations

## v2 Requirements

### Hosted Proxy

- **HOST-01**: Hosted proxy with hard spend cap enforcement
- **HOST-02**: Per-key spend tracking and budget enforcement

### Integrations

- **INTG-01**: MCP server for Claude Code / Cursor / Windsurf
- **INTG-02**: OpenClaw ClawHub skill

### Deployment

- **DEPL-01**: Docker Compose self-hosted deploy
- **DEPL-02**: Railway / Render one-click deploy

### Monitoring

- **MNTR-01**: Spend velocity anomaly detection
- **MNTR-02**: Email + Slack alerts

## Out of Scope

| Feature | Reason |
|---------|--------|
| Dashboard UI | Future work, separate repo |
| Team management UI | Future work, separate repo |
| Geo/time/model anomaly detection | Future work |
| SSO/SAML | Enterprise feature, later |
| Response caching, load balancing | Not a gateway product -- stay in security lane |
| Provider-managed keys | BYOK only -- the key must be the user's |
| Gemini support | Stretch goal |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| CRYP-01 | Phase 1 | Pending |
| CRYP-02 | Phase 1 | Pending |
| CRYP-03 | Phase 1 | Pending |
| CRYP-04 | Phase 1 | Pending |
| CRYP-05 | Phase 3 | Complete |
| PROX-01 | Phase 2 | Complete |
| PROX-02 | Phase 2 | Complete |
| PROX-03 | Phase 2 | Complete |
| PROX-04 | Phase 3 | Complete |
| PROX-05 | Phase 3 | Complete |
| CLI-01 | Phase 4 | Pending |
| CLI-02 | Phase 4 | Pending |
| CLI-03 | Phase 4 | Pending |
| CLI-04 | Phase 4 | Pending |
| STOR-01 | Phase 1 | Pending |
| STOR-02 | Phase 1 | Pending |
| DOCS-01 | Phase 5 | Pending |

**Coverage:**
- v1 requirements: 17 total
- Mapped to phases: 17
- Unmapped: 0

---
*Requirements defined: 2026-03-14*
*Last updated: 2026-03-14 after roadmap creation*
