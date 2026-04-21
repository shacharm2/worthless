# New ticket brief — Authenticate Docker Hub pulls in release workflow

**Proposed project**: v1.1 (post-launch hardening — same line as cosign-signing and arm64-scan wins)
**Proposed priority**: P1 (reliability risk, not acute but recurring)
**Proposed labels**: `v1.1`, `DevOps`, `reliability`
**Proposed parent epic**: none

## Story (ELI5)

Our release workflow pulls the Debian Python base image (`python:3.12.9-slim`) from Docker Hub three times per release — once per local scan build, once per multi-arch push. On an unauthenticated GitHub Actions runner, Docker Hub enforces a rate limit of 100 pulls per 6 hours PER SHARED IP pool. Since GitHub runners share IP ranges across all customers, any release window where that pool is saturated 429s us mid-build, usually during the slow arm64 QEMU step. Chaos-engineer reviewer flagged this during WOR-236 review as the failure mode nobody had considered.

## Why this issue exists

WOR-236 built the GHCR publish workflow with unauthenticated Docker Hub pulls. Three-build design (amd64 scan + arm64 scan + multi-arch push) triples the base-image pulls per release. Released under time pressure; reliability against Docker Hub rate limits was deferred. This is the follow-up.

## Options

### Option A — Mirror base image to GHCR (recommended)

- Add a separate weekly workflow that pulls `python:3.12.9-slim`, re-tags as `ghcr.io/<owner>/python-base:3.12.9-slim`, pushes to our GHCR.
- Dockerfile `FROM` switches to the mirrored ref.
- Releases pull from GHCR — same registry we push to — no external rate limit.
- Base image upgrades = update the mirror workflow + Dockerfile pin.
- **Cost**: ~2h. One new workflow, one Dockerfile line, one docs note.

### Option B — Authenticate pulls with Docker Hub PAT

- User creates Docker Hub account + read-only PAT.
- Add `DOCKERHUB_TOKEN` + `DOCKERHUB_USERNAME` as repo secrets.
- Workflow adds `docker/login-action` for `docker.io` before any build.
- Authenticated pulls = 200/6h for free tier (still rate-limited, just less tight).
- **Cost**: ~1h, mostly account setup.

### Option C — Accept the risk, retry on 429

- No auth, no mirror.
- Wrap build steps in retry-on-failure logic.
- Doesn't fix the root cause; just recovers slowly.
- **Cost**: ~30 min, ugly workflow.

**Recommended**: Option A. Removes Docker Hub from our release hot path entirely. One-time setup, zero ongoing credential management.

## Acceptance criteria

- [ ] `python:3.12.9-slim` is no longer pulled from `docker.io` during a release run (verify via `docker events` or workflow logs).
- [ ] Base image mirror workflow runs on schedule AND on-demand (workflow_dispatch).
- [ ] Dockerfile `FROM` line references the mirrored image.
- [ ] A release workflow run fully succeeds with Docker Hub blocked via network rule (local test or intentional DNS override).

## Research context for the implementer

- Docker Hub rate limits: 100 unauth / 200 authenticated / 6h per source IP. GitHub runners share IP pools — you can hit the limit even on first pull if someone else used the pool that 6h window.
- GHCR is our canonical registry already; mirroring there keeps "all our images in one place" posture and costs nothing (GHCR has no pull limits for public packages).
- `docker/build-push-action` honors `docker.io` rate limits transparently — it'll fail with `TOOMANYREQUESTS` and exit the build step.

## Dependencies

- WOR-236 must ship (this ticket modifies the workflow it creates).

## Scope boundary

Does NOT include:
- Mirroring other base images (Dockerfile currently has one `FROM`).
- Generalized image-mirror infrastructure (this is a one-line workflow, not a platform).
- Automatic CVE-driven base-image bumps (separate concern — renovate/dependabot territory).

## Effort estimate

Option A: ~2 hours total.
