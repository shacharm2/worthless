# Worthless

**We make your API keys worthless to steal.**

A three-person team had a $180/month Gemini bill. Someone stole their API key. Forty-eight hours later: $82,314. Google cited the shared responsibility model. The team faces bankruptcy.

Worthless makes that outcome architecturally impossible.

---

## How it works

**The key never exists as a complete string anywhere it can be stolen.**

Your API key is split into two halves using XOR secret sharing. One half stays on your machine. One half is encrypted on the server. Neither half alone reveals anything about the key — this is information-theoretic security, not just encryption.

Every request goes through the Worthless proxy. Before your key ever reconstructs:
- Is the spend cap still clear? 
- Is the rate limit still open?
- Is the model allowed?

If anything fails — the key never forms. The request stops. The attacker gets nothing.

A stolen Worthless credential has a hard cap, routes through a proxy, and unlocks a key that expires after one request's lifetime. It is **literally worthless**.

```
Your code → Worthless proxy → [cap check] → [key reconstructs] → OpenAI / Anthropic
                                    ↓
                              If cap hit: stops here.
                              Key never forms.
```

---

## Install

Pick your path:

### I use Claude Code, Cursor, or Windsurf

```json
{
  "mcpServers": {
    "worthless": {
      "command": "npx",
      "args": ["-y", "worthless-mcp"],
      "env": { "WORTHLESS_BUDGET": "10.00", "WORTHLESS_PERIOD": "daily" }
    }
  }
}
```

Add to your `.mcp.json`. Every AI API call is now budget-protected. Done.

---

### I want to protect my own code (solo dev)

```bash
curl worthless.sh | sh
```

This will:
1. Open a browser for a one-click auth (GitHub or Google)
2. Ask you to paste your API key
3. Ask for a daily spend cap
4. Start a local proxy on `localhost:9191`

Then swap one environment variable:

```bash
export OPENAI_BASE_URL=http://localhost:9191/v1
# or
export ANTHROPIC_BASE_URL=http://localhost:9191/v1
```

Your existing code works identically. Your key now has a hard cap.

**Target: working in 90 seconds.**

---

### I use OpenClaw

```yaml
# In your OpenClaw config
api_base: https://api.worthless.cloud/openai
worthless_cap: 50
worthless_period: daily
```

One config line. Your agent can no longer run up a bill while you sleep.

Or install the Worthless skill from ClawHub: `worthless-guard`

---

### I want to self-host

```bash
curl worthless.sh | sh --self-hosted
```

Pulls a Docker Compose stack (proxy + Postgres + Redis), prompts for config, starts running. Your infrastructure, your data, your control.

Full Helm charts and Terraform modules available in `deploy/`.

**Target: working in under 5 minutes.**

---

### My team needs shared key management

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/template/worthless)

One-click deploy. Get a team dashboard, per-member spend caps, and contractor management in 2 minutes.

---

## The aha moment

First proxied call:

```
✓ Request proxied
  Model: gpt-4o-mini  |  Cost: $0.003  |  Remaining today: $9.997
```

---

## What Worthless protects

- ✅ API key stolen from GitHub, `.env` file, or client-side JS
- ✅ Agent or script running a billing loop overnight  
- ✅ Contractor or team member exceeding their budget
- ✅ Stolen key used by an attacker to rack up charges
- ✅ Anomalous usage patterns (spend velocity, unusual models, geographic)

## What Worthless does not protect

- ❌ Full machine compromise (same boundary as 1Password)
- ❌ Upstream LLM provider outages
- ❌ Content safety or prompt injection

---

## CLI reference

```bash
worthless enroll              # First-time setup (opens browser, splits key)
worthless wrap -- python app.py  # Run any command with proxy + env vars set
worthless scan                # Scan files for exposed API keys
worthless status              # Show current spend, cap, remaining
worthless keys rotate <alias> # Rotate underlying API key without changing proxy config
worthless daemon start        # Start/stop the local sidecar proxy
```

**Pre-commit hook** — detects API keys before they hit GitHub:

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/worthless/worthless
    rev: v1.0.0
    hooks:
      - id: worthless-scan
```

Unlike TruffleHog or GitGuardian which only alert, `worthless scan` detects and offers immediate enrollment — remediation in the same step.

---

## Security model

**What the server never sees:**
- Your full API key (only receives one half at enrollment)
- Shard A (your half, stored in your OS keychain)
- Request or response content (proxy has no body-parsing code, verified by CI)
- Reconstructed key (exists only in isolated Rust memory for < 500ms)

**Trust boundaries:**

| Deployment | Key security | Data privacy |
|---|---|---|
| Self-hosted | Architectural — key splitting | Architectural — your servers |
| Managed | Architectural — key splitting + customer-managed KMS | Policy — no-log, open source, audited |

Full security model and threat boundary: [docs/security.md](docs/security.md)

---

## Providers

| Provider | Status |
|---|---|
| OpenAI | ✅ Full drop-in proxy |
| Anthropic | ✅ Full drop-in proxy |
| Gemini | 🚧 In progress |
| Others | Coming — PRs welcome |

---

## Self-hosting

All components are open source (AGPL-3.0). Self-hosted is a first-class deployment path and will never be degraded or removed.

See [deploy/](deploy/) for:
- `docker-compose.yml` — local or single-server deployment
- `helm/` — Kubernetes deployment
- `terraform/` — cloud infrastructure
- Railway and Render deploy templates

---

## Architecture

```
worthless/
  proxy/          # Python/FastAPI — rules engine, metering, mTLS
  reconstruction/ # Rust — crypto hot path, XOR, memory zeroing
  cli/            # Python — enrollment, sidecar daemon, wrap, scan
  mcp/            # Node.js — MCP server
  deploy/         # Docker Compose, Helm, Railway, Render
  docs/           # Architecture, security model, threat boundary
```

The reconstruction service is written in Rust for deterministic memory control. Key material is mlock'd, explicitly zeroed, and exists for < 500ms. No GC'd language touches the crypto path.

---

## Contributing

PRs welcome. Before contributing, read [docs/security.md](docs/security.md) — especially the three architectural invariants. Any PR that violates them will be closed regardless of other merits.

The three invariants:
1. Client-side splitting only — server never receives the full key
2. Gate before reconstruction — rules engine runs before any key material is touched
3. Direct upstream call — reconstructed key never transits the network

---

## License

AGPL-3.0. See [LICENSE](LICENSE).

Commercial dual-licensing available for enterprises requiring proprietary distribution. Contact [hello@worthless.cloud](mailto:hello@worthless.cloud).

---

## Why AGPL?

Anyone who builds a competing managed service on top of Worthless open source must open source their entire stack. This protects the community that builds on this foundation.

---

*"A kill switch, not an alert."*
