# Worthless

**Make leaked API keys worthless.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-green)](LICENSE)
[![Tests](https://github.com/shacharm2/worthless/actions/workflows/tests.yml/badge.svg)](https://github.com/shacharm2/worthless/actions/workflows/tests.yml)

Your API key is split in two. Neither half works alone. The proxy enforces a hard spend cap **before** the key reconstructs — blow the budget, the key never forms, the request never leaves your machine.

## Quickstart

```bash
curl -sSL https://worthless.sh | sh
```

Then `cd` into your project and run `worthless`. It detects keys in your `.env`, splits them, starts a local proxy. No code changes.

Full install options (pipx, Docker, MCP for Claude Code / Cursor / Windsurf, GitHub Actions, signed-installer verification): **[docs.wless.io](https://docs.wless.io)**

## How it works

1. `worthless lock` splits each API key into two shards
2. Shard A stays on your machine (encrypted). Shard B goes to the proxy database
3. Your `.env` is rewritten with shard A — format-preserving, but cryptographically useless alone
4. The proxy reconstructs the key only when the rules engine approves the request
5. Spend cap blown? The key never forms. The request never reaches the provider

## Platforms

| Platform | Status |
|---|---|
| macOS | Supported |
| Linux | Supported |
| Windows + WSL | Supported |
| Native Windows | Not supported — use WSL or Docker |

Native-Windows support is tracked in [WOR-237](https://linear.app/plumbusai/issue/WOR-237/v12-native-windows-support-stdin-fernet-job-objects-detached-process). See [docs.wless.io](https://docs.wless.io) for the full distro support matrix.

## Documentation

Everything lives at **[docs.wless.io](https://docs.wless.io)** — install guides, the security model, wire protocol, recovery runbook, and the agent skill file.

## Development

```bash
git clone https://github.com/shacharm2/worthless && cd worthless
uv sync --extra dev --extra test
uv run pytest
```

Internal developer documentation lives in [`engineering/`](engineering/). Security invariants are in [`SECURITY.md`](SECURITY.md).

## Contributing

PRs welcome. Read [CONTRIBUTING-security.md](CONTRIBUTING-security.md) first.

## License

[AGPL-3.0](LICENSE)
