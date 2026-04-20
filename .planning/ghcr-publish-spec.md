# WOR-236 — Publish Docker Image to GHCR (v2, post-review)

**Linear**: WOR-236 (v1.1 project → Wave 7 — Launch → Launch Blockers epic WOR-234)
**Branch**: `gsd/wor-236-ghcr-docker`
**Worktree**: `/Users/shachar/Projects/worthless/worthless-wor236`
**Spec version**: v2 (folds in deployment-engineer + security-engineer + brutus review findings)

## Target (one sentence)

On every `v*` git tag push, GitHub Actions builds `./Dockerfile` for both `linux/amd64` and `linux/arm64`, scans the result with Trivy, publishes multi-tagged images to `ghcr.io/<owner>/worthless-proxy` authenticated with `GITHUB_TOKEN`, flips package visibility to public automatically on first push, and runs a pull-back-and-smoke test against the published manifest — so anyone with Docker can `docker run` the persistent proxy without running `worthless up`.

## Why this is a launch blocker

Native-service path (launchd/systemd) slipped to v1.2 (WOR-193). Docker is the **only** launch-day persistent-proxy story. Until an image exists in a public registry, "install once, proxy stays up" is untestable for external users. GHCR closes that gap using the registry coupled to the repo — zero external credentials.

## Acceptance criteria

Canonical AC from `.planning/launch-cleanup-plan.md` draft-6:

> `docker pull ghcr.io/<org>/worthless-proxy:0.3.0 && docker run -d ghcr.io/<org>/worthless-proxy` works from any machine with Docker, proxy runs persistently without manual `worthless up`.

Derived sub-criteria (all gated in-CI where feasible):

| # | Criterion | Gate |
|---|-----------|------|
| AC1 | Workflow triggers on `v*` tag only | `on: push: tags: ["v*"]` |
| AC2 | Builds from repo-root `./Dockerfile` | `file: ./Dockerfile`, `context: .` |
| AC3 | Publishes to `ghcr.io/<owner>/worthless-proxy` | `docker/metadata-action` image input |
| AC4 | Tags: full semver, major.minor, `latest` (stable-only) | `docker/metadata-action` with `flavor: latest=auto` + `type=semver` |
| AC5 | Multi-arch: `linux/amd64` + `linux/arm64` | `platforms:` input on build-push |
| AC6 | `GITHUB_TOKEN` only; no external secrets | `docker/login-action` password |
| AC7 | Release-gate Trivy scan fails on CRITICAL before push | Trivy step with `exit-code: 1` before `push: true` |
| AC8 | Provenance + SBOM attached to manifest | `provenance: true, sbom: true` |
| AC9 | First-push package visibility auto-flipped to public | `gh api -X PATCH` follow-up job |
| AC10 | In-CI smoke: pull from ghcr.io, start container, hit `/healthz` | Pull-and-run job reusing `tests/test_docker_e2e.py` subset |
| AC11 | Idempotent — re-run on same tag does not fail hard | `docker/build-push-action` overwrite-by-tag default |
| AC12 | Concurrency guard prevents same-ref races | `concurrency: group: publish-docker-${{ github.ref }}, cancel-in-progress: false` |

## Non-goals (tracked as separate tickets — see below)

- **Cosign image signing** → new ticket, v1.1 post-launch hardening.
- **Registry fallback / GHCR SPOF mitigation** → new ticket, v1.2.
- **Dockerfile digest-pinning of `FROM python:...`** → already tracked in docker-hardening research backlog.
- **No DockerHub mirror.** GHCR is the single-source registry.

## Spec — workflow file

**Path**: `.github/workflows/publish-docker.yml` (new)

**Why separate file** (not a job in `publish.yml`):
- Independent failure (PyPI pass ≠ Docker pass).
- Different permission sets (`packages: write` vs `id-token: write`).
- Parallel execution without cross-job `needs:`.
- Repo convention — one registry per workflow file.

**Jobs**:

1. **`build-push`** (runs-on: ubuntu-latest, timeout: 25m)
   - `permissions: contents: read, packages: write`
   - `actions/checkout@<sha>` with `persist-credentials: false`
   - `docker/setup-qemu-action@<sha>` (arm64 emulation for amd64 runner)
   - `docker/setup-buildx-action@<sha>`
   - `docker/login-action@<sha>` → `ghcr.io`, `username: ${{ github.actor }}`, `password: ${{ secrets.GITHUB_TOKEN }}`
   - `docker/metadata-action@<sha>`:
     - `images: ghcr.io/${{ github.repository_owner }}/worthless-proxy`
     - `tags: type=semver,pattern={{version}}` + `type=semver,pattern={{major}}.{{minor}}`
     - `flavor: latest=auto` (action handles pre-release correctly — NO hand-rolled `!contains(ref, '-')`)
   - **Trivy release-gate** (`aquasecurity/trivy-action@<sha>` — same SHA as `docker-security.yml`):
     - Build image locally first (`load: true, push: false` in a preliminary build-push step), scan, fail on CRITICAL before proceeding.
     - Alternative: single `build-push-action` call with `push: false, load: true`, then Trivy, then a second call with `push: true`. Decide during implementation based on cache reuse.
   - `docker/build-push-action@<sha>` (final push):
     - `context: .`, `file: ./Dockerfile`, `platforms: linux/amd64,linux/arm64`
     - `push: true`
     - `tags: ${{ steps.meta.outputs.tags }}`, `labels: ${{ steps.meta.outputs.labels }}`
     - `provenance: true`, `sbom: true`
     - **No GHA cache** — tag-only workflow, cache is theatre here. If build time becomes painful, revisit.

2. **`visibility`** (runs-on: ubuntu-latest, needs: build-push)
   - `permissions: packages: write`
   - One step: `gh api -X PATCH /orgs/${{ github.repository_owner }}/packages/container/worthless-proxy/visibility -f visibility=public` (or user-scoped path depending on owner type).
   - Guarded by `if: ${{ success() }}` and idempotent (PATCH to already-public is 204).
   - On failure, job logs the exact manual command for the user to run. AC1 does NOT depend on this job succeeding (fail-open).

3. **`smoke`** (runs-on: ubuntu-latest, needs: [build-push, visibility], timeout: 10m)
   - `permissions: contents: read`
   - Steps:
     - Checkout (for `tests/test_docker_e2e.py`).
     - `astral-sh/setup-uv@<sha>` + `uv sync --group test`.
     - `docker pull ghcr.io/${{ github.repository_owner }}/worthless-proxy:${{ github.ref_name }}` — force pull from registry, not local daemon.
     - `uv run pytest tests/test_docker_e2e.py -v -m docker -k "container_starts_healthy or enroll_and_healthz or runs_as_non_root" --tb=short` with `WORTHLESS_DOCKER_IMAGE=ghcr.io/...:<tag>`.
   - Runs only on single arch (amd64 runner). Multi-arch parity verified by metadata-action manifest list.

## Token & permissions model

- `GITHUB_TOKEN` ephemeral, per-run, repo-scoped.
- `packages: write` granted per-job — `build-push` + `visibility` only. `smoke` is read-only.
- `persist-credentials: false` on every checkout.
- No PATs, no org secrets, no user credentials.
- Rotation automatic.

## `latest` tag policy

Delegated entirely to `docker/metadata-action`'s `flavor: latest=auto` on the `type=semver` rule. Action handles:
- Full semver (`v1.2.3`) → `latest` yes
- Pre-release (`v1.2.3-rc.1`, `v1.2.3-alpha`) → `latest` no
- Build metadata (`v1.2.3+build.5`) → handled per semver spec
- Non-semver tags (`v1.2`, `v1.2-stable`) → `latest` no

Zero hand-rolled string matching. This was a review fix.

## Failure modes & response

| Failure | Cause | Response |
|---|---|---|
| Trivy fails on CRITICAL | Base image CVE between PR-merge and tag | Release does not push. Bump Dockerfile base, re-tag. |
| First push 403 | Workflow permissions misconfigured | Check repo → Settings → Actions → Workflow permissions is "Read and write". |
| `visibility` job 404 | First push on org vs user account — API path differs | Fallback: manual click (logged in job output). Does not block publish. |
| `smoke` job fails | Published image actually broken | Release artifact exists but flagged broken in GitHub UI. Delete package version, fix, re-tag patch. |
| Re-run on same tag | Idempotency | `build-push` overwrites by tag; `visibility` PATCH is idempotent; `smoke` re-runs. Safe. |
| arm64 build slow | QEMU emulation on amd64 runner | Acceptable for tag-only. If >20 min, cache via registry-level `--cache-from ghcr.io/...:buildcache`. |

## Definition of Done

- [ ] `.github/workflows/publish-docker.yml` committed on `gsd/wor-236-ghcr-docker`
- [ ] `actionlint` run (or visual diff against `publish.yml` for syntax/style parity)
- [ ] PR body: one sentence telling users to pin `:X.Y.Z` for prod, `:latest` moves on every release
- [ ] PR body: release-notes line "Unsigned — verify via provenance attestation; cosign signing tracked in <new-cosign-ticket-id>"
- [ ] Two new tickets filed in Linear (see below)
- [ ] Commit message: `feat(ci): WOR-236 publish multi-arch docker image to GHCR on tag push`

## Out-of-repo follow-ups (not this ticket)

- User moves WOR-236 to Done in Linear after first `v*` tag passes `smoke` job.

## Ticket briefs created alongside this spec

- `.planning/tickets/ghcr-cosign-signing-brief.md` → proposed v1.1 post-launch hardening
- `.planning/tickets/ghcr-registry-fallback-brief.md` → proposed v1.2
