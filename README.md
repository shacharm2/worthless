# Worthless

**Make leaked API keys worthless.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-green)](LICENSE)
[![Tests](https://github.com/shacharm2/worthless/actions/workflows/tests.yml/badge.svg)](https://github.com/shacharm2/worthless/actions/workflows/tests.yml)

Your API key is split in two. Neither half works alone.
The proxy enforces a hard spend cap **before** the key reconstructs — blow the budget, the key never forms, the request never leaves your machine.

## Quickstart

```bash
pipx install worthless   # or: pip install worthless
cd your-project
worthless
```

Detects keys in your `.env`, splits them, starts a local proxy. No code changes.

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
bypass the hard gate on the other entry points. If you need native-Windows
support, please open an issue rather than patching around the guard — the
process-lifecycle work is tracked but deliberately out of V1 scope.

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

Worthless ships a `SKILL.md` that tells Claude Code, Cursor, and Windsurf what commands are available. Agents use `--json` for structured output:

```bash
worthless status --json
```

## Development

```bash
git clone https://github.com/shacharm2/worthless && cd worthless
uv sync --extra dev --extra test
uv run pytest
```

## Learn more

- [Security model](docs/security-model.md) -- how the split-key proxy works
- [Security rules](SECURITY_RULES.md) -- invariants all contributions must preserve
- [SKILL.md](SKILL.md) -- agent discovery file

## Contributing

PRs welcome. Read [SECURITY_RULES.md](SECURITY_RULES.md) first.

## License

[AGPL-3.0](LICENSE)
