# New ticket brief — GHCR single-point-of-failure mitigation

**Proposed project**: v1.2 (hardening — not launch-required; GHCR works today)
**Proposed milestone**: v1.2 (no milestone) or a new "Supply chain resilience" milestone if user wants grouping
**Proposed labels**: `v1.2`, `DevOps`, `reliability`
**Proposed priority**: P2 (resilience improvement; only bites on GHCR outage)
**Proposed parent epic**: none (standalone; user can reparent to a v1.2 infra epic if one exists)

## Story (ELI5)

We publish our Docker image to one registry: GHCR. If GitHub's container registry has a multi-hour outage during a launch window or critical moment, our ONLY persistent-proxy install story is broken with zero fallback. Brutus flagged this during WOR-236 review as the "killer objection" that couldn't be solved inside WOR-236's scope — it's a milestone-level risk, not a ticket-level one. This ticket addresses it.

## Why this issue exists

WOR-236 set up `ghcr.io/<owner>/worthless-proxy` as the single launch-day distribution channel. Explicit non-goal in that ticket: "No registry-migration fallback. GHCR only." Rationale was scope discipline for launch. Post-launch, GHCR has had documented multi-hour outages (status.github.com history shows several in the last 12 months). On any day of a GHCR outage:

- `docker pull` fails for all new users.
- Existing users can't re-pull after image eviction from local Docker cache.
- Our README, install scripts, and docs all reference an unreachable URL.
- Native-service path is v1.2+ (tracked separately), so there's no alternative persistence path yet.

This is a reliability gap with a one-line blast radius: "Worthless is broken for anyone trying to install today."

## What needs to be done

Choose ONE approach (ticket scope includes the decision):

### Option A — Mirror to a second registry

- Add a second job to `.github/workflows/publish-docker.yml` that pushes the same manifest list to a second registry (DockerHub, Quay.io, or a self-hosted registry).
- Update README/docs to advertise BOTH paths: "Primary: `ghcr.io/<owner>/worthless-proxy`. Mirror: `<secondary>/worthless-proxy`."
- Mirror push is best-effort — primary push must succeed; secondary can fail without failing the release.
- Cost: one more registry account + credentials. DockerHub free tier allows 1 public repo with rate-limited pulls — adequate for a fallback.

### Option B — Automated mirror via a pull-through proxy

- Use Docker Hub's official mirror-of-GHCR feature, or run a small Cloudflare-hosted proxy that pulls from GHCR on demand.
- Zero workflow changes; all proxy is runtime.
- Only helps if the pull-through proxy has a separate backend (e.g. scheduled mirror job that pre-populates).

### Option C — Accept risk, document runbook

- Add a section to `docs/runbooks/ghcr-outage.md`:
  - How to check GHCR status.
  - How users can `docker save` / `docker load` an image they already have.
  - How to hand-build from source as an emergency fallback: `git clone && docker build`.
- No infrastructure change; purely documentation + monitoring.

**Recommended**: Option A with DockerHub as secondary. Lowest ongoing cost, highest user-facing reliability, aligns with "one flag, one credential" discipline.

## Acceptance criteria

- [ ] Decision doc in `.planning/` explaining which option was chosen and why (even if Option C).
- [ ] If Option A: secondary registry receives the same image on every `v*` tag push, verified by `docker pull <secondary>/worthless-proxy:<tag>` from a clean machine.
- [ ] If Option B: pull-through proxy tested during a simulated GHCR outage (DNS-block `ghcr.io` locally, confirm pull still works).
- [ ] If Option C: runbook committed and linked from README. One paragraph in README explaining the single-registry posture and the runbook location.
- [ ] PR body explicitly states "This ticket closes brutus's GHCR-SPOF objection from WOR-236 review."

## Research context for the implementer

- GHCR outage history: check status.github.com historical incidents. Multi-hour outages in 2024 are documented — real risk, not theoretical.
- DockerHub free tier: 1 public repo, pulls rate-limited to 100/6h for anonymous users, 200/6h for authenticated. Acceptable for a fallback (users hit it only when GHCR is down).
- Quay.io: also free for public repos, better rate limits, worse ecosystem familiarity.
- Self-hosted registry (Harbor, etc.): overkill for this; ops burden > benefit.
- `docker/build-push-action` supports multiple registries in one `tags:` input — implementation is additive, not a rewrite.

## Dependencies

- WOR-236 must ship first (this ticket extends the publish workflow; no blocker otherwise).
- If Option A + DockerHub: requires user to create DockerHub account and add `DOCKERHUB_TOKEN` as a repo secret.

## Scope boundary

Does NOT include:
- Multi-cloud registry strategy (ECR, ACR, GCR).
- Automatic runtime failover in the worthless CLI.
- CDN-backed registry mirror.
- Signing parity between primary and mirror (tracked in cosign-signing ticket; this ticket signs both or neither, following whatever that ticket lands on).

## Effort estimate

- Option A: ~3 hours (workflow + docs + secrets + test).
- Option B: ~6 hours (proxy infra + testing).
- Option C: ~1 hour (runbook + README).

## Why not launch-block

WOR-236's AC is satisfiable today — GHCR is working. This ticket is purely resilience against a tail-risk outage. Launch-blocking would drag v1.1 for weeks of infra work that only bites during the specific case of a GHCR incident overlapping a critical user moment.
