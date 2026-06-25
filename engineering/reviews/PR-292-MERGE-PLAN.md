# PR #292 — merge plan (living doc)

**PR:** https://github.com/shacharm2/worthless/pull/292
**Branch:** `gsd/wor-193-wave3b-adversarial` → `main`
**Worktree:** `/Users/shachar/Projects/worthless/worthless-wor193-service`
**Updated:** 2026-06-08 (head `a71802f`)

This is the single checklist. Do not re-derive from chat.

---

## Gate matrix

| Gate | Status | Artifact / action |
|------|--------|-------------------|
| Pass-1 MUST-FIX (keystore S_ISREG, fernet chmod test, launchd plist test, PR Why, security doc trim) | **Done** | Replies on PR; code on branch |
| Pass-2 panel (default_command exit 2, single detect, keystore validate=True, stop(home) test) | **Done** | `engineering/reviews/thermo-nuclear/PR-292-pass2-verdict.md` → **GO** |
| Thermo-nuclear **security** | **PASS** | `engineering/reviews/thermo-nuclear/wor193-stack-security.md` |
| Thermo-nuclear **code quality** | **Approve** | `engineering/reviews/thermo-nuclear/wor193-stack-code-quality.md` |
| Claude / handoff review | **Done in-session** | `engineering/reviews/thermo-nuclear/PR-292-claude-handoff.md` (update base ref: `main`, not 717-integration) |
| CodeRabbit | **14/14 threads resolved** | Last open: fernet `chmod(0o644)` — fixed + resolved via API |
| CI | **Re-running on `a71802f`** | `gh pr checks 292` — wait for Test + User flows + docker-e2e + Windows smoke |

---

## What you should NOT have to ask again

1. **CodeRabbit** — all threads addressed; resolve in UI only if GitHub still shows stale count.
2. **Thermo-nuclear** — already run; artifacts in `engineering/reviews/thermo-nuclear/`.
3. **Claude review** — pass-2 verdict + security doc are the recorded outcomes; handoff doc is the prompt archive.
4. **Next action** — only CI green → merge (or triage remaining red jobs below).

---

## CI triage (if still red after `a71802f`)

| Job | Likely cause | Fix |
|-----|--------------|-----|
| Test ubuntu py3.10/3.13 | Stale mock (`_proxy_is_running` vs `detect_proxy_runtime`) | **Fixed in `a71802f`** |
| User flows macOS/ubuntu | default_command exit 2 / double detect | **Fixed in `cb53166`** |
| docker-e2e | May be infra/flake or unrelated stack | Inspect log; not always wave3b |
| Smoke windows py3.13 | Platform-specific | Inspect log |

**Local verify (wave3b scope):**

```bash
cd /Users/shachar/Projects/worthless/worthless-wor193-service
uv run pytest tests/cli/test_service_backends.py tests/cli/test_start_supervised_proxy_integration.py tests/test_keystore.py tests/cli/test_service_up_managed.py tests/cli/test_service_common.py tests/test_cli_default.py -o addopts= -q
gh pr checks 292
```

---

## Explicit non-blockers (do not hold merge)

- P2 orphan latch in `up.py` / `process.py` (W3-ADV-3/9) — bead follow-up
- P3 `_managed_sidecar_healthy` HELLO degradation
- WOR-435 full machine purge / `worthless uninstall`
- macOS live-pack manual L7 checklist

---

## Merge sequence

1. `gh pr checks 292` — all required green
2. Squash merge #292 to `main`
3. Optional: run macOS live pack from `engineering/testing/scripts/` on a dev machine
4. Close WOR-193 wave3b Linear/beads when verified on main

---

## Process note (why this doc exists)

Reviews were run in chat but not surfaced as a standing plan. **This file is the plan.** Update the **Updated** line and gate table after each push; do not rely on conversation memory.
