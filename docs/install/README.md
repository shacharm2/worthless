---
title: "Install — Pick Your Platform"
description: "Zero-to-working-proxy install guides for macOS, Linux, WSL2, and Docker."
---

# Installing worthless

| Platform | Guide | Time to working proxy |
|---|---|---|
| **macOS** (Apple Silicon or Intel) | [mac.md](./mac.md) | ~2 min on a fast network |
| **Linux** (Ubuntu / Debian / Alpine) | [linux.md](./linux.md) | ~1-2 min on a fast network |
| **Windows + WSL2** | [wsl.md](./wsl.md) | ~2-3 min on a fast network |
| **Docker** (your app runs in a container) | [docker.md](./docker.md) | ~5 min (one-time per project) |

> **Note:** Worthless is **always installed natively on your host**,
> even if your app runs in Docker. The Docker image is for self-hosting
> a worthless *server* for a team — not for running the CLI. See
> [docker.md](./docker.md) for the full explanation.

*Behind a corporate proxy or with cold uv caches, add 1-2 min for the
PyPI fetch.*

## Common to all platforms

### What `worthless lock` does to your `.env`

| Before | After |
|---|---|
| `OPENAI_API_KEY=<your-real-key-here>` | `OPENAI_API_KEY=<decoy-prefix>...` (useless on its own) |
| (no `OPENAI_BASE_URL`) | `OPENAI_BASE_URL=http://127.0.0.1:8787/<alias>/v1` |

`<alias>` is a per-key identifier worthless prints during `lock` (e.g.
`openai-bab71e6a`). The proxy infers the upstream provider from the alias
itself, so the URL is provider-neutral.

Your app code stays the same. The OpenAI/Anthropic SDK reads
`OPENAI_BASE_URL` and routes through the proxy automatically. The proxy
reconstructs the real key only when the request passes the spend-cap
gate.

### Verify it works

After locking on any platform, run your app's normal SDK path:

```python
# verify.py
from openai import OpenAI
client = OpenAI()                  # picks up OPENAI_BASE_URL from .env
print(client.models.list().data[0].id)
```

```bash
python verify.py
# → prints e.g. "gpt-4o-mini"
```

If you see a model id, the proxy reconstructed your key, hit the
upstream provider, and returned the response — without your real key
ever leaving the proxy process.

> **Never put your real API key on a shell command line.** That's the
> exact exfiltration worthless protects against. Use the SDK pattern
> above; it reads from `.env` at the right boundary.

## Known limitations as of v0.3.3

Honest list — these are tracked, not unknown.

| Limitation | Tracked as |
|---|---|
| Proxy doesn't auto-restart on reboot — you must run `worthless up` after every boot | WOR-174 (macOS launchd) + WOR-175 (Linux systemd), v1.1 |
| Docker containers can't reach `127.0.0.1:8787` from inside — edit `.env` to use `host.docker.internal:8787` | v1.2 work |
| `worthless up &` may exit prematurely instead of staying attached | `worthless-n8tj`, v0.3.4 |
| Stale orphan proxy can confuse `worthless up` PID detection | `worthless-6gkb`, v0.3.4 |
| `uv tool uninstall worthless` doesn't purge the keychain entry or `~/.worthless/` | WOR-435, v1.2 |
| No `worthless` CLI is exposed by `docker run ghcr.io/.../worthless` — you still install natively | by design; see [docker.md](./docker.md) |

If you hit something that isn't on this list, file a GitHub issue.

## For AI agents installing on a user's behalf

The bottom of each platform guide carries a `## For AI agents` section
with a fenced YAML block. The schema is intentionally small — only what
an agent needs to install + verify + recognize known failure modes
without parsing prose. Schema:

```yaml
schema_version: 1
platform: macos | linux | wsl | docker
commands:
  install: <shell command, runs once>
  verify: <shell command, runs after install>
  first_lock: <shell command, runs in a project dir with .env>
  proxy_restart: <shell command, runs after reboot or manual stop>
expectations:
  install_succeeds_silently: <bool — does install produce no popups>
  first_lock_keychain_popups: <int — how many popups on first lock>
  subsequent_command_keychain_popups: <int — should be 0>
  proxy_starts_automatically_on_lock: <bool>
  proxy_survives_reboot: <bool — currently false on every platform>
proxy:
  url_template: <string with <alias> placeholder>
  port: <int>
limitations:
  - <one-line text reference to a tracked ticket>
```

Agents should treat the human prose above each `## For AI agents` block
as background context, and the YAML as the actionable surface. SKILL.md
points agents at this entry path.

Schema versioning: bump `schema_version` on any breaking change to the
keys; additive changes (new optional keys) keep the version. Agents are
expected to tolerate unknown keys.
