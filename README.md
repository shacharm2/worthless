# Worthless

**Make your API keys worthless to steal.**

![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-green)
![Status: Pre-release](https://img.shields.io/badge/status-pre--release-orange)
![Tests: passing](https://img.shields.io/badge/tests-passing-brightgreen)

Your API key is split into two shards. One stays on your machine. One is encrypted on the proxy. Neither half reveals the key -- this is information-theoretic security, not just encryption. The key only reconstructs in memory for a single API call, then is zeroed. If the spend cap is hit, the key never forms at all.

> [!NOTE]
> **Pre-release software.** Worthless is under active development. The CLI works
> for local development. Hosted proxy, PyPI package, and one-click deploys are
> coming. See [SECURITY_RULES.md](SECURITY_RULES.md) for crypto constraints.

## Quickstart

```bash
git clone https://github.com/shacharm2/worthless && cd worthless
uv pip install -e .
```

Create a `.env` with an API key, then lock it:

```console
$ worthless lock
1 key(s) protected.
```

Your `.env` now contains a decoy -- format-correct, but cryptographically useless. Run your code through the proxy:

```console
$ worthless wrap python -c "import os; print(os.environ.get('OPENAI_BASE_URL', 'not set'))"
OPENAI_BASE_URL=http://127.0.0.1:51799
```

`wrap` started an ephemeral proxy, injected `OPENAI_BASE_URL` so your SDK routes through it, ran your command, and cleaned up. Your code didn't change.

Check what's protected:

```console
$ worthless status
Enrolled keys:
  openai-69ccc444  openai  PROTECTED

Proxy: not running
```

### Undo everything

```console
$ worthless unlock
1 key(s) restored.
```

Your original key is back in `.env`. No shards, no proxy, no trace.

## What just happened

`worthless wrap` is shorthand for:

```bash
worthless up                    # start proxy on localhost:8787
export OPENAI_BASE_URL=http://127.0.0.1:8787
python your_app.py              # SDK calls route through proxy
```

The proxy holds Shard B (encrypted). Your machine holds Shard A. On each API call, the proxy XOR-reconstructs the real key in memory, makes the upstream call, and zeros the key. If the spend cap fires, reconstruction never happens.

## How it works

```
  .env (decoy)     Your machine         Proxy              Provider
       |               |                  |                    |
       |          Shard A (file)    Shard B (encrypted)        |
       |               |                  |                    |
       |               +--- XOR merge --->|                    |
       |                           real key (in memory)        |
       |                                  |--- API call ------>|
       |                           key zeroed                  |
       |                                  |<-- response -------|
```

**Key invariant:** The rules engine (rate limit, spend cap) evaluates *before* the key is reconstructed. If denied, zero key material is touched.

## CLI reference

| Command | Description |
|---------|-------------|
| `worthless lock` | Scan `.env`, split keys, replace with decoys |
| `worthless unlock` | Restore original keys from shards |
| `worthless wrap CMD` | Ephemeral proxy + run CMD with injected `BASE_URL` |
| `worthless up` | Start proxy on port 8787 (foreground) |
| `worthless up -d` | Start proxy in daemon mode |
| `worthless status` | Show enrolled keys and proxy health |
| `worthless scan` | Detect exposed API keys in files |
| `worthless enroll` | Enroll a single key (scripting/CI primitive) |

## Positioning

Every secrets tool protects the key until your app gets it. Worthless protects you after it leaks.

## What Worthless does NOT protect against

- Memory inspection on a fully compromised host (same boundary as any process-level secret)
- Supply-chain attacks that modify Worthless itself
- Keys already leaked before locking -- lock your keys *before* they're exposed
- Upstream provider outages or content safety

## Security

Cryptographic primitives: XOR secret sharing, HMAC-SHA256 commitment. No novel cryptography -- standard constructions only. Shard B is encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256).

**No independent security audit has been performed yet.** See [SECURITY_RULES.md](SECURITY_RULES.md) for the crypto invariants that all contributions must preserve.

## Pre-commit hook

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/shacharm2/worthless
    rev: main
    hooks:
      - id: worthless-scan
```

Catches unprotected API keys before they reach git history.

## Development

```bash
git clone https://github.com/shacharm2/worthless && cd worthless
uv sync --extra dev --extra test
uv run pytest
uv run ruff check .
```

## Contributing

PRs welcome. Any PR that violates the three security invariants (gate-before-reconstruct, transparent routing, server-side only) will be closed regardless of other merits. Read [SECURITY_RULES.md](SECURITY_RULES.md) before touching crypto code.

## License

AGPL-3.0. See [LICENSE](LICENSE).
