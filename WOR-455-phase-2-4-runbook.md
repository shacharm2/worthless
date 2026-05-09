# WOR-455 Phase 2 – 4 — Operator Runbook

**Prereq:** Phase 1 (PR #160) merged to `main`. `marketing/`,
`wrangler-marketing.toml`, `.github/workflows/{deploy,lint}-marketing.yml`
present on `main`.

**Audience:** repo maintainer with Cloudflare dashboard + GitHub
admin access. Every step that requires an external GUI or a secret is
flagged **OPERATOR**. Every code change goes through PR review on a
short-lived branch.

**Threat model invariant:** production deploys are tag-only.
`workflow_dispatch` is preview-only by construction
(`deploy-marketing.yml` § "LOAD-BEARING" comment). The runbook never
asks an operator to flip that switch.

---

## Pre-flight checklist (block until all green)

Run before Phase 2 and re-confirm before Phase 3.

| # | Item | How to verify | Owner |
|---|------|---------------|-------|
| P1 | Repo Variable `MAINTAINER_GPG_PUBKEY` populated | Settings → Variables → Actions → repo-scope | OPERATOR |
| P2 | Repo Variable `MAINTAINER_GPG_FINGERPRINT` populated | same as P1 | OPERATOR |
| P3 | Repo secret `CLOUDFLARE_ACCOUNT_ID` exists | Settings → Secrets → Actions | OPERATOR |
| P4 | Repo secret `CLOUDFLARE_ACCOUNT_SUBDOMAIN` exists (used by smoke) | same as P3 | OPERATOR |
| P5 | GH Actions environment `wless-marketing-preview` created | Settings → Environments | OPERATOR |
| P6 | GH Actions environment `wless-marketing-production` created with deployment-branch rule restricting to `marketing-v*` tags | same as P5 | OPERATOR |
| P7 | Secret `CLOUDFLARE_API_TOKEN_MARKETING` (preview-scoped) attached to env `wless-marketing-preview`. Token scope: `Edit Workers Scripts` on `wless-marketing-preview` only. | env-scoped secrets UI | OPERATOR |
| P8 | Secret `CLOUDFLARE_API_TOKEN_MARKETING` (production-scoped) attached to env `wless-marketing-production`. Token scope: `Edit Workers Scripts` on `wless-marketing` + `Edit Workers Routes` on zone `wless.io`. **Distinct token from P7** — leak isolation. | env-scoped secrets UI | OPERATOR |
| P9 | `security@wless.io` mailbox exists and is monitored (sweep rewrote `marketing/.well-known/security.txt`) | send a test mail | OPERATOR |
| P10 | Current zone Transform Rule "Security Headers" recorded for byte-comparison: capture exact response from `https://wless.io/` with `curl -fsI` and save to `phase2-audit/baseline-headers.txt` | shell, see § Phase 2 step 2.2 | OPERATOR |

If any of P1–P9 are missing, stop. Phase 2 cannot start.

---

## Phase 2 — Preview deploy + Transform Rule audit

**Goal:** Worker serves on `wless-marketing-preview.<sub>.workers.dev`
with security-header byte parity to current production zone Transform
Rule output. No DNS, no production touch.

**Reversibility:** fully reversible. Preview surface has no public DNS;
disabling is `wrangler delete` (operator) and the preview env in GH.

### 2.1 — Trigger preview deploy

OPERATOR:

1. GitHub → Actions → "Deploy Worker (wless-marketing)" → Run workflow.
2. Branch: `main`. Target: `preview` (only choice).
3. Wait for `verify` + `deploy` + `smoke` jobs to all green.

Verification: `Marketing smoke (preview)` job passes its inline checks
(install command in body + 6 headers each count==1 on preview URL).

If `smoke` fails: read the job log; the placeholder smoke prints which
header is missing or doubled. Fix forward by editing
`marketing/_headers` or `marketing/index.html` on a new PR. **Do not**
edit on the preview workers.dev surface directly.

### 2.2 — Capture baseline (current production = Transform Rule)

OPERATOR — run locally before disabling anything:

```bash
mkdir -p phase2-audit  # already in .gitignore
curl -fsI https://wless.io/ | tee phase2-audit/baseline-headers.txt
curl -fsI https://wless-marketing-preview.${ACCOUNT_SUBDOMAIN}.workers.dev/ \
  | tee phase2-audit/preview-headers.txt
```

### 2.3 — Compare

Header values MUST be byte-identical between baseline and preview for
all six WOR-299 headers. Diff:

```bash
for h in strict-transport-security content-security-policy \
         x-frame-options x-content-type-options \
         referrer-policy permissions-policy; do
  baseline=$(grep -i "^${h}:" phase2-audit/baseline-headers.txt | head -1)
  preview=$(grep -i "^${h}:" phase2-audit/preview-headers.txt | head -1)
  if [ "$baseline" != "$preview" ]; then
    echo "DRIFT: $h"
    echo "  baseline: $baseline"
    echo "  preview:  $preview"
  fi
done
```

**Exit gate:** zero `DRIFT:` lines. If any drift, fix `marketing/_headers`
on a PR — **do not** loosen the Transform Rule to match.

### 2.4 — Phase 2 done

Outputs:
- Preview Worker live, byte-parity confirmed.
- `phase2-audit/baseline-headers.txt` retained locally (gitignored) as
  the rollback target for Phase 3.

**Do not** disable the Transform Rule yet. Phase 3 owns the cutover
window.

---

## Phase 3 — Production cutover (signed `marketing-v*` tag)

**Goal:** `wless.io/*` served by `wless-marketing` Worker; zone Transform
Rule "Security Headers" disabled in the same maintenance window.

**Reversibility:** fully reversible until § Phase 4 step 4.3 (HSTS
preload submit). Up to that point: re-enable Transform Rule, redeploy
without route, undo DNS via dashboard.

**Maintenance window estimate:** 15 min including verification.

### 3.1 — Uncomment production route in `wrangler-marketing.toml`

Branch: `feature/wor-455-phase-3-cutover` (claude/* prefix also fine).

Edit `wrangler-marketing.toml`, uncomment the bottom block:

```toml
[[env.production.routes]]
pattern = "wless.io/*"
zone_name = "wless.io"
```

PR title: `feat(marketing): claim wless.io/* route for production Worker (WOR-455 Phase 3)`

PR body MUST include:
- Link to this runbook.
- The Phase 2 baseline → preview drift-check output (zero drift).
- Operator confirmation P1–P9 all green.

Reviewer must verify the route block is the **only** material change.

### 3.2 — Merge + tag

After merge to `main`:

```bash
git fetch origin main
git checkout main
git pull --ff-only origin main
TAG=marketing-v0.1.0
git tag -s "$TAG" -m "WOR-455 Phase 3 — claim wless.io/* route"
git push origin "$TAG"
```

GPG-signing the tag is mandatory; `verify-tag.sh` rejects unsigned
tags and `deploy-marketing.yml` runs `verify-tag.sh` in both `verify`
and `deploy` jobs (defense-in-depth). An unsigned tag will fail before
any wrangler call.

### 3.3 — Workflow execution

`deploy-marketing.yml` triggered by tag push:

1. `verify` job — re-runs lint guards + verifies tag signature.
2. `deploy` job — re-verifies tag, runs `wrangler deploy --config wrangler-marketing.toml --env production`.
3. `smoke` job — **skipped on production** in current YAML (the inline
   placeholder is preview-only). Operator runs production smoke
   manually in step 3.4 until WOR-457 ships the reusable smoke
   workflow.

If any job fails: tag is rolled back via `git push --delete origin
"$TAG"` (operator) and the route claim is undone by reverting the PR
on a new branch. The Worker stays deployed but unrouted; no public
surface is affected.

### 3.4 — Production smoke (manual, until WOR-457)

OPERATOR — within 60 s of `deploy` job success:

```bash
URL="https://wless.io/"
# 1. body contains canonical install command
curl -fsSL "$URL" | grep -qE 'pipx install worthless|curl.*worthless\.sh|docker run.*worthless' \
  && echo "OK: install command present" || echo "FAIL: install command missing"
# 2. each of 6 headers fires EXACTLY once (catches Worker+Transform Rule
#    double-fire — this is the bug Phase 3.5 disables the rule to avoid)
for h in strict-transport-security content-security-policy \
         x-frame-options x-content-type-options \
         referrer-policy permissions-policy; do
  count=$(curl -fsI "$URL" | grep -ic "^${h}:" || true)
  [ "$count" = "1" ] && echo "OK: $h count=1" \
                     || echo "ALERT: $h count=$count (rule + Worker double-fire)"
done
```

After cutover but **before** rule disable, several headers will
likely show `count=2`. That's expected for the next ~5 min until
step 3.5 completes. Do not roll back on `count=2` unless step 3.5
also fails.

### 3.5 — Disable zone Transform Rule

OPERATOR — Cloudflare Dashboard:

1. Zone `wless.io` → Rules → Transform Rules.
2. Locate the rule named (likely) "Security Headers" matching the six
   WOR-299 headers.
3. Toggle **Disabled** (do not delete — keeps a recovery target).
4. Wait 60 s for edge propagation.

### 3.6 — Re-smoke after rule disable

Re-run § 3.4. All six headers must now show `count=1` exactly. If any
shows `count=0`, the Worker is not serving them — re-enable the rule
immediately and investigate (most likely cause: `marketing/_headers`
not in the deployed bundle; check `.assetsignore` did not include it).

### 3.7 — Phase 3 done

Outputs:
- `wless.io/` served by Worker.
- Transform Rule disabled (not deleted).
- All six headers count==1.
- Tag `marketing-v0.1.0` exists on origin, GPG-signed by maintainer.

---

## Phase 4 — Cleanup, decommission, preload

**Goal:** decommission the legacy GH Pages surface, lock in the new
production posture via HSTS preload, deindex stale URLs.

**Reversibility:** step 4.1 (GH Pages) reversible. Step 4.2 (branch
delete) reversible from local clone via re-push. Step 4.3 (HSTS
preload submit) **effectively irreversible** for ~12 weeks — once
preloaded, browsers refuse plain-HTTP for `wless.io` and all
subdomains. Do not run 4.3 unless 3.7 has held green for at least
72 h.

### 4.1 — Disable GitHub Pages on `website` branch

OPERATOR — repo Settings → Pages:

1. Source: change from `Deploy from a branch` to `None`.
2. Wait 60 s.
3. Verify the legacy GH Pages URL (likely
   `https://shacharm2.github.io/worthless/`) returns 404.

DNS for `wless.io` already points to Cloudflare (step 3.5 confirmed
the Worker route serves traffic), so disabling Pages does not affect
the live surface. This step closes the leak vector where the stale
`worthless.cloud`-self-identifying copy was still publicly indexable
on a github.io URL.

### 4.2 — Delete `website` and `website-dev` branches

**Wait at least 14 days** after step 4.1 before deleting. Branches
are cheap; a quick Phase 3 rollback may need the old copy.

After grace period:

```bash
git push origin --delete website
git push origin --delete website-dev
```

If anyone needs the history later: `git fetch origin
'refs/replace/*:refs/replace/*'` and the SHAs in
`WOR-455-phase-1-plan.md` § 0.2 still point to the same objects until
GC.

### 4.3 — Submit HSTS preload (gates rollback)

`marketing/_headers` already advertises `preload`. Verify:

```bash
curl -fsI https://wless.io/ | grep -i strict-transport-security
# expected: max-age=15552000; includeSubDomains; preload
```

Then OPERATOR submits `wless.io` at https://hstspreload.org/ . The
form will run its own checks (HTTPS-only, valid cert, includeSubDomains,
preload directive, max-age >= 31536000 — **this is the catch**).

`max-age=15552000` is **180 days**, which is **below the 1-year
preload requirement (31536000 s)**. Bump `marketing/_headers` to
`max-age=31536000` on a separate PR before submitting:

```diff
- Strict-Transport-Security: max-age=15552000; includeSubDomains; preload
+ Strict-Transport-Security: max-age=31536000; includeSubDomains; preload
```

Tag the bump as `marketing-v0.2.0` and deploy via the same Phase 3
pipeline. Re-curl, then submit.

Once submitted, Chrome/Firefox/Safari pull the list on their own
schedule (typically 4-12 weeks). Until removal (and removal takes
months once landed), `wless.io` is HTTPS-only on every preloaded
browser. Plan accordingly.

### 4.4 — Sitemap + search console

OPERATOR:

- Google Search Console: add `wless.io` property; submit
  `https://wless.io/sitemap.xml`. Request reindex of root.
- Bing Webmaster: same. Optional but cheap.
- 301 redirect from `worthless.cloud` (lives on the `worthless.cloud`
  zone, which is a separate ticket per Phase 1 plan § "Cutover (Phase
  2/3) and worthless.cloud→wless.io 301") — track in WOR-455 epic.

### 4.5 — Close the epic

OPERATOR — Linear WOR-455:

- Move to Done.
- Confirm follow-up tickets exist:
  - **WOR-457** — `marketing-url-smoke.yml` reusable workflow
    (replaces the placeholder smoke).
  - **WOR-473** — `staging.wless.io` surface (slot already left in
    `wrangler-marketing.toml [env.preview]`).
  - **WOR-379** — cross-link `wless.io` ↔ `docs.wless.io` (unblocked
    by Phase 4).
  - **worthless.cloud → wless.io 301** ticket (separate zone).

---

## Verification matrix

| Phase | Surface | Body check | Headers count==1 | Cert valid | Notes |
|-------|---------|------------|------------------|------------|-------|
| 2 end | `wless-marketing-preview.<sub>.workers.dev` | install cmd | yes | CF default | drift vs baseline = 0 |
| 3.4 | `wless.io/` | install cmd | **count==2 expected briefly** | yes | rule + Worker overlap |
| 3.6 | `wless.io/` | install cmd | yes | yes | rule disabled |
| 4.1 end | `shacharm2.github.io/worthless/` | n/a | n/a | n/a | expected 404 |
| 4.3 ready | `wless.io/` | install cmd | yes | yes | `max-age=31536000` set |

---

## Rollback paths

| Failed at | Action |
|-----------|--------|
| 2.1 deploy | Read job log; fix on PR; re-dispatch. No prod impact. |
| 2.3 drift | Edit `marketing/_headers` on a PR until drift=0. Loosen the rule never. |
| 3.3 deploy | `git push --delete origin <tag>`; revert route-claim PR. Worker stays deployed unrouted; no public effect. |
| 3.5 rule disable | Re-enable rule (one toggle). Re-smoke; expect double-fire reverted. |
| 3.6 missing headers | Re-enable rule immediately. Investigate `.assetsignore` / bundle contents. |
| 4.3 preload landed in error | Submit removal at https://hstspreload.org/removal/ . Plan for 12+ week window. |

---

## Out of scope for this runbook

- WOR-454 install-command form — closed by Phase 1 port (already on `main`).
- WOR-457 `marketing-url-smoke.yml` — separate ticket.
- WOR-473 `staging.wless.io` — separate ticket.
- Any change to `worthless-sh` Worker, `worthless-docs` Worker, or
  `docs.wless.io` zone.
