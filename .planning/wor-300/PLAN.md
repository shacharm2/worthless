# WOR-300 — `worthless.sh` Install Endpoint (Cloudflare Worker)

**Status:** Planning complete. Implementation pending.
**Branch:** `feature/wor-300-worker-implementation`
**Parent:** [WOR-209](https://linear.app/plumbusai/issue/WOR-209)
**Blocks:** WOR-302 (trust tier 1), WOR-304 (AI audit), WOR-301 (trust epic)
**Blocked-by:** WOR-321 (multi-config lock), WOR-322 (MCP parity)

---

## One-line story

`curl worthless.sh | sh` → `worthless lock` → `worthless up` → your app code keeps working, real API key is now a local proxy token.

---

## Personas (primary)

- **P3 (AI agent)** — highest volume; demands JSON output, stable exit codes, `--yes` flag, idempotency.
- **P1 (dev terminal)** — demands Bun-style banner, `worthless lock` preview before mutation, clear next-step.
- **P6 (OpenClaw user)** — day-1; `worthless lock` must auto-detect and rewrite `openclaw.json` same as `.env`.

Full persona breakdown: `research/01-personas.md`.

---

## Architecture decision: **Option A (inline) + verified-deploy pipeline**

- `install.sh` bundled into Worker at build time via Wrangler Text rule.
- `docker-install.sh` ALSO bundled for the `/docker` path (both install flavors served).
- Worker emits verifiability headers: `X-Worthless-Script-Sha256`, `X-Worthless-Script-Tag`, `X-Worthless-Script-Commit`.
- Sigstore-signed `.sig` file published on GitHub release.
- Protected tag patterns (`v*`) + signed-tag verification at deploy time.

Full decision + threat-model rationale: `research/04-architecture-decision.md` + `research/09-threat-model.md`.

---

## Phases

### Phase 0 — Confirm blockers resolved / sequenced

- [ ] WOR-321 (multi-config `lock`) spec'd; implementable in parallel
- [ ] WOR-322 (MCP parity — `up`, `down`, `unlock`, `enroll` as MCP tools, JSON output contract, exit codes) spec'd
- [ ] WOR-126 (service-layer extraction) referenced; MCP parity depends on it
- [ ] WOR-249 (Docker path content) acknowledged; `docker-install.sh` stub OK for day 1

### Phase 1 — Worker test scaffolding (TDD foundation)

Extend existing `workers/worthless-sh/` tests to enable mocked fetch/bundling:
- [ ] `vitest.config.ts` wires test bindings (`REDIRECT_URL`)
- [ ] `test/_helpers.ts` — canonical bundled install.sh fixture, assertion helpers
- [ ] Confirm the 4 existing RED tests still collect (`ua-curl`, `ua-browser`, `ua-missing`, `explain`)

### Phase 2 — Adversarial / attack / chaos tests (RED)

New test files before implementation:
- [ ] `test/security.test.ts` — SSRF, header injection, UA spoof, shell metachar in UA, `X-Content-Type-Options: nosniff` asserted, no `Access-Control-Allow-Origin: *`, HEAD request parity
- [ ] `test/chaos.test.ts` — upstream 5xx (N/A for inline, assert inline always succeeds), concurrent requests, worker exception doesn't leak stack trace
- [ ] `test/edge-cases.test.ts` — null-byte UA, 10KB UA, mixed-case (`CURL/8.4.0`), positive-match (not substring), non-`/` paths, query variants (`?explain=0`, `?EXPLAIN=1`), POST/PUT/DELETE methods
- [ ] `test/docker-path.test.ts` — `/docker` serves `docker-install.sh` for curl UA, 302 for browser, `?explain=1` works for Docker path too
- [ ] `test/headers.test.ts` — response header contract (content-type, cache-control, nosniff, HSTS, X-Worthless-Script-*)

### Phase 3 — Minimal implementation (GREEN)

- [ ] `src/ua.ts` — `isCurlFamily(ua: string): boolean` positive-match allowlist
- [ ] `src/index.ts` — route handler: path `/` vs `/docker`, UA branching, query `?explain=1`, response headers
- [ ] `src/walkthrough.ts` — loads walkthrough txt, returns Response
- [ ] Update `wrangler.toml` — add `[[rules]]` Text rule for `.sh` bundling; drop `GITHUB_RAW_URL` env var
- [ ] `src/install.ts` — `import INSTALL_SH from "../../install.sh"`
- [ ] `src/docker-install.ts` — `import DOCKER_INSTALL_SH from "../../docker-install.sh"` (stub for v1 if WOR-249 not done)

### Phase 4 — Walkthrough content + banner copy

- [ ] `src/walkthrough.txt` — 40–60 line plain text walkthrough of install.sh (see `research/07`)
- [ ] Banner update in `install.sh` (Bun-style, see `research/07`)
- [ ] Same for `docker-install.sh`
- [ ] `brutus` LLM review of walkthrough wording

### Phase 5 — Deploy configuration

- [ ] `wrangler.toml` — production `routes` entry (`worthless.sh/*`), compatibility flags
- [ ] `.github/workflows/deploy-worker.yml` — tag-triggered, with signed-tag verification step
- [ ] GitHub repo ruleset: protected `v*` tag patterns, no force-push
- [ ] Actions environment `worthless-sh-prod` with required reviewers
- [ ] Cloudflare API token scoped to Worker-only, stored in the environment
- [ ] `workers/worthless-sh/DEPLOY.md` runbook — DNS (A/AAAA/CAA/MX null/DMARC/SPF), SSL Full Strict, HSTS ON, Bot Fight Mode OFF

### Phase 6 — Security hardening

Per `research/10-security-quick-wins.md`:
- [ ] `.semgrep/install-sh.yml` rules: sha256-pin required, no `eval $(curl)`, no base64 payloads
- [ ] Sigstore signing job in release workflow
- [ ] Signed git tag requirement enforced via ruleset
- [ ] Deploy pipeline verifies `install.sh.sig` before `wrangler deploy`

### Phase 7 — Manual dogfood (human test)

Before PR opens:
- [ ] Spin up `docker run -it --rm ubuntu:24.04 bash` (or fresh VM)
- [ ] `apt-get update && apt-get install -y curl`
- [ ] Run `curl https://worthless.sh | sh` against preview deploy — walk the full P1 journey
- [ ] Repeat on `alpine:3.20`, `debian:12`
- [ ] Repeat on macOS host
- [ ] Run `curl https://worthless.sh/docker | sh` — walk P4 journey
- [ ] Run `curl https://worthless.sh?explain=1` + verify sha256 matches
- [ ] Try `worthless lock` on a test project with `.env` + `openclaw.json` — verify both rewritten, backup created, consent gated

### Phase 8 — Pre-merge audit (Wave 5)

- [ ] `karen` reality check on completion vs plan
- [ ] `brutus` second pass (after implementation, different targets than Wave 3)
- [ ] `Jenny` spec compliance against PLAN.md
- [ ] Open PR, get CodeRabbit review, address findings

---

## Definition of Done

- All 4 original RED tests + ~25 new tests green
- Coverage ≥90% on `src/`
- `X-Worthless-Script-Sha256` header present and matches `sha256(install.sh)` in CI
- Sigstore `.sig` published on release
- `worthless.sh` + `worthless.sh/docker` both serve correct payloads to curl + 302 browsers
- `?explain=1` returns walkthrough for both paths
- DEPLOY.md walks through every CF step successfully (verified by a human)
- WOR-321 + WOR-322 merged (blockers)
- PR reviewed, green CI, brutus/karen/Jenny pass

---

## Out of scope (separate tickets)

- Docker Hub image content, multi-arch, non-root user (WOR-249)
- SLSA provenance, SBOM, Socket.dev (WOR-303)
- OSSF Scorecard badge (WOR-302)
- "Audit with AI" buttons on wless.io (WOR-304 — partial via website-dev)
- Version query (`worthless.sh?v=1.2.3`), analytics, KV-backed stats (post-launch)

---

## Research index

| File | What it covers |
|---|---|
| `research/01-personas.md` | P1–P7 ranking, magic moments, must-discover features |
| `research/02-industry-patterns.md` | rustup/uv/bun/docker/mise survey — what to copy, avoid |
| `research/03-ux-journeys.md` | Per-persona step-by-step flows, consent, failure UX |
| `research/04-architecture-decision.md` | Option A chosen + why, required composite defenses |
| `research/05-docker-product.md` | Two-path install decision (C), WOR-249 contradiction resolved |
| `research/06-docker-ux.md` | Docker path journey map, consent gate at alias write |
| `research/07-banner-and-explain.md` | Copy requirements + deferred to Phase 4 |
| `research/08-mcp-capability-audit.md` | MCP surface vs CLI surface, 6 missing tools |
| `research/09-threat-model.md` | 100-finding threat model, top 3 ship-blockers identified |
| `research/10-security-quick-wins.md` | Semgrep rules, GitHub rulesets, Sigstore CI stanza |

---

## Linear scope chain

```
WOR-300 (this ticket — Worker endpoint)
├── blocked-by WOR-321 — multi-config `lock` (auto-detect .env + openclaw.json + others)
├── blocked-by WOR-322 — MCP parity (add up/down/unlock/enroll as tools, JSON output, exit codes)
├── blocks WOR-302 — trust tier 1
├── blocks WOR-304 — AI audit buttons
└── related WOR-249 — Docker install path (docker-install.sh content)
```
