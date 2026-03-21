# Worthless

**We make your API keys worthless to steal.**

Your API key is split into two shards using XOR secret sharing. One half stays on your machine. One half is encrypted at rest on the proxy. Neither half alone reveals anything about the key — this is information-theoretic security, not just encryption.

The key only reconstructs in memory, server-side, for the duration of a single upstream API call — then zeroed.

```
Your code → Worthless proxy → [cap check] → [key reconstructs] → OpenAI / Anthropic
                                    ↓
                              If cap hit: stops here.
                              Key never forms.
```

---

## Install

- [Solo developer](docs/install-solo.md)
- [Claude Code / Cursor / Windsurf (MCP)](docs/install-mcp.md)
- [OpenClaw](docs/install-openclaw.md)
- [Self-hosted](docs/install-self-hosted.md)
- [Teams](docs/install-teams.md)
- [From source (development)](#from-source)

---

## From Source

**Requires Python 3.12+**

```bash
git clone https://github.com/shacharm2/worthless && cd worthless
uv sync --extra dev --extra test
```

### Enroll an API key

```python
import asyncio
from pathlib import Path
from cryptography.fernet import Fernet
from worthless.cli.enroll_stub import enroll_stub

fernet_key = Fernet.generate_key()
print(f"Save this key: {fernet_key.decode()}")

asyncio.run(enroll_stub(
    alias="my-openai",
    api_key="sk-your-openai-key-here",
    provider="openai",
    db_path=str(Path.home() / ".worthless" / "worthless.db"),
    fernet_key=fernet_key,
    shard_a_dir=str(Path.home() / ".worthless" / "shard_a"),
))
```

### Start the proxy

```bash
# DEV ONLY — never use WORTHLESS_ALLOW_INSECURE in production
WORTHLESS_FERNET_KEY="<paste key printed above>" WORTHLESS_ALLOW_INSECURE=true \
  uv run uvicorn worthless.proxy.app:create_app --factory --port 8443
```

### Use it

```bash
curl http://localhost:8443/v1/chat/completions \
  -H "x-worthless-alias: my-openai" \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4", "messages": [{"role": "user", "content": "hello"}]}'
```

### Health check

```bash
curl http://localhost:8443/healthz   # liveness
curl http://localhost:8443/readyz    # ready (DB connected, keys enrolled)
```

---

## How It Works

```
Client                     Proxy                        Provider
  │                          │                             │
  │  x-worthless-alias       │                             │
  │  x-worthless-shard-a     │                             │
  │ ──────────────────────►  │                             │
  │                          │  1. Authenticate (alias)    │
  │                          │  2. Gate (rules engine)     │
  │                          │  3. Reconstruct key (XOR)   │
  │                          │  4. Upstream call ──────────►│
  │                          │  5. Zero key from memory    │
  │                    ◄──── │  6. Relay response    ◄──── │
```

**Key invariant:** Step 2 (gate) always runs before step 3 (reconstruct). If the rules engine denies the request, the key is never reconstructed. Zero key material touched.

---

## What Worthless Protects

- ✅ API key stolen from GitHub, `.env` file, or client-side JS
- ✅ Agent or script running a billing loop overnight
- ✅ Contractor or team member exceeding their budget
- ✅ Stolen key used by an attacker to rack up charges

## What Worthless Does Not Protect

- ❌ Full machine compromise (same boundary as 1Password)
- ❌ Upstream LLM provider outages
- ❌ Content safety or prompt injection

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `WORTHLESS_FERNET_KEY` | *(required)* | Fernet key for encrypting Shard B at rest |
| `WORTHLESS_DB_PATH` | `~/.worthless/worthless.db` | SQLite database path |
| `WORTHLESS_SHARD_A_DIR` | `~/.worthless/shard_a` | Directory for file-based Shard A loading |
| `WORTHLESS_RATE_LIMIT_RPS` | `100.0` | Default rate limit (requests/second per IP) |
| `WORTHLESS_UPSTREAM_TIMEOUT` | `120.0` | Upstream timeout for non-streaming (seconds) |
| `WORTHLESS_STREAMING_TIMEOUT` | `300.0` | Upstream timeout for streaming (seconds) |
| `WORTHLESS_ALLOW_INSECURE` | `false` | Allow shard headers over non-TLS (dev only) |

---

## Providers

| Provider | Endpoint | Status |
|----------|----------|--------|
| OpenAI | `/v1/chat/completions` | ✅ Streaming + non-streaming |
| Anthropic | `/v1/messages` | ✅ Streaming + non-streaming |

---

## Security Model

Three architectural invariants:

1. **Gate before reconstruct** — The rules engine evaluates every request before Shard B is decrypted. Denied requests never touch key material.
2. **Transparent routing** — Setting `BASE_URL` to the proxy causes API calls to route through it. The proxy is invisible to provider SDKs.
3. **Server-side only** — The reconstructed key is used for the upstream call and never appears in any response.

All auth failures return an identical `401` body to prevent key enumeration.

See [docs/security-model.md](docs/security-model.md) for the full threat model and known limitations.

---

## Architecture

```
src/worthless/
├── crypto/          # XOR splitting, HMAC commitment, memory zeroing
├── adapters/        # Provider request/response transforms, SSE relay
├── proxy/           # FastAPI proxy, rules engine, metering
├── storage/         # Encrypted shard persistence (Fernet + SQLite)
└── cli/             # Enrollment stub (more commands coming)
```

---

## Development

```bash
uv sync --extra dev --extra test --extra qa
uv run pytest              # full suite
uv run ruff check .        # lint
```

---

## Contributing

PRs welcome. Any PR that violates the three architectural invariants will be closed regardless of other merits. Read [docs/security-model.md](docs/security-model.md) first.

---

## License

AGPL-3.0. See [LICENSE](LICENSE).

---

*"A kill switch, not an alert."*
