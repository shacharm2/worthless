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

### Integrity check (transit, not origin)

Piping a script from the internet into `sh` is a supply-chain risk. The
Worker emits an `X-Worthless-Script-Sha256` header so you can confirm
the saved file matches what the Worker advertised at fetch time:

```bash
# 1. Download.
curl -sSL https://worthless.sh -o install.sh

# 2. Body sha matches the header advertised by the Worker.
echo "$(curl -sSI https://worthless.sh | grep -i 'x-worthless-script-sha256' | awk '{print $2}' | tr -d '\r')  install.sh" \
  | sha256sum -c -

# 3. (Optional) read it.
less install.sh

# 4. Run.
sh install.sh
```

**What this catches:** post-download local-file tampering (between
`curl -o install.sh` and `sh install.sh`), CDN cache poisoning, and
Worker regressions that detach the served body from the advertised
header.

**What this does NOT catch:** transit MITM (TLS + HSTS already
prevent that — the header/body match check would be redundant if
that were the only threat), or a compromised origin. The header and
the body come from the same Worker; an attacker who controls the
response controls both, so the sha check is **not origin
attestation**. Real cryptographic origin attestation lands with
cosign-signed release manifests — tracked in
[WOR-303](https://linear.app/plumbusai/issue/WOR-303).

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

- [Security model](docs/security.md) -- threat model, invariants, known limitations
- [Engineering docs](engineering/README.md) -- internal developer documentation for the live codebase
- [Engineering architecture](engineering/architecture.md) -- current internal architecture overview
- [Contributor security rules](CONTRIBUTING-security.md) -- invariants all contributions must preserve
- [SKILL.md](SKILL.md) -- agent discovery file

## Contributing

PRs welcome. Read [CONTRIBUTING-security.md](CONTRIBUTING-security.md) first.

## License

[AGPL-3.0](LICENSE)
