---
phase: 04.2
reviewed_by: [python-pro, qa-expert, devops-engineer, security-engineer, code-reviewer]
blockers: 4
warnings: 8
nits: 7
verdict: needs-fixes
---

# Phase 04.2 Review: Test Hardening

## Blockers (4)

### B1. GHA action versions use mutable tags, not SHA pins
**Reviewer:** devops-engineer
**Files:** All `.github/workflows/*.yml`
**Issue:** `actions/checkout@v6`, `actions/setup-python@v6` are mutable tags -- a compromised tag = arbitrary code in CI.
**Fix:** Pin to full commit SHAs.

### B2. `pre-release.yml` uses bare `python` instead of `uv run python`
**Reviewer:** devops-engineer
**File:** `.github/workflows/pre-release.yml` (line ~92)
**Issue:** Coverage floors step runs `python scripts/check-coverage-floors.py` outside the uv venv. Will fail or use wrong interpreter.
**Fix:** Change to `uv run python scripts/check-coverage-floors.py`.

### B3. Bandit `-ll` hides low-severity crypto findings
**Reviewer:** security-engineer
**File:** `.github/workflows/sast.yml` (line ~41)
**Issue:** `-ll` suppresses LOW-severity findings like B105 (hardcoded passwords) and B324 (insecure hashes) -- relevant for a crypto project.
**Fix:** Use `-l -ii` (low severity, medium+ confidence) or configure via pyproject.toml.

### B4. `check-coverage-floors.py` crashes on missing `coverage.xml`
**Reviewer:** code-reviewer
**File:** `scripts/check-coverage-floors.py` (line 14)
**Issue:** `ET.parse("coverage.xml")` has no error handling. Missing file = cryptic traceback in CI.
**Fix:** Add try/except with clear error message and exit(1).

## Warnings (8)

### W1. `pytest-rerunfailures` missing from `dependency-groups.test`
**Reviewer:** python-pro
**File:** `pyproject.toml`
**Issue:** Added to `[project.optional-dependencies] test` but NOT to `[dependency-groups] test`. `uv sync --group test` won't install it.
**Fix:** Add to dependency-groups test list.

### W2. `concurrency = ["multiprocessing"]` likely incorrect for xdist
**Reviewer:** python-pro
**File:** `pyproject.toml` `[tool.coverage.run]`
**Issue:** xdist uses fork/subprocess, not Python multiprocessing. Setting is only needed if app code uses multiprocessing. Worthless is async (asyncio/uvicorn).
**Fix:** Remove or change to `concurrency = ["thread"]`.

### W3. SR-01 violation in test fixture: `bytes()` for shard_b
**Reviewer:** qa-expert
**File:** `tests/test_proxy.py:347-349`
**Issue:** `StoredShard` constructed with `bytes(sr.shard_b)` instead of `bytearray(sr.shard_b)`, violating SR-01.
**Fix:** Use `bytearray(sr.shard_b)`.

### W4. Fragile zero-after-use ordering in `test_properties.py`
**Reviewer:** qa-expert
**File:** `tests/test_properties.py:298`
**Issue:** `reconstruct_key` receives `sr.shard_a` (bytearray), then `sr.zero()` runs in finally. If reordered, test silently passes with zeroed input.
**Fix:** Copy the shard before use: `shard_a_copy = bytearray(sr.shard_a)`.

### W5. `tests.yml` grants `pull-requests: write` at workflow level
**Reviewer:** devops-engineer
**File:** `.github/workflows/tests.yml`
**Issue:** Only coverage-comment job needs write. Granting at workflow level violates least privilege.
**Fix:** Move permission to job level on coverage-comment only.

### W6. Semgrep `--config auto` lacks crypto-specific rulesets
**Reviewer:** security-engineer
**File:** `.github/workflows/sast.yml`
**Issue:** Missing `p/python-crypto`, `p/secrets`, `p/security-audit` rulesets for a crypto/key-management project.
**Fix:** Add `--config p/python-crypto --config p/secrets --config p/security-audit`.

### W7. No custom Semgrep rules for SR enforcement
**Reviewer:** security-engineer
**Issue:** SR-01 (bytearray not bytes), SR-07 (constant-time compare), SR-08 (CSPRNG only) have no SAST enforcement.
**Fix:** Create `.semgrep/worthless-rules.yml` with custom rules.

### W8. Substring matching in coverage floors causes false positives
**Reviewer:** code-reviewer
**File:** `scripts/check-coverage-floors.py` (line 31)
**Issue:** `if module in name` matches `worthless.crypto_utils` against `worthless.crypto` floor.
**Fix:** Use `name == module or name.startswith(module + ".")`.

## Nits (7)

### N1. No `--reruns` default in addopts
**Reviewer:** python-pro
**Issue:** `pytest-rerunfailures` installed but no `--reruns N` in addopts. Plugin does nothing without it.

### N2. Unused `tmp_path` in `test_cli_wrap.py:33,37`
**Reviewer:** qa-expert

### N3. Dead code `_real_popen` in `test_cli_wrap.py:501`
**Reviewer:** qa-expert

### N4. `scheduled.yml` contract tests swallow failures with `|| true`
**Reviewer:** devops-engineer
**Fix:** Use `continue-on-error: true` at job level instead.

### N5. Scheduled Hypothesis uses `ci` profile, not extended
**Reviewer:** devops-engineer
**Issue:** Job named "Extended Hypothesis" uses same ci profile as Tier 1.

### N6. No SARIF upload for PR annotations
**Reviewer:** security-engineer
**Issue:** Bandit/Semgrep findings only in logs, not inline PR comments.

### N7. Unchecked coverage floors silently pass
**Reviewer:** code-reviewer
**Issue:** If a module is absent from coverage.xml, its floor is never checked. Should fail on unmatched floors.

## Verdict

**needs-fixes** -- 4 blockers must be resolved before merging. The CI pipeline has supply chain risks (mutable action tags), a security blind spot (suppressed bandit findings), and a script that will crash in CI on first run without coverage.xml.

Priority fix order:
1. B1 (SHA-pin actions) + B3 (bandit severity) -- security
2. B4 (coverage script error handling) + B2 (bare python) -- CI reliability
3. W3 (SR-01 violation) + W8 (substring matching) -- correctness
4. Remaining warnings as follow-up
