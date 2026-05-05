# Installing worthless

Pick the platform that matches your setup. Each guide is end-to-end —
from "I have nothing installed" to "my LLM API calls go through the
proxy." Every step you'll type, every popup you'll see, every `.env`
line that changes.

| Platform | Guide | Time to working proxy |
|---|---|---|
| **macOS** (Apple Silicon or Intel) | [mac.md](./mac.md) | ~90 seconds |
| **Linux** (Ubuntu / Debian / Alpine) | [linux.md](./linux.md) | ~60 seconds |
| **Windows + WSL2** | [wsl.md](./wsl.md) | ~90 seconds |
| **Docker** (your app runs in a container) | [docker.md](./docker.md) | ~5 minutes (one-time per project) |

Worthless is **always installed natively on your host**, even if your
app runs in Docker. The Docker image is for self-hosting a worthless
*server* for a team — not for running the CLI. See
[docker.md](./docker.md) for the full explanation.

## Common to all platforms

What worthless does to your project after `worthless lock`:

| Before | After |
|---|---|
| `OPENAI_API_KEY=<your-real-key-here>` | `OPENAI_API_KEY=<decoy-prefix>...` (useless on its own) |
| (no `OPENAI_BASE_URL`) | `OPENAI_BASE_URL=http://127.0.0.1:8787/openai-<alias>/v1` |

Your app code stays the same. The OpenAI/Anthropic SDK reads
`OPENAI_BASE_URL` and routes through the proxy automatically.
The proxy reconstructs the real key only when the request passes
the spend-cap gate.

## Known limitations as of v0.3.3

Honest list — these are tracked, not unknown.

| Limitation | Tracked as |
|---|---|
| Proxy doesn't auto-restart on reboot — you must run `worthless up` after every boot | WOR-174 (macOS launchd) + WOR-175 (Linux systemd), v1.1 |
| Docker containers can't reach `127.0.0.1:8787` from inside — you edit `.env` to use `host.docker.internal:8787` instead | filed as v1.2 work |
| `worthless up &` may exit prematurely instead of staying attached | `worthless-n8tj`, v0.3.4 |
| Stale orphan proxy can confuse `worthless up` PID detection | `worthless-6gkb`, v0.3.4 |
| `uv tool uninstall worthless` doesn't purge the keychain entry or `~/.worthless/` | WOR-435, v1.2 |
| No `worthless` CLI is exposed by `docker run ghcr.io/.../worthless` — you still install natively | by design; documented in [docker.md](./docker.md) |

If you hit something that isn't on this list, file a Github issue.
