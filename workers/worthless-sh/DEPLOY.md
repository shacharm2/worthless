# Deploy Runbook — `worthless.sh` Cloudflare Worker

Operator runbook for the install-script Worker. Covers one-time setup,
per-release procedure, rollback, and pre-WOR-323 caveats. Source of
truth for "what do I do when the deploy is on fire" lives here.

---

## Architecture (one paragraph)

A single Cloudflare Worker at `worthless.sh` serves `install.sh` to
curl-family clients and 302-redirects browsers to `wless.io`. The script
is bundled into the Worker bytes at build time (Option A inline-bundle
per `engineering/adr/001-worthless-sh-inline-bundle.md`) — no runtime
fetch from GitHub. The Worker emits `X-Worthless-Script-Sha256` so
auditors can compute `curl worthless.sh | sha256sum` and confirm match.

Deploy pipeline: a `v*` tag push fires `.github/workflows/deploy-worker.yml`,
which runs vitest, computes the install.sh sha256, verifies the tag
signature, then `wrangler deploy --env production` with `SCRIPT_TAG` /
`SCRIPT_COMMIT` injected at deploy time.

---

## One-time setup

### 1. Cloudflare account

| Setting | Value | Why |
|---|---|---|
| Domain | `worthless.sh` (apex) on Cloudflare DNS | Required for the route binding. |
| SSL/TLS mode | **Full (strict)** | Anything weaker accepts MITM-substituted certs. |
| HSTS | `max-age=63072000; includeSubDomains; preload` | Matches the Worker-emitted header. Submit to hstspreload.org after first prod deploy. |
| Bot Fight Mode | **OFF** | A challenge page served to a curl client and piped to `sh` is RCE-class. |
| Always Use HTTPS | ON | Defence-in-depth. |
| Min TLS Version | 1.2 | RFC 8996 deprecates 1.0/1.1. |
| Cache Level | Bypass for `worthless.sh/*` | The Worker emits its own cache headers; CDN-level caching could collide with `Vary: User-Agent`. |

### 2. Cloudflare API token

Create a **scoped** token (NOT the global account token):

- **Permissions:** `Account → Workers Scripts → Edit`
- **Account resources:** Include → your account only
- **Zone resources:** Include → `worthless.sh` only
- **Client IP filter:** GitHub Actions IP ranges (optional, hardening)
- **TTL:** 90 days, calendared rotation

Per-WOR-323: rotate to a Workers-only OIDC-issued short-lived token
once Cloudflare exposes one for GHA.

### 3. GitHub repository setup

#### Actions environments (Settings → Environments)

Create two environments:

| Name | Required reviewers | Wait timer | Deployment branches |
|---|---|---|---|
| `worthless-sh-preview` | 0 (solo dev) | 0 min | `main`, `feature/wor-300-*` |
| `worthless-sh-production` | 0 (per WOR-330; solo until v2.x) | 0 min | tags matching `v*` only |

Each environment scopes secrets:

| Secret | Set in env | Value |
|---|---|---|
| `CLOUDFLARE_API_TOKEN` | both | scoped token from §2 |
| `CLOUDFLARE_ACCOUNT_ID` | both | from Cloudflare dashboard |
| `CLOUDFLARE_ACCOUNT_SUBDOMAIN` | preview only | your `*.workers.dev` subdomain |

#### Branch / tag rulesets

Settings → Rules → Rulesets:

1. **Tag protection (`v*`):**
   - Require signed tags
   - Block force pushes
   - Block deletion
   - Bypass list: empty (no admin override)

2. **Branch protection (`main`):**
   - Require signed commits
   - Require PR before merge
   - Require status checks: `Tests`, `Install Docker (bare-Ubuntu integration)`,
     and (post-Phase 6) `Worker vitest` (WOR-339)

WOR-323 ships these rulesets as IaC; until then, configure via UI.

---

## Per-release deploy

Standard release procedure once setup is complete:

```bash
# 1. From the feature branch, ensure tests are green and PR is merged.
gh pr checks <PR>
gh pr merge <PR> --squash

# 2. Pull latest main locally.
git checkout main && git pull

# 3. Tag with a signed tag (gpg key configured in git config).
git tag -s v0.3.1 -m "Release v0.3.1 — <one-line summary>"

# 4. Push the tag. This fires .github/workflows/deploy-worker.yml.
git push origin v0.3.1

# 5. Watch the deploy workflow.
gh run watch
```

The workflow's smoke-test step verifies `curl worthless.sh | sha256sum`
matches `X-Worthless-Script-Sha256` matches the build-time hash. If the
smoke test fails, the deploy is recorded as failed but the route may
already be live — proceed to rollback.

### Pre-deploy: dry-run via workflow_dispatch (preview)

Before tagging a release for production, exercise the full pipeline against
the preview environment:

```bash
gh workflow run deploy-worker.yml -f target=preview
gh run watch
```

Then dogfood the preview URL:

```bash
PREVIEW_URL="https://worthless-sh-preview.<account>.workers.dev"
curl -sSL "${PREVIEW_URL}/" | head -10                        # → install.sh
curl -sSL "${PREVIEW_URL}/?explain=1" | head -10              # → walkthrough
curl -sSI "${PREVIEW_URL}/" | grep -i x-worthless             # → verifiability headers
curl -sSI -A "Mozilla/5.0" "${PREVIEW_URL}/" | grep -i location  # → 302 to wless.io
```

If preview is good, push the production tag.

---

## Rollback

Three layers, fastest first:

### 1. Cloudflare dashboard — kill the route (~30 seconds)

The "stop the bleeding" path. Removes the route binding immediately;
`curl worthless.sh` starts returning DNS-only Cloudflare default (404).

```
Cloudflare Dashboard → Workers & Pages → worthless-sh →
  Triggers → delete the worthless.sh/* route
```

Users who try to install during the outage see a clear failure rather
than a malicious script. Restore the route once the bad deploy is
identified.

### 2. Re-deploy a known-good tag (~2 minutes)

```bash
gh workflow run deploy-worker.yml -f target=production
# (uses the workflow's default of last-good main commit, NOT the tagged ref)
```

Or re-tag the last-good commit:

```bash
git checkout v0.3.0      # the last-good tag
git tag -s v0.3.2-rollback-of-v0.3.1 -m "Roll back v0.3.1 — see <incident>"
git push origin v0.3.2-rollback-of-v0.3.1
```

Convention: rollback tags are clearly named so the audit trail shows
intent.

### 3. Domain compromise (~5 minutes, last resort)

If the Cloudflare account itself is compromised, delete the DNS records
at the registrar:

```
Registrar (e.g., Porkbun) → worthless.sh → DNS →
  Delete the `worthless.sh` A/AAAA/CNAME record
```

`curl worthless.sh` then NXDOMAIN-fails, which is the safest possible
outcome. Restore once the account is recovered. Full IR runbook lives
at WOR-331.

---

## Verification (post-deploy auditor checklist)

```bash
# 1. Body sha256 matches the release.
curl -sSL https://worthless.sh/ | sha256sum
# → compare to the published value in the GitHub release notes

# 2. X-Worthless-Script-Sha256 header matches the body.
curl -sSI https://worthless.sh/ | grep -i x-worthless-script-sha256

# 3. Tag and commit headers point at the right ref.
curl -sSI https://worthless.sh/ | grep -i 'x-worthless-script-tag\|x-worthless-script-commit'

# 4. Browser fail-safe still works.
curl -sSI -A "Mozilla/5.0" https://worthless.sh/ | head -3
# → expect HTTP/2 302; location: https://wless.io

# 5. Walkthrough still works.
curl -sSL https://worthless.sh/?explain=1 | head -10

# 6. Security headers still attached.
curl -sSI https://worthless.sh/ | grep -i 'strict-transport\|nosniff\|referrer-policy'

# 7. /.well-known/security.txt still served (RFC 9116).
curl -sSL https://worthless.sh/.well-known/security.txt | head -10
```

---

## Pre-WOR-323 caveats

The Phase 5 workflow ships with three soft-fails that WOR-323 hardens
into fatal checks:

1. **Tag-signature verification is non-fatal.** A `::warning::` is logged
   when `git tag --verify <tag>` fails. WOR-323 imports the maintainer
   keyring as a workflow step and makes this fatal.

2. **No SLSA / Sigstore provenance.** WOR-303 (post-v1.1) adds
   `sigstore-github-generator` to publish a signed release manifest with
   `{tag, commit, install.sh sha256, worker bundle sha256}`.

3. **CLOUDFLARE_API_TOKEN is account-scoped, not OIDC.** When Cloudflare
   ships OIDC for GHA, WOR-323 swaps to short-lived issued tokens.

Until WOR-323 lands, the **production environment must be set with 0
required reviewers + solo-dev allowlist** so a single compromised
maintainer credential doesn't auto-ship malicious bytes. See
`security-audit/threat-model-worthless-sh.md` finding F-12.

---

## Known gotchas

- **Wrangler picks the wrong `[env.*]` block.** If you forget
  `--env preview`, Wrangler deploys against the bare config, which has
  no route binding but is named `worthless-sh` — colliding with the
  production worker's name. Always pass `--env`.

- **`compatibility_date` drift.** Bumping the date in `wrangler.toml`
  can change runtime semantics (e.g., header normalisation, stream
  behaviour). Bump in a dedicated commit + PR with the full vitest
  suite re-run.

- **Cloudflare cache outliving the Worker.** Cache-Control values from
  the Worker are honoured but the CDN may cache the redirect for the
  TTL anyway. Purge via dashboard if the redirect target ever changes.

- **`workers.dev` subdomain leaks the account name.** Preview URL is
  `https://worthless-sh-preview.<account>.workers.dev`. Acceptable for
  internal dogfood; do not link to it publicly.

---

## Contacts

* **On-call:** see `.github/SECURITY.md`
* **Incident response:** WOR-331
* **Threat model:** `security-audit/threat-model-worthless-sh.md`
