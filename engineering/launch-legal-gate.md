# Launch Legal Gate — checklist + risk register

> "Last thing checked before the project is promoted. Verify everything is done, or explicitly deferred with a trigger."

Internal launch-readiness sign-off. Each row is **✓ done** / **⏳ deferred (with trigger)** / **✗ blocker**. Evidence is a real file path or a Linear ticket, not a promise.

## Reality note — the repo is already public

The repo flipped to **public on 2026-03-14** (`isPrivate: false`). So this is no longer a gate *before* a future flip — it is a **verify-what's-in-place + close-the-gaps** list. Anything still open here is already exposed, which raises the priority of the history scrub (see risk register).

Verified state as of 2026-06-13 (HEAD on `main`).

## Checklist

### Repo legal files (`shacharm2/worthless`)
- [x] `LICENSE` — full AGPL-3.0 text present ✓
- [x] `NOTICE` — present ✓ (WOR-575)
- [x] `SECURITY.md` — present, best-effort/AS-IS, working contact ✓ (WOR-575)
- [x] `CONTRIBUTING.md` — present ✓ (WOR-575)
- [x] `CLA.md` — present ✓. **Note:** the project ships *with* a CLA (sublicensable grant), which supersedes the ticket's original "DCO-only, no CLA" assumption.
- [ ] `README.md` AS-IS disclaimer block — **✗ CONFIRMED MISSING.** No AS-IS / warranty / liability / "at your own risk" language anywhere in the 122-line README. WOR-575 expected a prepended disclaimer block; it was not done. Fix: prepend a short AS-IS block (the LICENSE already disclaims warranty under AGPL sections 15-16, but the README block is the visible belt-and-suspenders).
- [x] SPDX `license` in package manifests — `AGPL-3.0-only` ✓ (WOR-555, merged)
- [x] CLI AS-IS first-run notice — ✓ (WOR-488, merged)

### Licensing / strategy
- [x] Dual-license **strategy** documented — ✓ **kept INTERNAL** in the separate private repo, deliberately not in this public repo (WOR-533). Public repo carries facts only (LICENSE/CLA/CONTRIBUTING).

### Trademark & registration
- [x] Trademark clearance — PROCEED decision logged ✓ (WOR-574)
- [ ] Defensive TM registration — **⏳ deferred.** Trigger: first commercial-license sale OR a visible squatter (WOR-521).
- [ ] Trademark *name-use policy* — **✗ not written.** Flagged by legal review: AGPL covers copyright, not the name "Worthless." File it.

### Infrastructure
- [x] Cloudflare email aliases active — `legal@` / `privacy@` / `security@` ✓ (WOR-576)
- [x] Commit provenance CI workflow — `verify-commit-provenance.yml` present ✓ (WOR-590, merged)
- [ ] Provenance check set as a **required** status check — **✗ not active.** A workflow can't self-add; set it in branch protection.
- [ ] DCO / CLA enforcement on PRs — **✗ no `dco.yml` / CLA-Assistant wired.** CONTRIBUTING + CLA require sign-off, but nothing enforces it in CI yet.

### Website legal surface
- [ ] TERMS page (`/terms`) — **⏳ pending**, lives on the website not the repo (WOR-663, Codex). Draft text exists (WOR-520 attachments).
- [ ] PRIVACY page (`/privacy`) — **⏳ pending** (WOR-663, Codex).

## Risk register (ranked)

| # | Risk | Severity | State |
|---|---|---|---|
| 1 | **History not scrubbed while repo is already public** — personal Gmail + `shachar-ug` identity + a dead test key are exposed in history right now | **High** | WOR-586 pending; recipe dry-run-proven, run when PR queue is small |
| 2 | **README AS-IS disclaimer confirmed missing** — no warranty/risk language in the 122-line README; weakens the "you run this at your own risk" posture | Medium | fix: prepend a short AS-IS block |
| 3 | **Provenance check not required** — the signing gate doesn't actually block until added to branch protection | Medium | operator: 1 config change |
| 4 | **No name-use / trademark policy** — forks can ship "Worthless"-branded builds; brand is the commercial moat | Medium | unticketed — file it |
| 5 | **No DCO/CLA CI enforcement** — outside contributions could merge without a signed grant | Low (solo now) | wire when accepting contributions |
| 6 | **TERMS/PRIVACY not yet live** — needed before the hosted service takes real users, not for the code repo | Low | WOR-663 (website) |

## Go / no-go

The **code repo's** legal surface is essentially complete (LICENSE, CLA, SECURITY, NOTICE, CONTRIBUTING, SPDX, provenance). Since it is already public, there is no "hold the launch" decision left for the repo — only **close-the-gaps**, in priority order:

1. Run the **history scrub** (risk #1) — the one genuinely exposed item.
2. Verify/fix the **README disclaimer** (risk #2).
3. Make the **provenance check required** (risk #3).
4. File the **trademark name-use policy** (risk #4).

TERMS/PRIVACY (website) and DCO/CLA enforcement gate the *hosted service* and *accepting contributions* respectively — not the public code repo.
