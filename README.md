# Worthless

**Make leaked API keys worthless.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-green)](LICENSE)
[![Tests](https://github.com/shacharm2/worthless/actions/workflows/tests.yml/badge.svg)](https://github.com/shacharm2/worthless/actions/workflows/tests.yml)

Your API key is split in two. Neither half works alone.
The proxy enforces a hard spend cap **before** the key reconstructs — blow the budget, the key never forms, the request never leaves your machine.

## Quickstart

```bash
curl -sSL https://worthless.sh | sh        # fresh machine — no Python needed
# or, if you already have Python 3.10+:
pipx install worthless
```

Then, in your project:

```bash
cd your-project
worthless
```

Detects keys in your `.env`, splits them, starts a local proxy. No code changes.

### Verify before running

Piping a script from the internet into `sh` is a supply-chain risk. Read it first:

```bash
curl -sSL https://worthless.sh -o install.sh
less install.sh                                   # inspect, then run
sh install.sh
```

See [docs/install-security.md](docs/install-security.md) for trust roots
(what the installer talks to and what it verifies) and the kill-switch
runbook.

```
$ worthless

  Found 2 API keys:
    OPENAI_API_KEY      openai
    ANTHROPIC_API_KEY   anthropic

  Lock these keys? [y/N] y
    OPENAI_API_KEY      locked
    ANTHROPIC_API_KEY   locked

  Proxy ready on 127.0.0.1:8787
```

## How it works

1. `worthless lock` splits each API key into two shards using XOR
2. Shard A stays on your machine (encrypted). Shard B goes to the proxy database.
3. Your `.env` is rewritten with shard-A (format-preserving — looks like a real key but is cryptographically useless alone)
4. The proxy reconstructs the key only when the rules engine approves the request
5. Spend cap blown? The key never forms. The request never reaches the provider.

## Commands

```bash
worthless              # Auto-detect, lock, start proxy (the magic)
worthless lock         # Lock keys in .env
worthless unlock       # Restore original keys
worthless scan         # Detect exposed keys without locking
worthless status       # Show proxy and key status
worthless up           # Start proxy (foreground)
worthless up -d        # Start proxy (background daemon)
worthless down         # Stop the proxy
worthless wrap <cmd>   # Run a command through the proxy
worthless revoke       # Revoke enrolled keys
```

## Docker

`docker run ghcr.io/shacharm2/worthless-proxy:<version>` — multi-arch, vulnerability-scanned, cosign-signed. See [docs/install-docker.md](docs/install-docker.md).

## Platforms

Worthless runs on POSIX hosts. The proxy relies on `setsid`, `os.killpg`,
fd-based key transport, and signal-group shutdown — primitives that have no
reliable native-Windows equivalent. Rather than degrade silently, the CLI
refuses to start on native Windows and tells you how to run it under WSL or
Docker.

| Platform | Status |
|---|---|
| macOS | Supported |
| Linux | Supported |
| Windows + WSL | Supported |
| Windows + Docker | Supported |
| Native Windows | Not supported — `up`, `wrap`, and the default command exit with `WRTLS-110`. `down` is allowed so existing state can be cleaned up. |

`WORTHLESS_WINDOWS_ACK=1` suppresses the soft warning on `down`; it does not
bypass the hard gate on the other entry points.

**Planned:** native-Windows support (stdin Fernet key transport, Windows Job
Objects, `DETACHED_PROCESS`) is tracked in
[WOR-237](https://linear.app/plumbusai/issue/WOR-237/v12-native-windows-support-stdin-fernet-job-objects-detached-process) —
target v1.2. If you need it sooner or want to help, comment on the issue
rather than patching around the guard locally.

### `install.sh` / `worthless.sh` support matrix

`curl worthless.sh | sh` bootstraps uv and worthless with no Python required
on the target host. Coverage (run via `pytest -m docker`):

| Host | Status | Notes |
|---|---|---|
| Ubuntu 24.04 (bare) | Supported | no python, no uv |
| Ubuntu 22.04 (bare) | Supported | still the LTS most prod/CI boxes run |
| Ubuntu 24.04 + pre-installed `uv` | Supported | asserts uv is reused, not reinstalled (sha256 check) |
| Debian 12 (bare) | Supported | second glibc distro |
| Alpine / musl | Supported | uv fetches musl-compatible Python via PBS; `zstd` required for modern tarballs |
| macOS (Intel / ARM) | Supported | manual test on dev boxes |
| Fedora / RHEL | Untested | — |
| Windows + WSL | Untested (expected to work) | — |
| Native Windows | Not supported | see Platforms section |

All distros are pinned to `linux/amd64` so arm64 hosts still exercise amd64
coverage. Per-distro verification runs `verify_install.sh` — checks resolved
binary path, `--version`, `--help` (exercises lazy imports), and scans stderr
for `Traceback` / `ModuleNotFoundError`.

## Undo everything

```console
$ worthless unlock
1 key(s) restored.
```

Original key is back. No trace.

## Pre-commit hook

```yaml
repos:
  - repo: https://github.com/shacharm2/worthless
    rev: main
    hooks:
      - id: worthless-scan
```

## For AI coding agents

Add to your project's `.mcp.json` (Node ≥ 18, no Python needed upfront):

```json
{
  "mcpServers": {
    "worthless": {
      "command": "npx",
      "args": ["-y", "worthless-mcp"]
    }
  }
}
```

Restart Claude Code / Cursor / Windsurf — MCP tools appear immediately. On first run, `worthless-mcp` bootstraps `uv` and installs the Python package automatically. Install time < 30 s.

Available tools: `worthless_status`, `worthless_lock`, `worthless_scan`, `worthless_spend`.

See [SKILL.md](SKILL.md) for the full agent discovery file.

## Development

```bash
git clone https://github.com/shacharm2/worthless && cd worthless
uv sync --extra dev --extra test
uv run pytest
```

## Learn more

- [Security model](docs/security.md) -- threat model, invariants, known limitations
- [Engineering docs](engineering/README.md) -- internal developer documentation for the live codebase
- [Engineering architecture](engineering/architecture.md) -- current internal architecture overview
- [Contributor security rules](CONTRIBUTING-security.md) -- invariants all contributions must preserve
- [SKILL.md](SKILL.md) -- agent discovery file

## Contributing

PRs welcome. Read [CONTRIBUTING-security.md](CONTRIBUTING-security.md) first.

## License

[AGPL-3.0](LICENSE)
