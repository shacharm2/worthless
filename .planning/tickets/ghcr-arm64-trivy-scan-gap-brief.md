# New ticket brief — Close arm64 Trivy scan gap in GHCR publish workflow

**Proposed project**: v1.1 (post-launch hardening — same line as cosign-signing)
**Proposed milestone**: v1.1 (no milestone) or "Post-launch hardening" if user wants grouping
**Proposed labels**: `v1.1`, `DevOps`, `security`
**Proposed priority**: P2 (real gap, low practical exposure for Python-on-Debian-slim)
**Proposed parent epic**: none

## Story (ELI5)

Our GHCR publish workflow scans the amd64 image with Trivy before pushing — but then pushes BOTH amd64 AND arm64 to the registry. The arm64 image never gets scanned. For Python apps on Debian slim this is low risk (base layers overlap heavily), but not zero: arm64 can pull different OS package versions. This ticket closes that gap.

## Why this issue exists

`.github/workflows/publish-docker.yml` (landed in WOR-236) uses a two-build-push-action pattern:

1. Build amd64-only with `load: true, push: false` → Trivy scan → gate on CRITICAL.
2. Build+push multi-arch `linux/amd64,linux/arm64`.

Step 1 validates only amd64. Step 2 ships both architectures unscanned on the arm64 side. Brutus flagged during WOR-236 review: "Debian's arm64 slim image has diverged from amd64 on at least 4 CVEs in 2024-2025." Security reviewer agreed it was a gap but acceptable-with-follow-up. This is the follow-up.

## What needs to be done

Pick one approach (ticket scope includes the decision):

### Option A — Scan both architectures locally before push (recommended)

- Add a second `build-push-action` call for `linux/arm64` with `load: true, push: false`.
- Run Trivy against the arm64 local image.
- Then proceed to the multi-arch push as today.
- Cost: +5-8 min build time per release (arm64 under QEMU on amd64 runner).
- Pro: simple, strict gate, scan covers exactly what ships.

### Option B — Scan the pushed manifest by digest, roll back on fail

- Push multi-arch first.
- Trivy scan against `ghcr.io/<owner>/worthless-proxy@${digest}` using the manifest list.
- On fail: delete the tag via `gh api -X DELETE /packages/container/.../versions/...`.
- Cost: scan time ~equal, but tag-deletion is racy if a user pulls between push and delete.
- Pro: single build path. Con: non-atomic rollback, visibility-flip might already have run.

### Option C — Replace Trivy with a registry-native scan (GHCR + GitHub security advisories)

- Rely on GitHub's built-in GHCR vulnerability scanning (passive, post-push).
- Remove the pre-push Trivy gate entirely.
- Cost: scan is informational only, not a release gate. Regresses security posture.
- Not recommended.

**Recommended**: Option A. Simplest, strictest, mirrors existing pattern.

## Acceptance criteria

- [ ] Workflow scans both `linux/amd64` AND `linux/arm64` images before the push step.
- [ ] Trivy failure on either arch blocks the push.
- [ ] Total workflow runtime stays under 25 min (current `timeout-minutes`).
- [ ] Comment in workflow documents why both arches are scanned and references this ticket's resolution.

## Research context for the implementer

- `docker/build-push-action@v6` supports per-platform local load ONLY if `platforms:` is a single platform per call. Multi-platform `load: true` is not supported by the Docker daemon (manifest list can't load into single daemon). That's why the current approach builds amd64-only for scan.
- Option A means two scan builds + one push build = three build invocations, but Buildx layer cache within a single job is shared in-memory, so the push build is fast (reuses cached layers).
- Trivy can scan OCI tarballs directly: `trivy image --input /tmp/image.tar`. Alternative to `load: true` if daemon interactions are problematic.
- Debian slim arm64 diverge examples: openssl, libxml2, ca-certificates have had arch-specific CVE windows. Check Trivy DB for concrete examples when implementing.

## Dependencies

- WOR-236 must ship first (this ticket modifies the workflow it creates).

## Scope boundary

Does NOT include:
- Adding more architectures (ppc64le, s390x).
- Switching to cosign (separate v1.1-post-launch ticket).
- Replacing Trivy with Grype or Snyk.
- Registry-side scanning configuration.

## Effort estimate

~2 hours (one extra scan step + comment + CI runtime validation).
