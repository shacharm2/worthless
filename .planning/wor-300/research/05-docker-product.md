# Docker Install Path — Product Decision

Source: product-manager pass + Linear context (WOR-249 exists).

## Prior decision (WOR-249, 2026-04-20)

"During WOR-211 review we decided Docker should be the favored quickstart." Audit revealed Docker-first was broken end-to-end. WOR-249 is backlog'd.

## Contradiction with WOR-300

WOR-300 ships `curl worthless.sh | sh` as primary. That's **not** Docker-first. Either:
- Honor prior: pause WOR-300 until Docker fixed
- Reverse: curl|sh IS primary, Docker is alternate
- **Both-primary (chosen):** Worker serves BOTH via path-based routing

## Decision: Option C — Worker serves both install paths

The Worker already does UA routing. Adding a path router is ~10 lines:

| Request | Response |
|---|---|
| `GET /` + curl UA | serve `install.sh` (uv bootstrap) |
| `GET /` + browser UA | 302 → wless.io |
| `GET /docker` + curl UA | serve `docker-install.sh` (pull image + alias) |
| `GET /docker` + browser UA | 302 → wless.io/docker-quickstart |
| `GET /?explain=1` + curl | walkthrough for install.sh |
| `GET /docker?explain=1` + curl | walkthrough for docker-install.sh |

Both personas get magical one-liner. User picks which.

## Why path-based not flag-based

User's critique (accurate): "doesn't downloading the docker take a long fucking time? magic should be super super fast." Docker pull of a ~50MB image takes 10–30s depending on network. That's a different UX than uv install (also ~30s for Python fetch). They feel similar in clocktime.

BUT: Docker users are already Docker users. They've pulled images before. The friction is cognitive, not temporal — "what's the magical command for me?" Path-routing gives them their own blessed one-liner.

## What `docker-install.sh` does

Lives in repo as `docker-install.sh` (separate from `install.sh`). Bundled into Worker alongside `install.sh`.

```sh
#!/bin/sh
# worthless.sh/docker — Docker install path
set -eu

# 1. Verify docker present. If not, link to docs (don't attempt to install Docker).
command -v docker >/dev/null || { echo "Docker required: https://docs.docker.com/get-docker/" >&2; exit 20; }

# 2. Pull the pinned image.
docker pull shacharm2/worthless:v0.3.0

# 3. Prompt for alias install (consent gate, --yes bypasses).
#    Writes to ~/.zshrc or ~/.bashrc with a clear block:
#      # BEGIN worthless
#      alias worthless='docker run --rm -it -v $PWD:/work -v ~/.worthless:/root/.worthless shacharm2/worthless:v0.3.0'
#      # END worthless

# 4. Banner: "Reload your shell: exec $SHELL. Then: cd project && worthless lock".
```

## Docker image requirements (scope for WOR-249, not WOR-300)

- Pinned tag `shacharm2/worthless:v0.3.0` on Docker Hub
- Multi-arch (amd64 + arm64)
- Non-root user inside container
- `~/.worthless` as named volume for keystore persistence
- Entrypoint that proxies to `worthless` CLI

None of that is in WOR-300 scope. WOR-300 just serves `docker-install.sh`. The ticket for the image + alias content is WOR-249.

## Speed concern addressed

"Magic should be super super fast" → `docker-install.sh` runs `docker pull` which is 10–30s for a ~50MB image. Not instant, but comparable to the uv path (Python fetch is similar). User feedback during pull:

```
Pulling worthless:v0.3.0 (~50MB)...
████████████████████░ 89%
Done. Setting up shell alias...
```

Reuses Docker's built-in progress bar. Don't reinvent.

## Relation to WOR-300 vs WOR-249

- **WOR-300:** Worker serves both install paths (bundles `docker-install.sh` same way it bundles `install.sh`).
- **WOR-249:** Owns the content of `docker-install.sh`, the Docker Hub image, the alias mechanics, any post-install needs for the Docker path.
- WOR-249 is NOT a blocker for WOR-300 to ship. Worker can serve an initial version of `docker-install.sh` that just pulls + aliases; WOR-249 can later enrich the image with lock/up/etc.
- WOR-249 should be re-prioritized to v1.1 if Docker path ships day 1.
