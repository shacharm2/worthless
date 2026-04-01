# Launch Reality Check

**Date**: 2026-03-31
**Verdict**: NOT READY TO LAUNCH. Multiple Critical and High issues would embarrass you on HN.

---

## 1. The "Clone and Try" Experience Is Broken

### CRITICAL: `worthless wrap` crashes on fresh install

```
WRTLS-103: Failed to initialise database: no such column: decoy_hash
```

The `wrap` command -- the flagship "just use it" experience -- crashes with a SQLite schema error. This is a migration bug where the `decoy_hash` column doesn't exist in freshly-created databases or databases created before that migration. A HN commenter will hit this in under 60 seconds.

### CRITICAL: README quickstart uses `enroll_stub` -- a test helper, not a CLI command

The README "Enroll an API key" section asks users to write a 12-line Python script importing `enroll_stub`. This is a development scaffold, not a user experience. The actual CLI has `worthless lock` and `worthless enroll` commands that do this properly, but the README doesn't mention them. The README and the actual product are describing two different tools.

### HIGH: README describes a manual `uvicorn` startup, but CLI has `worthless up`

The README tells users to run:
```
WORTHLESS_FERNET_KEY="..." WORTHLESS_ALLOW_INSECURE=true uv run uvicorn worthless.proxy.app:create_app --factory --port 8443
```

But the CLI has `worthless up` which handles this. The README is stuck in pre-CLI development mode.

### HIGH: README quickstart is the wrong flow entirely

The actual user flow should be:
```
worthless lock          # protect keys in .env
worthless up            # start proxy
worthless wrap -- cmd   # or just wrap a command
```

None of this appears in the README. The README describes a developer's testing workflow, not a user's workflow.

---

## 2. Test Suite: 8 Failures

689 passed, 8 failed. All failures are `ModuleNotFoundError: No module named 'scipy'` in `test_decoy.py`. These are statistical validation tests that import scipy at runtime but scipy isn't in the test dependencies. Not catastrophic but sloppy -- a contributor running `uv run pytest` sees red immediately.

---

## 3. Lint: 93 Ruff Errors

`uv run ruff check .` reports 93 errors. Mostly unused imports and import ordering in `lock.py`, but also line-length violations. A contributor opening a PR will see the linter screaming. 50 are auto-fixable.

---

## 4. README vs Reality Gap Analysis

| README Claims | Reality |
|---|---|
| Install links to `docs/install-solo.md`, etc. | Docs exist but need content verification |
| "Enrollment stub (more commands coming)" in Architecture | CLI has 7 working commands (lock, enroll, unlock, scan, status, wrap, up) -- README undersells |
| Quickstart uses Python script with enroll_stub | Real CLI has `worthless lock` which is far simpler |
| Manual uvicorn startup | `worthless up` exists with daemon mode |
| No mention of `lock`, `unlock`, `wrap`, `scan` | These are the actual product |
| Links to `docs/security-model.md` | File exists |
| SKILL.md mentioned in CLAUDE.md as deliverable | Does not exist |

The README is approximately 2 major versions behind the actual codebase.

---

## 5. What HN Commenters Will Tear Apart

1. **"I tried `worthless wrap` and it crashed"** -- the decoy_hash migration bug is a showstopper
2. **"The README tells me to write Python to enroll a key, but there's a CLI command for that?"** -- confused messaging
3. **"93 lint errors? Do they run CI?"** -- credibility hit for a security tool
4. **"This is just a reverse proxy with XOR. Why not just use a vault?"** -- you need the README to preemptively address this with the spend-cap angle
5. **"Where's the spend cap / rules engine configuration?"** -- the README mentions rate limiting in env vars but doesn't explain the core value prop (budget enforcement)
6. **"Python for a security tool?"** -- the README should acknowledge this and point to the Rust reconstruction service roadmap
7. **"No PyPI package?"** -- install is git clone only right now

---

## 6. Prioritized Action Plan

### P0 -- Must fix before any public mention

| # | Item | Severity | Done Criteria |
|---|---|---|---|
| 1 | Fix `decoy_hash` migration bug | Critical | `worthless wrap -- echo hello` works on fresh install |
| 2 | Rewrite README quickstart to use actual CLI | Critical | Quickstart is: `pip install worthless` or `uv sync`, then `worthless lock`, `worthless up`, `worthless wrap` |
| 3 | Fix 8 scipy test failures | High | `uv run pytest` shows 0 failures (either add scipy to test deps or skip those tests gracefully) |
| 4 | Fix 93 ruff errors | High | `uv run ruff check .` exits clean |

### P1 -- Should fix before HN launch

| # | Item | Severity | Done Criteria |
|---|---|---|---|
| 5 | README: lead with the spend-cap value prop, not just XOR | High | First paragraph answers "why not just use a vault?" |
| 6 | README: add comparison table (vs vault, vs env encryption, vs nothing) | Medium | Table exists showing Worthless advantages |
| 7 | README: add 30-second GIF or asciicast of lock/wrap flow | Medium | Visual demo embedded |
| 8 | README: preemptively address "Python for security?" | Medium | Short section or FAQ |
| 9 | Create SKILL.md for agent discovery | Medium | File exists at repo root |
| 10 | Verify all docs/ links have real content | Medium | Each install-*.md has working instructions |

### P2 -- Nice to have for launch

| # | Item | Severity | Done Criteria |
|---|---|---|---|
| 11 | PyPI package (`pip install worthless`) | Low | `pip install worthless` works |
| 12 | Add badges (CI, coverage, PyPI version) | Low | Badges render in README |
| 13 | Architecture diagram (mermaid or image) | Low | Visual in README |

---

## 7. The Actual README Should Look Like This

The current README is 753 words and structured wrong. The new README should:

1. **Open with the problem** -- "Your API key is one leaked `.env` away from a $10,000 bill"
2. **Show the solution in 4 lines** -- `worthless lock` / `worthless wrap`
3. **Explain why this is different** -- spend cap kills the request before the key forms, comparison table
4. **Quickstart** -- actual CLI commands, not Python scripts
5. **How it works** -- the existing diagram is good, keep it
6. **Security model** -- keep but tighten
7. **FAQ** -- address the obvious objections (Python? XOR? Why not vault?)

---

## Summary

The product is further along than the README suggests -- you have 7 CLI commands, a working proxy, 689 passing tests, and solid crypto foundations. But the README is stuck in early development mode and the `wrap` command has a showstopper bug. Fix the migration bug, rewrite the quickstart, clean the lint, and you have something worth launching. Right now you'd get "interesting idea, but the quickstart doesn't work" comments.
