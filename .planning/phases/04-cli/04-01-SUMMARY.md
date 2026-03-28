---
phase: 04-cli
plan: 01
subsystem: cli
tags: [typer, rich, fernet, dotenv, sarif, shannon-entropy]

requires:
  - phase: 03.1-proxy-hardening
    provides: ShardRepository, SplitResult, ProxySettings contracts
provides:
  - Typer CLI entry point with --quiet/--json flags
  - WorthlessConsole stderr/stdout routing singleton
  - ErrorCode enum (WRTLS-100..199) and WorthlessError exception
  - Bootstrap (~/.worthless/ directory, Fernet key, SQLite DB)
  - Atomic dotenv rewriter with Shannon entropy filtering
  - Scanner with KEY_PATTERN detection, decoy suppression, SARIF output
  - Key pattern detection for openai, anthropic, google, xai
affects: [04-02, 04-03, 04-04]

tech-stack:
  added: [typer, python-dotenv, rich]
  patterns: [TDD red-green, atomic file replacement via os.replace, O_CREAT|O_EXCL locking]

key-files:
  created:
    - src/worthless/cli/app.py
    - src/worthless/cli/console.py
    - src/worthless/cli/errors.py
    - src/worthless/cli/key_patterns.py
    - src/worthless/cli/bootstrap.py
    - src/worthless/cli/dotenv_rewriter.py
    - src/worthless/cli/scanner.py
    - tests/test_console.py
    - tests/test_bootstrap.py
    - tests/test_dotenv_rewriter.py
    - tests/test_scanner.py
  modified:
    - pyproject.toml
    - src/worthless/cli/__init__.py

key-decisions:
  - "Prefix detection sorted longest-first to prevent sk-ant- matching openai's sk- prefix"
  - "Bootstrap uses synchronous sqlite3 for DB init (avoids async complexity in CLI setup)"
  - "Dotenv rewriter uses tempfile + os.replace for atomic writes"

patterns-established:
  - "Longest-prefix-first matching for provider detection"
  - "Shannon entropy thresholding (4.5 bits) to filter placeholder keys"
  - "O_CREAT|O_EXCL for atomic lock file creation"

requirements-completed: [CLI-01, CLI-02, CLI-03, CLI-04]

duration: 5min
completed: 2026-03-26
---

# Phase 04 Plan 01: CLI Foundation Summary

**Typer CLI entry point with console routing, structured error codes, bootstrap, dotenv rewriter, and scanner with entropy-based key detection**

## Performance

- **Duration:** 5 min
- **Started:** 2026-03-26T21:37:02Z
- **Completed:** 2026-03-26T21:42:00Z
- **Tasks:** 2
- **Files modified:** 13

## Accomplishments
- Runnable `worthless --help` entry point with --quiet/-q and --json flags
- Console wrapper routing spinners to stderr, data to stdout, respecting NO_COLOR
- Bootstrap creating ~/.worthless/ with Fernet key, SQLite DB, shard_a dir (idempotent)
- Atomic dotenv rewriter with entropy filtering and scanner with SARIF output
- Key pattern detection for 4 providers (openai, anthropic, google, xai)
- 54 passing tests covering all modules

## Task Commits

Each task was committed atomically:

1. **Task 1: CLI framework, console wrapper, error codes, key patterns** - `a7601e0` (test) -> `cea811f` (feat)
2. **Task 2: Bootstrap, dotenv rewriter, scanner** - `4105a66` (test) -> `ffb66c9` (feat)

_TDD: each task has separate RED (test) and GREEN (feat) commits_

## Files Created/Modified
- `src/worthless/cli/app.py` - Typer app with --quiet/-q and --json flags
- `src/worthless/cli/console.py` - WorthlessConsole singleton for TTY/plain/json output
- `src/worthless/cli/errors.py` - ErrorCode IntEnum and WorthlessError exception
- `src/worthless/cli/key_patterns.py` - Provider prefix patterns and auto-detection
- `src/worthless/cli/bootstrap.py` - First-run ~/.worthless/ initialization
- `src/worthless/cli/dotenv_rewriter.py` - Atomic .env key replacement with entropy filtering
- `src/worthless/cli/scanner.py` - File scanning with decoy suppression and SARIF output
- `tests/test_console.py` - 25 tests for errors, patterns, console, app entry
- `tests/test_bootstrap.py` - 10 tests for bootstrap and locking
- `tests/test_dotenv_rewriter.py` - 11 tests for rewriter and entropy
- `tests/test_scanner.py` - 8 tests for scanner and SARIF
- `pyproject.toml` - Added typer, python-dotenv deps and [project.scripts] entry

## Decisions Made
- Prefix detection sorted longest-first to prevent `sk-ant-` matching openai's `sk-` prefix
- Bootstrap uses synchronous sqlite3 for DB init (avoids async complexity in CLI setup path)
- Dotenv rewriter uses tempfile + os.replace for atomic writes (no partial file states)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed prefix detection order for provider matching**
- **Found during:** Task 1 (key patterns implementation)
- **Issue:** `sk-ant-api03-` matched openai's `sk-` prefix before anthropic's longer prefix
- **Fix:** Built flat sorted lookup list (longest-first) instead of iterating dict order
- **Files modified:** `src/worthless/cli/key_patterns.py`
- **Verification:** All 4 provider detection tests pass
- **Committed in:** `cea811f` (Task 1 feat commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Essential for correctness. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- CLI foundation complete: all 6 planned commands can import console, errors, bootstrap, key_patterns
- Lock command (04-02) can use dotenv_rewriter + key_patterns
- Scan command (04-03) can use scanner + key_patterns
- All modules importable with no circular dependencies

---
*Phase: 04-cli*
*Completed: 2026-03-26*
