# Install with Docker

Docker is **not a way to run the worthless CLI**. It's a way to run a
worthless **server** for self-hosting (team mode). The CLI is always
installed natively on your host. This is the most-misunderstood part
of worthless and the source of every "Docker integration" question.

## TL;DR — pick your scenario

| Your setup | What to do |
|---|---|
| **Solo dev. App runs natively. No Docker.** | Use [mac.md](./mac.md) / [linux.md](./linux.md) / [wsl.md](./wsl.md) — Docker irrelevant |
| **Solo dev. App runs in a Docker container. worthless on host.** | This guide, §"Scenario A" |
| **Solo dev. Want both worthless + app inside the same Docker Compose stack.** | This guide, §"Scenario B" |
| **Team. Want a shared worthless server everyone points at.** | This guide, §"Scenario C" |

## What the Docker image actually is

`ghcr.io/shacharm2/worthless-proxy:0.3.3` packages worthless as a
**server** (proxy + control plane in one container). You run it like
any other service:

```bash
docker run -d -p 8787:8787 ghcr.io/shacharm2/worthless-proxy:0.3.3
```

That gives you a worthless **server** on port 8787. It does NOT give
you a `worthless` command on your terminal. To lock keys you still
need the CLI on your host.

## Scenario A — your app in Docker, worthless on host

Most common. Your app's Dockerfile / docker-compose.yml runs your
service in a container. worthless lives on your host.

### A.1 Install worthless natively on your host

Follow [mac.md](./mac.md) / [linux.md](./linux.md) / [wsl.md](./wsl.md)
end-to-end. After this you have `worthless` in your shell PATH and a
proxy running on host's `127.0.0.1:8787`.

### A.2 Lock the keys

```bash
cd /path/to/your/project
worthless
```

This rewrites your `.env`:

```diff
- OPENAI_API_KEY=<your-real-openai-key-here>
+ OPENAI_API_KEY=<decoy-prefix>...
+ OPENAI_BASE_URL=http://127.0.0.1:8787/openai-<alias>/v1
```

### A.3 Fix the URL for the container

**This is the gotcha.** From inside a container, `127.0.0.1` means
"the container itself" — not "the host." Your container can't reach
the host's port 8787 via that address.

Edit `.env` to use Docker's host-bridge address:

| Platform | Replace `127.0.0.1` with |
|---|---|
| Docker Desktop (Mac, Windows, WSL2) | `host.docker.internal` |
| Docker on Linux (no Desktop) | Add `--add-host=host.docker.internal:host-gateway` to your `docker run` (or to the service in docker-compose), then use `host.docker.internal` |

After edit:

```bash
# .env
OPENAI_API_KEY=<decoy-prefix>...
OPENAI_BASE_URL=http://host.docker.internal:8787/openai-<alias>/v1
```

### A.4 Mount the .env into the container

Your `docker-compose.yml` (or `docker run`) needs to volume-mount the
`.env` so the container reads the rewritten file:

```yaml
services:
  app:
    image: my-app:latest
    env_file:
      - .env
    extra_hosts:                   # ONLY needed on Linux (no Docker Desktop)
      - "host.docker.internal:host-gateway"
```

### A.5 Verify

From inside the container:

```bash
docker compose exec app curl -s \
  "http://host.docker.internal:8787/openai-<alias>/v1/models" \
  -H "Authorization: Bearer <decoy-prefix>..."
```

Expected: JSON list of models.

> Today's pain: `worthless lock` writes `127.0.0.1` blindly. You edit
> the `.env` after locking. A future version will detect Docker
> context or accept a `--docker-host` flag — tracked in v1.2.

## Scenario B — both worthless and app in the same Compose stack

If you want everything containerized (e.g., for reproducibility),
add the worthless server as a service:

```yaml
# docker-compose.yml
services:
  worthless:
    image: ghcr.io/shacharm2/worthless-proxy:0.3.3
    ports:
      - "8787:8787"   # host:container — exposes for CLI lock-from-host
    environment:
      WORTHLESS_DEPLOY_MODE: lan   # safe default for compose network
    volumes:
      - worthless-data:/data       # persists DB + shard storage

  app:
    image: my-app:latest
    env_file:
      - .env
    depends_on:
      - worthless
    # In compose, services reach each other by name. From `app`,
    # the proxy is at http://worthless:8787 — NOT host.docker.internal.

volumes:
  worthless-data:
```

### B.1 Lock from host (CLI on host targets the compose-side proxy)

```bash
# Tell the host CLI where the proxy lives
export WORTHLESS_PROXY_URL=http://127.0.0.1:8787   # since compose mapped it
worthless
```

This locks against the worthless container. `.env` gets rewritten with
the URL the **container** will use:

```diff
- OPENAI_API_KEY=<your-real-openai-key-here>
+ OPENAI_API_KEY=<decoy-prefix>...
+ OPENAI_BASE_URL=http://worthless:8787/openai-<alias>/v1
```

(Compose service name `worthless` resolves inside the network. The
host CLI lock writes the *container-resolvable* URL.)

> Note: today's CLI doesn't auto-detect compose context. You may need
> to `sed -i 's/127.0.0.1/worthless/' .env` after locking. Tracked in
> the same v1.2 bead as Scenario A.

## Scenario C — team server, multiple devs

Run a single shared worthless instance, multiple devs point their CLI
at it. Each dev's `worthless lock` enrolls keys against the team
server.

```yaml
# docker-compose.yml on the team server box
services:
  worthless:
    image: ghcr.io/shacharm2/worthless-proxy:0.3.3
    ports:
      - "443:8787"
    environment:
      WORTHLESS_DEPLOY_MODE: public           # exposes to internet
      WORTHLESS_TRUSTED_PROXIES: "10.0.0.0/8" # your VPC CIDR
    volumes:
      - worthless-data:/data
    # plus TLS termination — Caddy/nginx reverse proxy in front
```

Each dev:

```bash
export WORTHLESS_PROXY_URL=https://worthless.your-team.example.com
worthless
# .env gets URL = https://worthless.your-team.example.com/openai-<alias>/v1
```

Team mode is **not v1 scope** for self-hosting docs — wait for v1.1
launch (per WOR-300/388) for the full team-server runbook.

## Common failures

| Symptom | Cause | Fix |
|---|---|---|
| `worthless` not found in shell | You ran `docker run worthless` thinking that's the CLI | Install natively per [mac.md](./mac.md) / [linux.md](./linux.md) |
| App in container: "connection refused" on `127.0.0.1:8787` | `127.0.0.1` from container = container itself | Use `host.docker.internal` (§A.3) |
| Linux: `host.docker.internal` doesn't resolve | No Docker Desktop = no auto host-gateway | Add `--add-host=host.docker.internal:host-gateway` to `docker run` |
| Compose: `worthless:8787` doesn't resolve | Service not in same compose network | Check `docker compose ps` — both must be on the default network |
| Proxy on host responds but every request returns 502 | Proxy can't reach upstream — DNS / network from host | Test with `curl https://api.openai.com/v1/models` from host |
| Deploy mode mismatch warnings on container start | `WORTHLESS_DEPLOY_MODE` not set, defaults to loopback, but you exposed a port | Set `WORTHLESS_DEPLOY_MODE=lan` (or `public` with trusted proxies) |

## What worthless does NOT defend against in Docker setups

- Container escape. If your container runs as root with `--privileged`
  or mounts the host filesystem, attacker-with-container = attacker-
  with-host = full read of `~/.worthless/`.
- Compose secret leakage via env_file. `.env` mounted into the
  container is readable by anything in the container. shard A is
  decoy — but if your container is compromised, attacker has shard A
  and the proxy URL. They still can't reconstruct without server-side
  shard B + cap gate, but the audit log shows the request flow.
- Image supply chain. Use the cosign-signed image:
  ```
  cosign verify ghcr.io/shacharm2/worthless-proxy:0.3.3 \
    --certificate-identity-regexp "^https://github.com/shacharm2/worthless/.github/workflows/publish-docker.yml@refs/tags/v" \
    --certificate-oidc-issuer "https://token.actions.githubusercontent.com"
  ```

## Why this is more complicated than mac/linux/wsl

Because there are three legitimate setups (host-CLI + container-app,
single compose stack, team-server) and the URL semantics differ across
all three. The CLI doesn't auto-detect which one you're in — you tell
it (or accept the default and edit `.env`).

The v1.2 work tracked under "worthless lock detects Docker context"
will collapse some of this, but won't fully replace the need to
understand which scenario you're in.
