# Install — OpenClaw

Worthless integrates with OpenClaw automatically when you run `worthless lock`. No separate install step, no MCP server required.

## How it works

`worthless lock` does three things in one shot:

1. Splits your API key and writes Shard A into `.env`
2. Injects a `worthless-<provider>` entry into `~/.openclaw/openclaw.json`
3. Installs the Worthless skill into `~/.openclaw/workspace/skills/worthless/`

After that, every request OpenClaw makes routes through the Worthless proxy — spend cap enforced, key never exposed.

## Quick start (local)

```bash
# Lock your key — OpenClaw config updated automatically
worthless lock
```

That's it. Restart OpenClaw and it picks up the new provider.

## Docker / container setup

If OpenClaw runs in a container and the Worthless proxy runs on your host machine, the container needs to reach the host. Set two env vars before locking:

```bash
# Bind the proxy to all interfaces (not just loopback)
export WORTHLESS_HOST=0.0.0.0

# Tell lock to write host.docker.internal into openclaw.json
export WORTHLESS_PROXY_HOST=host.docker.internal

worthless lock
```

Then start OpenClaw with access to your config:

```bash
docker run --rm -it \
  --add-host=host.docker.internal:host-gateway \
  -v "$HOME/.openclaw:/home/node/.openclaw" \
  ghcr.io/openclaw/openclaw:latest \
  openclaw agent --local --message "hello"
```

> `--add-host=host.docker.internal:host-gateway` is the Linux equivalent of Docker Desktop's built-in `host.docker.internal`. On macOS with Docker Desktop, it resolves automatically.

## Verify it's working

```bash
worthless status
```

```
Enrolled keys:
  openai-a1b2c3d4  openai  PROTECTED

OpenClaw:
  ~/.openclaw/openclaw.json — worthless-openai ✓
  ~/.openclaw/workspace/skills/worthless/ — installed ✓

Proxy: running on 127.0.0.1:8787
```

`PROTECTED` and both OpenClaw entries present means your key is enrolled and OpenClaw is wired to the proxy. Restart OpenClaw and send a request — the proxied request count in `worthless status` will increment.

## Undo

```bash
worthless unlock
```

Removes the `worthless-<provider>` entries from `openclaw.json` and uninstalls the skill folder. Your original providers are untouched.
