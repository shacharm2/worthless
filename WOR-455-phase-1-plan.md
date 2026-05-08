# WOR-455 Phase 1 — Implementation Plan

**Status:** Plan only. Phase 1 not executed. No commits, no pushes, no
`wrangler deploy` (not even `--dry-run`). Awaiting review before
execution.

**Branch:** `claude/wor-455-implementation-plan-6ZO0f` (this clone).
The prompt referenced a worktree at `feature/wor-455-marketing-worker`;
that branch was not present here. Create or rebase onto
`feature/wor-455-marketing-worker` per project convention before any
execution work begins. The plan itself is branch-agnostic.

**Scope:** WOR-455 description steps #16–#26. Build a new Cloudflare
Worker (`wless-marketing`) serving Static Assets for `wless.io`,
ported from the `website` branch's `docs/` tree. **Origin swap, not
zone migration** — `wless.io` is already proxied through the same CF
account as `worthless.sh` and `worthless.cloud`.

**Cutover (Phase 2/3) and worthless.cloud→wless.io 301 (lives on the
worthless.cloud zone) and email routing are explicitly out of scope.**

---

## Pull-quote / mission framing for the eventual PR

> wless.io is currently 226 commits divergent from `main`, marketing
> copy still self-identifies as worthless.cloud, the install command
> on the landing page is wrong (WOR-454), and a single edit that
> touches "marketing + install" spans two PRs on two branches.
> Consolidate to one branch, behind one Worker, with one source of
> truth for security headers — so the next install-funnel fix is
> one PR, not three.

---

## Section 0 — Context binders (live findings, not speculation)

### 0.1 Live production header baseline (re-fetched 2026-05-08)

```
HTTP/2 200
content-security-policy: default-src 'self'; script-src 'self' https://tally.so; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src 'self' data:; frame-src https://tally.so
strict-transport-security: max-age=15552000; includeSubDomains; preload
x-frame-options: DENY
x-content-type-options: nosniff
referrer-policy: strict-origin-when-cross-origin
permissions-policy: camera=(), microphone=(), geolocation=()
```

These are the strings the Worker (or zone Transform Rule, depending on
decision #1) MUST emit verbatim post-cutover. CSP is unchanged from
the truncated form in WOR-455 — it ends at `frame-src https://tally.so`.

### 0.2 Branch divergence (verified)

| Comparison | Commits |
|---|---|
| `origin/main..origin/website` | **226** (website ahead of main) |
| `origin/website..origin/website-dev` | **43** (website-dev ahead of website) |

`website` ↔ `main` divergence is much larger than the 45 the prompt
estimated. This **strengthens** the case against a squash-merge of
`website` into `main` and pushes us toward a clean port (decision #2).

### 0.3 Filename collisions — `origin/website:docs/*` vs `origin/main:docs/*`

```
docs/PROTOCOL.md
docs/install-github-actions.md
docs/install-mcp.md
docs/install-solo.md
```

The three `install-*.md` files are **stale marketing-side copies** —
canonical install docs live at `main:docs/install/{mac,linux,wsl,docker}.md`
(Astro/Starlight). Drop the website-side `install-*.md` from the port.
`PROTOCOL.md` requires a 3-way merge (see step #18).

### 0.4 Confirmed `worthless.cloud` self-identification on website branch

Sample from `origin/website:docs/index.html`:

```html
<link rel="canonical" href="https://worthless.cloud/" />
<meta property="og:image" content="https://worthless.cloud/og-image.png" />
<meta property="og:url" content="https://worthless.cloud/" />
<meta name="twitter:image" content="https://worthless.cloud/og-image.png" />
"url": "https://worthless.cloud/"
"logo": "https://worthless.cloud/apple-touch-icon.png"
```

Step #18a is therefore non-optional; without it the CI guard catches
the leak immediately and blocks merge.

### 0.5 Six HTML files to port from `website:docs/`

```
docs/blog/index.html
docs/coming-soon.html
docs/features.html
docs/how-it-works.html
docs/index.html
docs/memes.html
```

Plus images (favicons, og-image.png, hero.png, memes), `robots.txt`,
`sitemap.xml`, `llms.txt`, `site.webmanifest`, and
`.well-known/security.txt`. Inventory in §3.2.

---

## Section 1 — Open decisions (resolved)

### Decision 1 — Security-headers source of truth

**Recommendation: (a) — Disable the zone "Security Headers" Transform
Rule. Bake all six headers into the Worker.**

| Option | Pros | Cons |
|---|---|---|
| **(a) Worker emits, Transform Rule disabled** ✅ | One source of truth co-located with code. Reviewable in PR. Auditable in `git log`. Survives zone-config drift. Matches `worthless-sh` pattern (Worker emits its own `X-Worthless-*` headers). | Requires CF dashboard write to disable rule (manual step, single user action). Brief overlap window during cutover where both fire — handled by sequencing (disable rule **after** Worker route claims, before route claim is rolled back; verify with smoke). |
| (b) Transform Rule emits, Worker silent | No CF dashboard change needed pre-cutover. | Two sources of truth (zone + repo). Drift inevitable. CSP changes require dashboard work + PR coordination. Future engineers will miss the rule. |
| (c) Both emit (status quo if untouched) | None. | Header doubling — `Strict-Transport-Security` appearing twice with different values is a real CSP-style bug; some browsers honor first, some last, scanners flag inconsistency. Hard rejection. |

Execution sequencing for (a) (Phase 2/3 — not Phase 1, but spec'd here
to bind Phase 1 design): pre-deploy the Worker with all six headers
baked in **and** verify locally via `wrangler dev`. After production
deploy claims `wless.io/*`, immediately disable the Transform Rule via
CF dashboard. Smoke asserts each header appears **exactly once** in
the response.

Phase 1 deliverable: Worker code emits the six headers; CI smoke
contract documented; the dashboard flip is a Phase 2 runbook step,
not a Phase 1 commit.

### Decision 2 — Strategy for the 226 commits on `website`

**Recommendation: clean port (cherry-pick the marketing artifacts as
files, NOT as commits) — option C below.**

| Option | Pros | Cons |
|---|---|---|
| A. Cherry-pick all 226 commits onto `main:/marketing/` | Preserves full author/timestamp history. | Massive conflict storm (`docs/` collisions on every commit). 226 cherry-picks at ~5 min each is a multi-day blocker for no reader-facing benefit. History is preserved on `origin/website` regardless — no information loss. |
| B. Squash-merge `website` → `main` | Single commit, one diff to review. | The merged tree includes `docs/install-*.md` stale files and `worthless.cloud` self-identification. Massive cleanup commit follows. Reviewer cannot tell "what's marketing-redesign" from "what's accidental drag-along". |
| **C. Clean port** ✅ | Reviewer sees exactly what's in production. Deliberate file selection forces decisions about each artifact. Existing `origin/website` ref preserves history; we never delete the branch (per WOR-455 hard guardrail). PR diff is exactly the bytes that ship. | Loses the per-file commit attribution of website-side changes. Mitigation: commit message of the port references `origin/website@<tip-sha>` so the lineage is grep-able. |

Execution: `git show origin/website:docs/<file> > marketing/<file>`
for each file in the inventory (§3.2). One commit per logical group
(HTML, images, metadata, well-known). Reference WOR-455 + the source
SHA in each commit message.

### Decision 3 — Fate of `website-dev` (43 unique commits ahead of `website`)

**Recommendation: fold into the port.** The 43 commits include the
WOR-326 landing redesign (persona chips, two-step install, audit
buttons, `[mcp]` extra note), all of which are in flight per the
sibling tickets (WOR-302/303/304). Picking the older `website` tree
as the port baseline ships a stale design.

Concrete approach:
1. The port baseline tree is `origin/website-dev:docs/` (NOT `origin/website:docs/`).
2. Compare: `git diff origin/website..origin/website-dev -- docs/` — verify the
   delta is purely the WOR-326 redesign with no spurious changes (CodeRabbit
   PR #72 fix is in there per `2afdbc2`).
3. Port from `origin/website-dev` instead. Keep the `origin/website`
   branch alive; document the `website-dev` branch as the "captured
   baseline" in the PR body so reviewers know which ref was used.
4. `origin/website-dev` gets the same lifecycle as `origin/website` —
   never deleted in Phase 1, decommissioned in Phase 4 cleanup.

Rejected alternatives:
- *Fold into `website` first, then port from `website` only:* Adds an
  intermediate merge commit on a branch we're decommissioning.
  Pointless ceremony.
- *Drop `website-dev` work:* Loses the WOR-326 redesign that
  WOR-455 explicitly asks to coordinate with.

### Decision 4 — Deploy mechanism for `wless-marketing`

**Recommendation: tag-gated, GPG-verified — parity with `worthless-sh`.**

| Option | Pros | Cons |
|---|---|---|
| **Tag-gated (`v*` on push, dispatch=preview only)** ✅ | Same threat model as `worthless-sh` — single GPG key gates both production surfaces. PR-time review is preserved (CI deploys only on signed tag). Matches the WOR-401/WOR-323 hardening pattern. | Slower iteration on copy fixes (tag → release → deploy). Mitigated by preview environment on `workflow_dispatch`. |
| Auto-deploy on push to `main` (parity with `worthless-docs`) | Fast iteration. | Marketing has more attack-surface than docs — install command lives there (post WOR-454). Compromised PR merge → instantly live. We've consciously gated `worthless.sh` for this reason; same logic applies. |
| Manual `wrangler deploy` from operator laptop | None for a hosted public surface. | No audit trail. |

Phase 1 deliverable: `deploy-marketing.yml` modeled directly on
`deploy-worker.yml` — same `verify` + `deploy` two-job split, same
`verify-tag.sh` re-invocation in both jobs, same per-env scoped
tokens. The asset-hash smoke test is replaced by the marketing smoke
contract from WOR-457 (canonical=wless.io, install command in body,
six security headers, no leak-class paths).

### Decision 5 — WOR-454 install-command form to feature in marketing hero

**Recommendation: `curl -sSL https://worthless.sh | sh` as the primary
hero CTA, with `pipx install worthless` and `docker run …` as
secondary tabs.**

Rationale:
- `worthless.sh` is the canonical bytes-served install (gated by
  GPG-signed Worker deploy + WOR-323 hardening). Featuring it in the
  hero reinforces the "verified install" story from WOR-326.
- `pipx install worthless` is the hardened-installer story for users
  who already have Python tooling.
- `docker run` is the supply-chain-conscious option per WOR-302/303
  Trust Tier copy.
- `pip install worthless` is **not** featured because pip without
  pipx pollutes the user's environment; it's listed in the
  installation docs but not the hero.

If WOR-326's design has already pinned a different choice in
`origin/website-dev`, **the design wins** — Phase 1 doesn't relitigate
copy decisions, only ports them. Verify which install line is in
`origin/website-dev:docs/index.html` before finalizing the PR.

### Decision 6 — Header values (verbatim from production)

Resolved by §0.1 capture. The Worker code embeds exactly those six
header strings as constants. No re-formatting, no additional headers,
no `Server` removal, no `Cache-Control` change beyond what the
Worker's static-asset behavior already provides.

---

## Section 2 — Step-by-step execution plan

Each step lists: file paths, exact commands, gates, rollback notes,
decision rationale.

### Step #16 — Audit `website-dev` branch

**Goal:** capture exactly what's on `website-dev`, decide its fate
(decision 3 says "fold").

**Commands** (run from worktree root, **no checkout, no merge**):

```sh
git fetch origin website website-dev
git log --oneline origin/website..origin/website-dev | tee phase1-audit/website-dev-commits.txt
git diff --stat origin/website..origin/website-dev -- docs/ | tee phase1-audit/website-dev-diffstat.txt
git diff origin/website..origin/website-dev -- docs/index.html > phase1-audit/website-dev-index.diff
git diff origin/website..origin/website-dev -- docs/PROTOCOL.md > phase1-audit/website-dev-protocol.diff
```

**Note:** `phase1-audit/` is a working-only directory. **Not** committed
(per "no `.planning/<ticket>/` scratch directories" rule). It exists
solely for the human reviewer to glance at before approving the port.
`.gitignore` already excludes `phase1-audit/`? — check; if not, add
`phase1-audit/` to the local `.git/info/exclude` (not `.gitignore`).

**Gate:** the diff in `website-dev-index.diff` shows only the
WOR-326 redesign + CodeRabbit PR #72 fixes. If anything else surfaces
(e.g. a half-merged WOR-453 header experiment), pause and surface it.

**Rollback:** none — this step makes no changes.

**Decision rationale:** decision 3 — port from `website-dev` not
`website`.

---

### Step #17 — Decide the `website` branch's fate (45 → 226 commits)

**Goal:** lock in decision 2 (clean port) with an artifact reviewers
can verify.

**Commands:**

```sh
git log --oneline origin/main..origin/website > phase1-audit/website-vs-main.txt
git log --oneline origin/main..origin/website-dev > phase1-audit/website-dev-vs-main.txt
wc -l phase1-audit/website-vs-main.txt phase1-audit/website-dev-vs-main.txt
```

**Gate:** confirm the divergence is exclusively website-content
(no shared library/SDK changes that `main` needs). A spot-check on
the 5 oldest and 5 newest commits is sufficient.

**Rollback:** none.

**Decision rationale:** decision 2 — clean port. The `origin/website`
and `origin/website-dev` refs **stay alive** through Phase 4. Phase 1
PR body documents `origin/website-dev@<tip-sha>` as the captured
baseline.

---

### Step #18 — Port content from `origin/website-dev:docs/*` → `marketing/*`

**Goal:** produce the `marketing/` tree the Worker will serve.

**File path:** `marketing/` at repo root (sibling of `docs/`,
`workers/`, `wrangler.jsonc`).

**Why root, not under `workers/`:** the existing `worthless-docs`
Worker uses root `wrangler.jsonc` → `./dist/` (Astro build output).
`worthless-sh` lives at `workers/worthless-sh/`. The `wless-marketing`
Worker is a third config; placing assets at `marketing/` and config
at root `wrangler-marketing.toml` mirrors the docs pattern (root config
+ root-relative asset directory) while keeping the assets folder
outside `workers/` to make the layout obvious to reviewers.

**Port commands** (per file, no checkout):

```sh
mkdir -p marketing/blog marketing/.well-known
git show origin/website-dev:docs/index.html         > marketing/index.html
git show origin/website-dev:docs/coming-soon.html   > marketing/coming-soon.html
git show origin/website-dev:docs/features.html      > marketing/features.html
git show origin/website-dev:docs/how-it-works.html  > marketing/how-it-works.html
git show origin/website-dev:docs/memes.html         > marketing/memes.html
git show origin/website-dev:docs/blog/index.html    > marketing/blog/index.html
# images & static
for f in og-image.png hero.png logo-transparent.png \
         meme-jenga.png meme-llm-tower.png \
         apple-touch-icon.png \
         favicon.ico favicon.png favicon-16x16.png favicon-32x32.png \
         android-chrome-192x192.png android-chrome-512x512.png \
         site.webmanifest robots.txt sitemap.xml llms.txt; do
  git show "origin/website-dev:docs/${f}" > "marketing/${f}"
done
git show origin/website-dev:docs/.well-known/security.txt > marketing/.well-known/security.txt
```

**File-by-file disposition (vs `origin/main:docs/*` collisions):**

| File on `origin/website-dev:docs/` | Disposition | Reason |
|---|---|---|
| `index.html`, `coming-soon.html`, `features.html`, `how-it-works.html`, `memes.html`, `blog/index.html` | **port** (run #18a sweep) | Marketing HTML — the whole point of the port. |
| `*.png`, `*.ico`, `favicon-*`, `apple-touch-icon.png`, `og-image.png`, `hero.png`, `logo-*`, `meme-*` | **port** | Static assets referenced by the HTML. Hashes verified post-port. |
| `site.webmanifest`, `robots.txt`, `sitemap.xml`, `llms.txt` | **port** (sweep applies to webmanifest + sitemap; `robots.txt` and `llms.txt` may not contain URLs but verify) | Site metadata. |
| `.well-known/security.txt` | **port** | Required surface (RFC 9116). Verify Contact URL. |
| `CNAME` | **drop** | GitHub Pages artifact. Worker doesn't read this; CF route binding replaces it. |
| `install-github-actions.md`, `install-mcp.md`, `install-openclaw.md`, `install-self-hosted.md`, `install-solo.md`, `install-teams.md` | **drop** | Stale marketing-side copies. Canonical is `main:docs/install/{mac,linux,wsl,docker}.md`. |
| `PROTOCOL.md` | **drop from `marketing/`, 3-way merge into `main:docs/PROTOCOL.md`** | `docs/PROTOCOL.md` is on Astro side. Resolve in a separate commit on this same PR. See §2.18b. |
| `ARCHITECTURE.md`, `security-model.md`, `risk-key-material-in-python-memory.md` | **drop from `marketing/`, leave on `origin/website-dev`** | Engineering docs, not landing copy. They live in the repo on `main` already (or will after WOR-379 cross-link work). Phase 1 doesn't touch them. |
| `adversarial/*`, `research/*` | **drop from `marketing/`** | Engineering content. Phase 1 scope is landing-page surface only. Re-evaluate placement in a follow-up ticket if cross-linking is desired. |

**Sub-step #18a — canonical/OG/twitter sweep**

This is the bug-binder. The website branch self-identifies as
`worthless.cloud`. Every ported HTML file (and the webmanifest +
sitemap + JSON-LD inside HTML) must be rewritten to `wless.io`.

**Approach:** scripted, idempotent, reversible.

```sh
# In-tree rewrite. Surgical: only matches the specific URL forms.
grep -rli 'worthless\.cloud' marketing/ | while read -r f; do
  # Plain string. Case-insensitive (-I).
  perl -i -pe 's{https?://(www\.)?worthless\.cloud}{https://wless.io}gi' "$f"
done
```

**CI guard** — `.github/workflows/lint-marketing.yml` (new):

```yaml
- name: Block worthless.cloud references in marketing/
  run: |
    if git grep -niE 'worthless[.\\]cloud|worthless%2[Ee]cloud' -- marketing/ ; then
      echo "::error::worthless.cloud reference found in marketing/. Sweep step #18a missed something."
      exit 1
    fi
```

The literal-block scalar `run: |` is required so the `\\` inside the
character class survives YAML parsing (the WOR-455 prompt called this
out).

**Acceptance smoke (post-cutover, runs in `marketing-url-smoke.yml`
from WOR-457):**

```sh
curl -fsSL https://wless.io \
  | grep -iE 'canonical|og:url|twitter:url|twitter:domain' \
  | grep -i worthless\\.cloud && exit 1 || exit 0
```

**Sub-step #18b — `PROTOCOL.md` 3-way merge**

`origin/main:docs/PROTOCOL.md` is the Astro/Starlight canonical;
`origin/website-dev:docs/PROTOCOL.md` may have website-side edits
(security-relevant updates). Resolve:

```sh
git show origin/main:docs/PROTOCOL.md         > /tmp/protocol.main
git show origin/website-dev:docs/PROTOCOL.md  > /tmp/protocol.web
diff -u /tmp/protocol.main /tmp/protocol.web > phase1-audit/protocol.diff
```

If the diff is purely formatting, take `main`. If website carries
security-relevant updates, hand-merge into `main:docs/PROTOCOL.md` as
a **separate commit** in this PR. Do **not** put `PROTOCOL.md` in
`marketing/` — it's documentation, not landing copy.

**Gates:**
- `git grep -niE 'worthless[.\\]cloud' -- marketing/` returns nothing.
- `git ls-files marketing/ | grep -E '\.(md|CNAME)$'` returns nothing
  (we dropped all the MD files).
- Six HTML files present at expected paths.

**Rollback:** Pure file manipulation, all changes confined to
`marketing/`. To roll back, `git checkout marketing/` (deletes the
folder if not yet committed) or `git rm -r marketing/`.

**Decision rationale:** decisions 2, 3, 6.

---

### Step #19 — `wrangler-marketing.toml` at repo root

**File path:** `/wrangler-marketing.toml` (root, **not** in `marketing/`).

**Exact content:**

```toml
# Cloudflare Worker for wless.io marketing surface — WOR-455.
#
# Static-assets-only Worker (no `main` field). Modeled on
# `worthless-docs` (root wrangler.jsonc) but spec'd in TOML for
# clarity, with explicit named environments matching the
# `worthless-sh` two-env pattern.
#
# CRITICAL — `wrangler deploy` MUST be invoked with
# `--config wrangler-marketing.toml --env <env>`. Without `--config`,
# Wrangler 4 walks up the tree, finds root `wrangler.jsonc` first
# (the docs Worker), and refuses with "No environment named
# 'production'". The `deploy-marketing.yml` workflow enforces this.

name = "wless-marketing"
compatibility_date = "2026-04-26"

[assets]
directory = "./marketing/"
not_found_handling = "404-page"
# Workers Static Assets respects marketing/.assetsignore — see that
# file for the deny list (wrangler*.toml, package.json, etc.).

# Disable workers.dev fallback for production. Identical reasoning to
# workers/worthless-sh/wrangler.toml § env.production: parallel
# *.workers.dev surface bypasses the wless.io route, the security
# headers anchored to it, and any zone-level WAF rules. Production
# must only be reachable via the wless.io route.
#
# Phase 1 does NOT claim the wless.io/* route — that's Phase 2/3.
# Until then, production deploy targets *.workers.dev with workers_dev
# = false, which means production cannot be deployed at all in Phase 1.
# That's intentional: Phase 1 ships only to preview.

[env.preview]
name = "wless-marketing-preview"

[env.production]
name = "wless-marketing"
workers_dev = false

# Production route — NOT claimed in Phase 1. Uncommented and merged
# in Phase 3 cutover commit, gated by signed-tag verify.
# [[env.production.routes]]
# pattern = "wless.io/*"
# zone_name = "wless.io"
```

**Defense-in-depth — `marketing/.assetsignore`:**

```
# Wrangler 3.91+ honors this file the same way `.gitignore` works:
# any path matched here is excluded from the published asset bundle.
# Belt-and-braces against accidental inclusion of repo metadata.
wrangler*.toml
wrangler*.jsonc
.env*
.dev.vars
node_modules/
.git/
*.md
tsconfig.json
package*.json
.assetsignore
phase1-audit/
```

**Gate (run AFTER preview deploy in step #25; not in step #19):**

```sh
for p in wrangler.toml wrangler-marketing.toml wrangler.jsonc \
         .dev.vars .env package.json package-lock.json \
         tsconfig.json node_modules .assetsignore; do
  curl -fsI -o /dev/null -w "%{http_code} $p\n" \
    "https://wless-marketing-preview.<account>.workers.dev/$p"
done
```

All status codes must be 4xx (404 expected, 403 acceptable). Any 200
fails the gate.

**Rollback:** delete the two files. No live infrastructure touched.

**Decision rationale:** root config keeps assets folder publishing
clean and matches the existing repo pattern. `.assetsignore` is
Wrangler-native — strictly stronger than relying on `--config` alone.

---

### Step #20 — Security headers (Decision 1: Worker-emitted)

**Phase 1 deliverable:** the headers are baked into Worker code that
the Static Assets handler can read. Workers Static Assets supports a
custom `_headers` file (Cloudflare Pages-compatible) at the root of
the assets directory.

**File path:** `marketing/_headers`

**Exact content:**

```
/*
  Strict-Transport-Security: max-age=15552000; includeSubDomains; preload
  Content-Security-Policy: default-src 'self'; script-src 'self' https://tally.so; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src 'self' data:; frame-src https://tally.so
  X-Frame-Options: DENY
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
  Permissions-Policy: camera=(), microphone=(), geolocation=()
```

Notes:
- Six headers, byte-identical to §0.1 production capture.
- Cloudflare Workers Static Assets honors `_headers` per
  https://developers.cloudflare.com/workers/static-assets/headers/ — same
  syntax as Cloudflare Pages.
- `_headers` itself is not served (it's an asset-system metadata file),
  but as belt-and-braces it's also listed in `.assetsignore` (already).
- HSTS preload flag is preserved verbatim, but **the domain is not
  on the preload list** (Phase 0 surprise #2). Rollback remains
  fully reversible, which preserves Phase 3's safety story.

**Transform Rule disablement** is a Phase 2/3 dashboard step. Phase 1
ships the Worker with these headers ready; smoke contract in WOR-457
asserts each header appears **exactly once** in the production
response post-cutover, which catches both (a) Worker forgot to emit
and (b) Transform Rule still firing.

**Gate (post-deploy, in `marketing-url-smoke.yml`):**

```sh
expected_headers=(
  "strict-transport-security"
  "content-security-policy"
  "x-frame-options"
  "x-content-type-options"
  "referrer-policy"
  "permissions-policy"
)
for h in "${expected_headers[@]}"; do
  count=$(curl -fsI "https://wless-marketing-preview.<account>.workers.dev/" \
    | grep -ic "^${h}:" || true)
  if [ "$count" -ne 1 ]; then
    echo "::error::header ${h} appeared ${count} times (expected exactly 1)"
    exit 1
  fi
done
```

**Rollback:** delete `marketing/_headers`. Worker re-deploys with no
headers (until cutover), which means the Transform Rule still fires —
production state is unchanged.

**Decision rationale:** Decision 1 — one source of truth in
repo. `_headers` is the lowest-friction expression of that decision
that doesn't require writing a Worker `fetch` handler.

---

### Step #21 — WOR-454 install-command fix (closes WOR-454)

**Goal:** marketing copy contains at least one canonical install
command in a hero-prominent position.

**Source of truth:** `origin/website-dev:docs/index.html` (which
already has the WOR-326 redesign per `0657f09 feat(install): OpenClaw
panel` — agent installs, not human, and `a84aa63 feat: persona chips
hero + two-step install page with verified CLI flows`).

**Commands:**

```sh
# Confirm what the redesigned hero already says
grep -A 3 -iE 'pipx install|curl.*worthless|docker run|pip install worthless' \
  marketing/index.html
```

**Required:** at least one of (decision 5 priority order):
1. `curl -sSL https://worthless.sh | sh`
2. `pipx install worthless`
3. `docker run --rm worthless/worthless ...`
4. `pip install worthless` (only if WOR-326 design specifies)

If `marketing/index.html` post-port already contains #1 or #2, this
step is a verification-only no-op. If it doesn't, hand-edit the hero
to add `pipx install worthless` (lowest-risk addition; doesn't conflict
with any other marketing claim).

**CI guard** (in `lint-marketing.yml`):

```sh
if ! grep -qE 'pipx install worthless|curl.*worthless\.sh|docker run.*worthless' \
     marketing/index.html; then
  echo "::error::marketing/index.html missing canonical install command (WOR-454)"
  exit 1
fi
```

**Gate:** `lint-marketing.yml` green.

**Rollback:** revert the hero edit.

**Decision rationale:** decision 5. Hero copy is closed by WOR-326's
design; Phase 1 closes WOR-454 by virtue of porting that design.

---

### Step #22 — `wrangler dev` locally

**Goal:** confirm the Worker boots and serves the six HTML files +
six headers locally before any cloud deploy.

**Commands:**

```sh
# From repo root
npm install --no-save wrangler@^4
npx wrangler dev --config wrangler-marketing.toml --local --port 8787
# In a second shell:
curl -fsI http://localhost:8787/
curl -fsI http://localhost:8787/features.html
curl -fsI http://localhost:8787/coming-soon.html
curl -fsI http://localhost:8787/how-it-works.html
curl -fsI http://localhost:8787/memes.html
curl -fsI http://localhost:8787/blog/
# Internal-link smoke
npx --yes lychee --no-progress --offline --base ./marketing marketing/**/*.html
```

**Port:** 8787 (Wrangler default — pick anything 8000–9000, document
in PR body).

**Gates:**
- All 6 HTML files return 200.
- All 6 headers present on every response (count==1).
- `lychee` reports zero broken internal links.

**Rollback:** none — local-only.

**Decision rationale:** the cheapest possible evidence that the
Worker bundle is valid before consuming a CF deploy slot.

---

### Step #23 — WAF byte-sequence scan on migrated HTML

**Goal:** prevent CF WAF from rejecting future `wrangler deploy`
because some marketing copy contains a shell-injection-looking string
(e.g., a code snippet showing `curl … | sh`, an embedded one-liner in
a memes page, or a JSON literal with backticks).

**Background:** `workers/worthless-sh/scripts/embed-assets.mjs` ADR-001
documents that CF's WAF on `api.cloudflare.com` inspects multipart
upload parts and 403s when bytes match shell-injection signatures. The
worthless-sh Worker base64-encodes the entire `install.sh` to evade
this. The marketing Worker doesn't bundle `install.sh` — but the hero
literally **shows** `curl -sSL https://worthless.sh | sh`, which is
exactly the byte sequence that triggers the WAF.

**Commands:**

```sh
# Heuristic scan. Patterns from ADR-001 + common false-positives.
patterns=(
  '\\bcurl\\b.*\\|\\s*(sh|bash)\\b'
  '\\beval\\s*\\$\\('
  'wget\\b.*\\|\\s*(sh|bash)\\b'
  '/dev/tcp/'
)
for p in "${patterns[@]}"; do
  echo "==== ${p} ===="
  grep -rEn "${p}" marketing/ || echo "(none)"
done
```

If any pattern matches, choose:

a. **HTML-encode the visible characters** (e.g., `&#x7C;` for `|`,
   `&#x24;` for `$`). Renders identically; bytes change. Lowest-risk
   for a marketing surface.
b. **Move the snippet into a `<code>` block sourced from a separate
   file** that's referenced via JS from a non-installable origin
   (overkill for Phase 1).
c. **Base64-encode the snippet** and decode in-page with a tiny inline
   script (introduces script weight; fights CSP; rejected for marketing).

**Recommendation: (a).** Apply per-occurrence; document in commit
message.

**Gate:** A trial deploy attempt to preview (step #25) succeeds. If
CF WAF 403s the upload, return here and HTML-encode more aggressively.
The 403 manifests as `wrangler deploy` failing with a specific error
code; document the known signature in the runbook.

**Rollback:** revert HTML-encoding edits. Doesn't affect rendered
output.

**Decision rationale:** ADR-001 is the precedent; same constraint
applies to any payload uploaded via `wrangler deploy`.

---

### Step #24 — Lighthouse / accessibility quick check

**Goal:** baseline thresholds we won't regress past.

**Commands:**

```sh
# Against the local wrangler dev server from step #22
npx --yes @lhci/cli@latest autorun \
  --collect.url=http://localhost:8787/ \
  --collect.url=http://localhost:8787/features.html \
  --collect.url=http://localhost:8787/how-it-works.html \
  --upload.target=temporary-public-storage
```

**Acceptance thresholds (Phase 1 PR-blocking):**

| Category | Threshold | Rationale |
|---|---|---|
| Performance | ≥ 90 (mobile) | Static HTML + a few images; below 90 means the port broke something. |
| Accessibility | ≥ 95 | Marketing surface; must clear WCAG A. |
| Best practices | ≥ 95 | HTTPS, no console errors, modern image formats. |
| SEO | ≥ 95 | Canonical sweep + structured data should make this trivial. |

If any score is < threshold, treat as a Phase 1 blocker. Bumping the
threshold up later is preferable to landing with a regression baked in.

**Gate:** all four thresholds met on `index.html`, `features.html`,
`how-it-works.html`. (Other pages can ship below threshold but get a
follow-up ticket.)

**Rollback:** none.

**Decision rationale:** Phase 1 is the cheapest moment to set the
baseline; once cutover happens, regressions are real-traffic visible.

---

### Step #25 — `deploy-marketing.yml` workflow

**File path:** `.github/workflows/deploy-marketing.yml`

**Skeleton (modeled on `deploy-worker.yml`):**

```yaml
name: Deploy Worker (wless-marketing)

# WOR-455 Phase 1 — marketing Worker for wless.io.
# Modeled on .github/workflows/deploy-worker.yml (worthless-sh).
# Same threat model: signed-tag-only production, dispatch=preview only.

on:
  push:
    tags:
      # Distinct tag namespace from worthless-sh (`v*`). Marketing tags
      # are `marketing-v*` so a Worker-code release tag does not also
      # ship marketing copy and vice versa.
      - 'marketing-v*'
  workflow_dispatch:
    # LOAD-BEARING — production NOT in options (see deploy-worker.yml
    # for the WOR-323 reasoning). Adding production here re-opens
    # dispatch-to-prod with no signature check.
    inputs:
      target:
        description: "Deploy target (preview only — production is tag-triggered)"
        type: choice
        options: [preview]
        default: preview

permissions:
  contents: read

concurrency:
  group: deploy-marketing-${{ github.ref }}
  cancel-in-progress: false

jobs:
  verify:
    name: Verify (lint, sweep, tag signature)
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    outputs:
      target: ${{ steps.target.outputs.value }}
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6
        with:
          fetch-depth: 0
          persist-credentials: false

      - name: Verify tag GPG signature (fatal, tag-push only)
        if: github.event_name == 'push'
        env:
          MAINTAINER_PUBKEY: ${{ vars.MAINTAINER_GPG_PUBKEY }}
          MAINTAINER_FINGERPRINT: ${{ vars.MAINTAINER_GPG_FINGERPRINT }}
        run: bash .github/scripts/verify-tag.sh

      - name: Resolve deploy target
        id: target
        env:
          INPUT_TARGET: ${{ inputs.target }}
        run: |
          if [ "${GITHUB_EVENT_NAME}" = "push" ]; then
            echo "value=production" >> "$GITHUB_OUTPUT"
          else
            echo "value=${INPUT_TARGET}" >> "$GITHUB_OUTPUT"
          fi

      - name: Block worthless.cloud references in marketing/
        run: |
          if git grep -niE 'worthless[.\\]cloud|worthless%2[Ee]cloud' -- marketing/ ; then
            echo "::error::worthless.cloud reference found in marketing/."
            exit 1
          fi

      - name: Require canonical install command in hero
        run: |
          if ! grep -qE 'pipx install worthless|curl.*worthless\.sh|docker run.*worthless' \
               marketing/index.html; then
            echo "::error::marketing/index.html missing canonical install command (WOR-454)."
            exit 1
          fi

      - name: Forbid wrangler invocation without --config
        run: |
          # CI lint: every wrangler call in any workflow MUST be
          # `wrangler ... --config <file>`. Otherwise wrangler walks up
          # and could pick the wrong root config.
          bad=$(grep -RIEn 'wrangler (deploy|publish|dev)\b' .github/workflows/ \
                | grep -v -- '--config ' || true)
          if [ -n "$bad" ]; then
            echo "::error::wrangler invocation without --config:"
            echo "$bad"
            exit 1
          fi

  deploy:
    name: Deploy to Cloudflare
    needs: verify
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    environment:
      name: wless-marketing-${{ needs.verify.outputs.target }}
      url: >-
        ${{ needs.verify.outputs.target == 'production'
            && 'https://wless.io/'
            || 'https://dash.cloudflare.com/' }}
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6
        with:
          fetch-depth: 0
          persist-credentials: false

      - name: Re-verify tag GPG signature (defense-in-depth)
        if: github.event_name == 'push'
        env:
          MAINTAINER_PUBKEY: ${{ vars.MAINTAINER_GPG_PUBKEY }}
          MAINTAINER_FINGERPRINT: ${{ vars.MAINTAINER_GPG_FINGERPRINT }}
        run: bash .github/scripts/verify-tag.sh

      - name: Deploy via Wrangler
        # Per-Worker, per-env scoped tokens. Do NOT reuse
        # worthless-sh-deploy-* tokens (WOR-323/401 pattern).
        env:
          CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN_MARKETING }}
          CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
          DEPLOY_TARGET: ${{ needs.verify.outputs.target }}
        run: |
          npx wrangler@^4 deploy \
            --config wrangler-marketing.toml \
            --env "${DEPLOY_TARGET}"

  smoke:
    # Calls into marketing-url-smoke.yml (WOR-457). On preview deploys
    # only — production smoke is gated by Phase 2/3 cutover.
    name: Marketing URL smoke
    needs: deploy
    if: needs.verify.outputs.target == 'preview'
    uses: ./.github/workflows/marketing-url-smoke.yml
    secrets: inherit

  wire-attacks:
    name: Worker wire attacks (security probes)
    needs: deploy
    if: needs.verify.outputs.target == 'preview'
    uses: ./.github/workflows/worker-wire-attacks.yml
    secrets: inherit
```

**Required secrets / vars (must be created before first run):**

| Name | Type | Scope | Notes |
|---|---|---|---|
| `CLOUDFLARE_API_TOKEN_MARKETING` (preview) | secret | env: `wless-marketing-preview` | Scope: Edit Workers Scripts ONLY for `wless-marketing-preview`. NO zone scope. |
| `CLOUDFLARE_API_TOKEN_MARKETING` (production) | secret | env: `wless-marketing-production` | Scope: Edit Workers Scripts ONLY for `wless-marketing` + Edit Workers Routes for `wless.io` zone. Created **but not used** until Phase 3. |
| `CLOUDFLARE_ACCOUNT_ID` | secret | both envs | Already exists in repo for `worthless-sh-*` envs; reuse. |
| `MAINTAINER_GPG_PUBKEY`, `MAINTAINER_GPG_FINGERPRINT` | repo Variables | repo | Already exist (WOR-401). Reused. |

**Token rotation note:** the prompt says "do NOT reuse `worthless-sh-deploy-*`
tokens (per WOR-323/401 pattern)". Confirmed: each Worker gets its own
token, scoped to that Worker's resources only.

**Smoke wiring:** the `smoke` job calls `marketing-url-smoke.yml`,
which is **created in WOR-457**, not Phase 1. If WOR-457 hasn't shipped
yet at execution time, comment out the `smoke:` job with a `TODO:
WOR-457` and inline a minimal smoke (5-line curl check) until it
lands. The `wire-attacks:` job calls the existing
`worker-wire-attacks.yml`.

**Gate:** workflow is committed but not triggered. First trigger is a
maintainer-dispatch with `target=preview`.

**Rollback:** delete the workflow file. No infrastructure touched
until first dispatch.

**Decision rationale:** decision 4. Two-job split, signed-tag-only
production, scoped tokens — same hardening pattern as `worthless-sh`.

---

### Step #26 — Open the PR

**Title (product-headline format):**

> Stop wless.io from quietly losing its install command — single source of truth on `main` (WOR-455 Phase 1)

**Body (house format) — skeleton:**

```markdown
> wless.io is 226 commits divergent from `main`, marketing copy still
> self-identifies as worthless.cloud, the install command on the
> landing page is wrong, and a single edit that touches "marketing +
> install" spans two PRs on two branches.

**TL;DR:** Phase 1 of WOR-455 ports the `website-dev` landing copy
into `main:/marketing/`, behind a new Cloudflare Worker
(`wless-marketing`), with one source of truth for security headers.
Closes WOR-454. Does **not** cut DNS over yet — that's Phase 2/3.

# Summary
This PR consolidates the wless.io marketing surface onto `main` so
the next install-funnel fix is one PR, not three. We port the
`website-dev` redesign (WOR-326), correct every `worthless.cloud`
self-reference to `wless.io`, bake the WOR-299 security-header
posture into the Worker via `_headers`, and wire a tag-gated deploy
pipeline modeled on `worthless-sh` (WOR-323/401). The cutover itself
is a Phase 2/3 follow-up and lands behind a separate signed tag.

# What
- New `marketing/` tree at repo root with the six landing HTML files
  and supporting assets, ported from `origin/website-dev:docs/`.
- New `wrangler-marketing.toml` at repo root (named, two-env Worker
  config; production route commented out for Phase 1).
- New `marketing/_headers` baking the six WOR-299 / WOR-299-derived
  security headers verbatim from production capture.
- New `marketing/.assetsignore` belt-and-braces denylist.
- New `.github/workflows/deploy-marketing.yml` — tag-gated production,
  dispatch=preview only.
- New `.github/workflows/lint-marketing.yml` — guards
  `worthless.cloud` references, the WOR-454 install-command, and
  `wrangler` invocations missing `--config`.
- 3-way merge of `docs/PROTOCOL.md` if the website-dev branch carries
  security-relevant edits.

# Why
- One source of truth for the marketing surface (Phase 1 → Phase 4
  decommission of `origin/website` and `origin/website-dev`).
- Closes WOR-454 in the same PR that ports copy — the install
  command on the landing page becomes correct by construction.
- Security headers move from the zone Transform Rule (drift-prone)
  to a repo-tracked `_headers` file (decision 1, recorded in this PR).
- Deploy mechanism (decision 4) parities `worthless-sh` so the team
  has a single deploy mental model.

# How
- Step-by-step in `WOR-455-phase-1-plan.md` (this PR).
- Decision matrix recorded for each open question; rejected
  alternatives explicit.
- Pre-cutover state of wless.io captured in §0.1 of the plan.

# Tests
- [ ] `lint-marketing.yml` green (no `worthless.cloud`, install
      command present, no `wrangler` without `--config`).
- [ ] `wrangler dev` local smoke: 6 HTML files 200, all 6 security
      headers count==1.
- [ ] `lychee` internal-link smoke: 0 broken links.
- [ ] Lighthouse on index/features/how-it-works ≥ 90/95/95/95
      (perf/a11y/best/seo).
- [ ] WAF byte-sequence scan: no shell-injection signatures
      unencoded.
- [ ] Preview deploy succeeds; the leak-class probe (loop in §2.19)
      returns only 4xx.
- [ ] `marketing-url-smoke.yml` green against the preview URL.
- [ ] No production deploy occurs in this PR (route claim is gated by
      Phase 3 tag).

# Follow-ups
- WOR-457 — `marketing-url-smoke.yml` (this PR's smoke job depends on
  it; placeholder inline smoke until then).
- WOR-473 — `staging.wless.io` surface (Phase 2; this PR leaves room
  by defining `[env.preview]`).
- WOR-454 — closed by this PR (verified by lint guard).
- WOR-326 — landing redesign; merged in by virtue of the
  `website-dev` port.
- WOR-379 — cross-link discoverability; unblocked once Phase 4
  decommissions the legacy branches.
- WOR-299 — header parity verified by §0.1 capture; document the
  Transform Rule disablement runbook (Phase 2).

WOR-455
```

**Conventional Commit messages:**

| Commit | Type | Scope | Subject |
|---|---|---|---|
| Port content from website-dev | `feat` | `marketing` | port wless.io landing from website-dev (WOR-455) |
| Canonical/OG sweep | `fix` | `marketing` | rewrite worthless.cloud → wless.io (WOR-455) |
| Wrangler config | `feat` | `infra` | add wrangler-marketing.toml + _headers (WOR-455) |
| Deploy workflow | `feat` | `ci` | tag-gated marketing Worker deploy (WOR-455) |
| Lint workflow | `feat` | `ci` | lint-marketing guards (WOR-455) |
| PROTOCOL merge (if needed) | `docs` | `protocol` | 3-way-merge website-dev edits (WOR-455) |

**Decision rationale:** product-headline framing matches house format
(threat-defeated language). Body sequence (pull-quote → TL;DR →
What/Why/How/Tests/Follow-ups) per the prompt's spec.

---

## Section 3 — Reference data

### 3.1 Final layout of the new tree (under `main`)

```
/
├── marketing/                          # NEW — Worker assets
│   ├── _headers                        # NEW — six security headers
│   ├── .assetsignore                   # NEW — denylist
│   ├── .well-known/
│   │   └── security.txt
│   ├── android-chrome-192x192.png
│   ├── android-chrome-512x512.png
│   ├── apple-touch-icon.png
│   ├── blog/
│   │   └── index.html
│   ├── coming-soon.html
│   ├── favicon-16x16.png
│   ├── favicon-32x32.png
│   ├── favicon.ico
│   ├── favicon.png
│   ├── features.html
│   ├── hero.png
│   ├── how-it-works.html
│   ├── index.html                      # canonical install command
│   ├── llms.txt
│   ├── logo-transparent.png
│   ├── meme-jenga.png
│   ├── meme-llm-tower.png
│   ├── memes.html
│   ├── og-image.png
│   ├── robots.txt
│   ├── site.webmanifest
│   └── sitemap.xml
├── wrangler-marketing.toml             # NEW
├── wrangler.jsonc                      # UNCHANGED (worthless-docs)
├── workers/worthless-sh/               # UNCHANGED
└── .github/workflows/
    ├── deploy-marketing.yml            # NEW
    ├── lint-marketing.yml              # NEW
    ├── deploy-worker.yml               # UNCHANGED
    └── …
```

### 3.2 Inventory: `origin/website-dev:docs/*` → `marketing/*`

| Source path (on `website-dev`) | Destination | Action |
|---|---|---|
| `docs/index.html` | `marketing/index.html` | port + sweep |
| `docs/coming-soon.html` | `marketing/coming-soon.html` | port + sweep |
| `docs/features.html` | `marketing/features.html` | port + sweep |
| `docs/how-it-works.html` | `marketing/how-it-works.html` | port + sweep |
| `docs/memes.html` | `marketing/memes.html` | port + sweep |
| `docs/blog/index.html` | `marketing/blog/index.html` | port + sweep |
| `docs/og-image.png` | `marketing/og-image.png` | port (binary) |
| `docs/hero.png` | `marketing/hero.png` | port (binary) |
| `docs/logo-transparent.png` | `marketing/logo-transparent.png` | port (binary) |
| `docs/meme-jenga.png` | `marketing/meme-jenga.png` | port (binary) |
| `docs/meme-llm-tower.png` | `marketing/meme-llm-tower.png` | port (binary) |
| `docs/apple-touch-icon.png` | `marketing/apple-touch-icon.png` | port (binary) |
| `docs/android-chrome-192x192.png` | `marketing/android-chrome-192x192.png` | port (binary) |
| `docs/android-chrome-512x512.png` | `marketing/android-chrome-512x512.png` | port (binary) |
| `docs/favicon.ico` | `marketing/favicon.ico` | port (binary) |
| `docs/favicon.png` | `marketing/favicon.png` | port (binary) |
| `docs/favicon-16x16.png` | `marketing/favicon-16x16.png` | port (binary) |
| `docs/favicon-32x32.png` | `marketing/favicon-32x32.png` | port (binary) |
| `docs/site.webmanifest` | `marketing/site.webmanifest` | port + sweep |
| `docs/sitemap.xml` | `marketing/sitemap.xml` | port + sweep |
| `docs/robots.txt` | `marketing/robots.txt` | port + sweep |
| `docs/llms.txt` | `marketing/llms.txt` | port + sweep |
| `docs/.well-known/security.txt` | `marketing/.well-known/security.txt` | port + verify Contact URL |
| `docs/CNAME` | — | **drop** (GitHub Pages artifact) |
| `docs/install-github-actions.md` | — | **drop** (stale; canonical on `main:docs/install/`) |
| `docs/install-mcp.md` | — | **drop** (stale) |
| `docs/install-openclaw.md` | — | **drop** (stale) |
| `docs/install-self-hosted.md` | — | **drop** (stale) |
| `docs/install-solo.md` | — | **drop** (stale) |
| `docs/install-teams.md` | — | **drop** (stale) |
| `docs/PROTOCOL.md` | `docs/PROTOCOL.md` (on `main`) | **3-way merge** (§2.18b) — not in `marketing/` |
| `docs/ARCHITECTURE.md` | — | drop (engineering doc, not landing copy) |
| `docs/security-model.md` | — | drop |
| `docs/risk-key-material-in-python-memory.md` | — | drop |
| `docs/adversarial/*` | — | drop (engineering doc) |
| `docs/research/*` | — | drop (engineering doc) |

### 3.3 Hard guardrails (cross-checked at PR time)

- [ ] `docs.wless.io` deploy plumbing untouched (root `wrangler.jsonc`
      unchanged; CF Workers Builds for `worthless-docs` unchanged).
- [ ] `workers/worthless-sh/` untouched.
- [ ] `worthless.cloud → wless.io` 301 not touched (lives on
      `worthless.cloud` zone, not in this repo).
- [ ] No DNS records added/removed on `wless.io` zone in this PR.
- [ ] `wless.io/*` route is **not** claimed (commented out in
      `wrangler-marketing.toml`).
- [ ] No `wrangler deploy` invocation in this PR run (preview deploy
      happens on first `workflow_dispatch` after merge — separate
      action).
- [ ] Branch name follows Conventional Branch prefix (`feature/wor-455-…`).
- [ ] Each commit references WOR-455 in the message body.

### 3.4 Verification commands (run in order during execution)

```sh
# 0 — environment
pwd && git branch --show-current
git fetch origin website website-dev main

# 1 — port
mkdir -p marketing/{blog,.well-known}
# (...per §2.18 commands)

# 2 — sweep
grep -rli 'worthless\\.cloud' marketing/ \
  | xargs -I{} perl -i -pe 's{https?://(www\\.)?worthless\\.cloud}{https://wless.io}gi' {}
git grep -niE 'worthless[.\\\\]cloud|worthless%2[Ee]cloud' -- marketing/ \
  && { echo "FAIL: residual worthless.cloud"; exit 1; } || echo "OK: sweep clean"

# 3 — local smoke
npx wrangler@^4 dev --config wrangler-marketing.toml --local --port 8787 &
WPID=$!
sleep 3
for path in / /features.html /coming-soon.html /how-it-works.html /memes.html /blog/; do
  curl -fsI "http://localhost:8787${path}" | head -n 1
done
for h in strict-transport-security content-security-policy x-frame-options \
         x-content-type-options referrer-policy permissions-policy; do
  count=$(curl -fsI "http://localhost:8787/" | grep -ic "^${h}:" || echo 0)
  echo "${h}: ${count}"
  [ "$count" -eq 1 ] || echo "FAIL: ${h} count=${count}"
done
kill $WPID

# 4 — accessibility / link smoke
npx --yes lychee --no-progress --offline --base ./marketing 'marketing/**/*.html'

# 5 — WAF byte scan
grep -rEn '\\bcurl\\b.*\\|\\s*(sh|bash)\\b' marketing/  # expect rendered, encoded, or moved

# 6 — leak-class scan (after preview deploy only — Phase 1 manual step)
for p in wrangler.toml wrangler-marketing.toml wrangler.jsonc \
         .dev.vars .env package.json package-lock.json \
         tsconfig.json node_modules .assetsignore _headers; do
  curl -fsI -o /dev/null -w "%{http_code} $p\n" \
    "https://wless-marketing-preview.<ACCOUNT-SUBDOMAIN>.workers.dev/$p"
done
```

### 3.5 Rollback summary by step

| Step | Rollback |
|---|---|
| #16 audit | n/a (read-only) |
| #17 strategy | n/a (decision artifact) |
| #18 port | `git rm -r marketing/` (uncommitted) or revert port commits (committed) |
| #18a sweep | `git revert <sweep-sha>` |
| #18b PROTOCOL merge | `git revert <merge-sha>` |
| #19 wrangler-marketing.toml | delete file; no infra effect (route not claimed) |
| #20 _headers | delete `marketing/_headers`; preview Worker serves no headers (production unchanged — Transform Rule still fires there) |
| #21 install command | revert hero edit |
| #22 wrangler dev | n/a (local) |
| #23 WAF encoding | revert encoding edits |
| #24 Lighthouse | n/a (measurement) |
| #25 deploy-marketing.yml | delete workflow file; no triggers fired |
| #26 PR | close PR |

Phase 1 has zero irreversible actions. The first irreversible action
is the **preview** deploy (consumes a Worker slot and an account
subdomain), which is itself reversible by `wrangler delete
wless-marketing-preview` or via dashboard.

---

## Section 4 — Open questions to answer before execution

The decisions in §1 are resolved with explicit recommendations.
These remaining items are **operator-confirmation** that don't block
Phase 1 planning but must be confirmed before the PR ships:

1. **Confirm `worthless.cloud → wless.io` 301 lives on the
   `worthless.cloud` zone** (per WOR-298). Phase 1 doesn't touch it,
   but Phase 2/3 cutover assumes it's already there.
2. **Confirm `worthless-docs` Workers Builds is wired** to auto-deploy
   on push to `main` when `docs/**` or `wrangler.jsonc` changes —
   adding `marketing/**` does **not** retrigger it, but a misconfigured
   Workers Build could pick up the new tree and try to deploy it.
   Verify by inspecting the Workers Builds config in the dashboard.
3. **Confirm `MAINTAINER_GPG_PUBKEY` and `MAINTAINER_GPG_FINGERPRINT`
   repo Variables are populated** (WOR-401) — the new
   `deploy-marketing.yml` re-uses them; without them the verify job
   fail-closes on every tag push.
4. **Confirm the WOR-326 design pinned in `origin/website-dev` matches
   our decision-5 ordering** (curl → pipx → docker). Spot-check
   `origin/website-dev:docs/index.html` hero section before merging.
5. **Confirm `marketing-url-smoke.yml` (WOR-457) is on the schedule
   for landing before Phase 2 cutover.** If WOR-457 slips, Phase 1
   smoke job either (a) inlines a 5-line curl placeholder or (b)
   blocks on WOR-457 — pick policy now.
6. **Confirm scoped CF API tokens
   (`CLOUDFLARE_API_TOKEN_MARKETING`, both envs) will be created**
   before first dispatch. Phase 0 audit found the existing
   `worthless-sh-deploy-*` tokens; do not reuse them.
7. **Confirm the orphan `worthless-sh-test-minimal` Worker** (last
   modified 2026-04-26 per Phase 0 audit) is safe to delete in Phase 4
   cleanup — Phase 1 doesn't touch it but it's adjacent to the new
   `wless-marketing-preview` namespace.

---

## Section 5 — Things explicitly NOT in Phase 1

- DNS A-record swap on `wless.io` apex / `www`. (Phase 3.)
- Claiming `wless.io/*` route on the Worker. (Phase 3 — route block in
  `wrangler-marketing.toml` is commented out.)
- Disabling the zone "Security Headers" Transform Rule. (Phase 2/3
  dashboard step; Worker emits `_headers` regardless.)
- Lowering DNS TTLs pre-cutover. (Already at 300s per Phase 0 — no
  action needed.)
- Touching the `worthless.cloud → wless.io` 301. (Lives on the
  `worthless.cloud` zone.)
- Touching the email routing (`route1/2/3.mx.cloudflare.net`, SPF,
  DMARC). (Orthogonal — Phase 0 confirmed.)
- Deleting `origin/website` or `origin/website-dev`. (Phase 4
  cleanup, only after the new Worker has owned production for ≥ N
  days.)
- Deleting the orphan `worthless-sh-test-minimal` Worker. (Phase 4.)
- Implementing WOR-379 cross-linking. (Blocked by WOR-455; unblocks
  on Phase 4.)
- Implementing WOR-473 `staging.wless.io`. (Phase 2 surface; this PR
  leaves room via `[env.preview]`.)
- HSTS preload submission. (Out of scope until cutover is stable;
  preload remains advertised but unsubmitted, preserving rollback.)
- Editing the WOR-455 ticket itself. (Phase 1 is execution; ticket
  edits >10KB go via `issueUpdate` mutation when applicable.)

---

**End of plan.**
