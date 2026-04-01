# Phase 04.1 — Final Plan (from discuss-phase + expert analysis)

## Recommended Phase Split

Phase 04.1 should split into three sub-phases to avoid documenting broken code:

### 04.1a — Code Prerequisites (before docs)
- Fix `worthless wrap` crash on fresh DB (worthless-fit, P0)
- Fix 93 ruff lint errors (worthless-fc9, P1)
- Standardize port to 8787 everywhere (README says 8443, code says 8787)
- Fix 8 test failures / scipy dep (worthless-c0q, P1)

### 04.1b — README & Docs Rewrite
All decisions below apply to this sub-phase.

### 04.1c — Header Rename (separate branch/PR)
- Rename `x-worthless-alias` → `x-worthless-key` across all source, tests, docs (~103 occurrences)
- Mechanical refactor touching proxy request handling + test assertions
- Needs its own test pass, own PR — merge conflict risk if bundled with docs

---

## Naming Decisions

- **CLI vocabulary**: `lock/unlock` everywhere in user-facing docs
- **PRD**: Leave as-is (historical doc), add glossary note: "PRD says enroll, shipped CLI says lock — same operation"
- **Internal modules**: `enroll_stub.py` keeps its name (protocol-level term is correct internally)
- **Wire protocol header**: `x-worthless-alias` → `x-worthless-key` (separate branch, 04.1c)
  - "alias" is user-facing (README curl examples, error messages), not self-documenting
  - "key" matches mental model, parallels x-api-key
  - Pre-release, zero users, zero breaking change cost

## Port & Canonical Path

- **8787** is the canonical port everywhere (already the default in `up.py`)
- Update README (8443→8787), install-solo (9191→8787), any other references
- **CLI-first install path**: git clone + `uv pip install -e .` + `worthless lock` + `worthless up`
- **Two gates for future paths**: (1) `pip install worthless` when PyPI published, (2) `curl worthless.sh | sh` when domain purchased
- Until then, from-source is the only documented working path

## README Structure (~1,000 words target)

Research-backed: 800-1,500 words is sweet spot for CLI tools. First 3 sections get ~75% of attention. Beyond 2,000 words, star velocity drops.

### Section Order:
1. **Title + tagline** — "Make your API keys worthless to steal" + 4 badges (Python 3.10+, AGPL-3.0, pre-release, tests)
2. **Hero image** — Visual showing the .env problem / split-key solution. Shareable, stops scrolling. (Last task in phase — doesn't block anything)
3. **Three-line value prop** — split, proxy, budget-blown-means-key-never-forms
4. **Pre-release callout** — GitHub `[!NOTE]` box. Honest, familiar.
5. **Quickstart** — REAL command output from lock, wrap, status. No fabrication. Wrap-first progressive disclosure.
6. **"What just happened"** — Inline explanation showing equivalent manual steps (worthless up + BASE_URL). User reads it, doesn't run it.
7. **`worthless status` output** — Trust anchor. Transparency as first-class feature.
8. **"Undo everything"** — `worthless unlock` + `worthless down`. Reversibility reduces anxiety.
9. **How it works** — ASCII diagram of split/proxy/reconstruct flow
10. **CLI reference table** — All commands, one-line descriptions
11. **Positioning (NOT a comparison table)** — 2 sentences: "Every secrets tool protects the key until your app gets it. Worthless protects you after it leaks." Brutus killed the comparison table — apples to oranges invites HN criticism.
12. **"What Worthless does NOT protect against"** — Explicit non-goals. Highest trust signal per security research.
13. **Security section** — Link to SECURITY_RULES.md, name crypto primitives (XOR, HMAC), "no novel cryptography", honest no-audit-yet. NO Rust mention (no Rust code exists). NO OpenSSF badge (score would be low, worse than no badge).
14. **Pre-commit hook** — 5 lines showing `.pre-commit-config.yaml` setup with `worthless-scan`. Pre-commit framework, not plain git hook.
15. **Minimal Dev + Contributing footer** — `uv sync`, `pytest`, `ruff check`. "See SECURITY_RULES.md before touching crypto."

### What the README does NOT include:
- Install links to planned docs (solo, mcp, openclaw, self-hosted, teams) — cut from README, live in docs/
- Comparison table — replaced with 2-sentence positioning
- GIF/terminal animation — defer until CLI UX is polished
- Centered HTML headers — plain markdown only, more accessible
- Universal `--json` claim — only status and scan support it
- Fabricated terminal output — use actual command output
- Rust hardening mention — no Rust code exists
- OpenSSF/mutation testing badges — defer until prerequisites met

## Integration Docs

- **All integration docs**: rewrite local-first (worthless up + BASE_URL), cloud sections get `[!NOTE] Planned` banners
- **install-mcp.md**: show .mcp.json pointing at localhost:8787, cover Claude Code + Cursor + Windsurf
- **install-openclaw.md**: `[!NOTE] Planned` banner only. ClawHub skill requires MCP server which doesn't exist yet.
- **install-self-hosted.md + install-teams.md**: keep with `[!NOTE] Planned` banners. Consistent treatment.
- **install-github-actions.md**: real workflow.yml (copy-pasteable), tested in 04.2
- **examples/ directory**: create with mcp.json + ci workflow.yml. Defer openclaw.yaml.
- Smaller delta when cloud ships — just swap in cloud URLs instead of writing from scratch

## Inner Contract Doc

- **Slim wire protocol doc** at `docs/PROTOCOL.md`
- Contents: headers (x-worthless-key, x-worthless-shard-a), proxy endpoints, error codes, env vars
- **Plus half-page Security Model section**: shard custody (Shard A stays client-side, never transmit), gate-before-reconstruct, server-side direct call
- NOT internal architecture, NOT XOR mechanics, NOT KMS internals
- Pre-release banner at top
- Referenced by SKILL.md and integration docs

## E2E Setup Test

- **04.1**: manual walkthrough of quickstart to verify it works. Real output inlined in README.
- **04.2**: automated repeatable test using live uvicorn harness (WOR-77)

## Pre-commit Scan Wiring

- README section (5 lines) showing: install pre-commit framework hook, commit with unprotected key, see it blocked
- Manual walkthrough to verify
- Pre-commit framework (not plain git hook) — `.pre-commit-hooks.yaml` already exists

## Visual Identity (Hero Image)

Five concepts documented in research. Owner decision: keeping the hero image.

Best candidates:
1. **"The Heist Gone Wrong"** — Fake terminal: attacker finds key, tries to use it, gets 403. Most shareable with developer audience.
2. **"The .env Graveyard"** — xkcd-style illustration. Humor + visual distinctiveness.
3. **"Nebraska Man, But For Keys"** — xkcd homage. Split block holds the tower.

Implementation: last task in phase, doesn't block anything. PNG at 1280x640px (GitHub social preview ratio). Must work in dark mode.

## Launch Strategy (NOT in 04.1, but captured for later)

- Blog post can be written now (independent of product readiness)
- HN Show: NOT until PyPI + CLI tested on a stranger
- Full launch plan in `.planning/research/04.1-readme-launch/launch-strategy.md`
- Trend timing is excellent (GitGuardian report, $82K Gemini incident, Claude Code CVEs)
- Competitive positioning: "Every secrets tool protects the key until your app gets it. Worthless protects you after it leaks."

## Filed Issues

| Issue | Title | Priority |
|---|---|---|
| worthless-edb | Go public: release worthless repo (epic) | P2 |
| worthless-fit | wrap crashes on fresh DB: decoy_hash | P0 |
| worthless-c0q | 8 test failures: scipy dep | P1 |
| worthless-fc9 | 93 ruff lint errors | P1 |
| worthless-o6n | Swap integration docs to cloud URLs | P4 |
| worthless-67o | Publish to PyPI | P2 |
| worthless-27g | Purchase worthless.sh domain | P2 |
| worthless-05e | Create SECURITY.md | P2 |
| worthless-wij | Create SKILL.md | P2 |
| worthless-txn | Confirm AGPL-3.0 license | P3 |
| worthless-yoh | Align pyproject.toml version | P3 |
| worthless-67k | Create THREAT_MODEL.md | P3 |

## Research Files

All in `.planning/research/04.1-readme-launch/`:
- `oss-launch-research.md` — viral launch patterns from 10+ repos
- `trust-signals-research.md` — badges, security signals, what's credible vs theater
- `competitive-analysis.md` — positioning vs 9 competitors, $82K incident
- `launch-strategy.md` — sequenced HN/Twitter/Reddit plan
- `api-key-security-trend-analysis-2026-03.md` — market timing, GitGuardian data
- `launch-reality-check.md` — Karen's bug findings and codebase state
- `readme-conversion-research.md` — length/conversion data, scroll depth
- `readme-patterns-research.md` — patterns from top GitHub repos
