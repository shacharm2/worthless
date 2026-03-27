---
phase: 04-cli
plan: 04
subsystem: cli
tags: [process-lifecycle, uvicorn, pipe-death-detection, pid-files, signal-forwarding, daemon]

requires:
  - phase: 04-cli-02
    provides: "lock/enroll commands for key enrollment, shard storage"
provides:
  - "wrap command: ephemeral proxy + child process with env var injection"
  - "up command: standalone proxy daemon with PID file management"
  - "process lifecycle module: pipe death detection, signal forwarding, health polling"
affects: [04-cli, 05-docs]

tech-stack:
  added: []
  patterns: [pipe-based-death-detection, pid-file-management, provider-env-injection]

key-files:
  created:
    - src/worthless/cli/process.py
    - src/worthless/cli/commands/wrap.py
    - src/worthless/cli/commands/up.py
    - tests/test_process.py
    - tests/test_cli_wrap.py
    - tests/test_cli_up.py
  modified:
    - src/worthless/cli/app.py

key-decisions:
  - "Pipe-based death detection: proxy receives read_fd via WORTHLESS_LIVENESS_FD, parent holds write_fd — closing it signals proxy to self-terminate"
  - "Provider-to-env mapping: openai->OPENAI_BASE_URL, anthropic->ANTHROPIC_BASE_URL for transparent child routing"
  - "Session token via secrets.token_urlsafe(32) for proxy-child authentication"
  - "Daemon mode uses start_new_session=True (setsid equivalent) for process detachment"
  - "Port 0 parsing via threading reader on uvicorn stdout for OS-assigned random port"

patterns-established:
  - "Pipe death detection: os.pipe() with WORTHLESS_LIVENESS_FD env var for robust process cleanup"
  - "PID file format: pid\\nport\\n with stale detection via os.kill(pid, 0)"
  - "Provider env injection: _PROVIDER_ENV_MAP dict maps provider names to BASE_URL env vars"

requirements-completed: [CLI-02]

duration: 5min
completed: 2026-03-27
---

# Phase 04 Plan 04: Wrap & Up Commands Summary

**Process lifecycle with pipe death detection, wrap (ephemeral proxy + child env injection), and up (standalone daemon with PID files)**

## Performance

- **Duration:** 5 min
- **Started:** 2026-03-27T01:03:35Z
- **Completed:** 2026-03-27T01:08:35Z
- **Tasks:** 2
- **Files modified:** 7

## Accomplishments
- Process lifecycle module with pipe-based death detection, PID file management, and signal forwarding
- wrap command spawns ephemeral proxy on random port, injects provider BASE_URL env vars, mirrors child exit code
- up command starts standalone proxy in foreground or daemon mode with PID file and stale detection

## Task Commits

Each task was committed atomically:

1. **Task 1: Process lifecycle module** - `c4046a0` (feat)
2. **Task 2: wrap and up commands** - `d37f055` (feat)

## Files Created/Modified
- `src/worthless/cli/process.py` - Process lifecycle: spawn proxy, pipe death detection, PID files, signal forwarding
- `src/worthless/cli/commands/wrap.py` - wrap command: ephemeral proxy + child with env injection
- `src/worthless/cli/commands/up.py` - up command: standalone proxy foreground/daemon
- `src/worthless/cli/app.py` - Register wrap and up commands
- `tests/test_process.py` - 13 tests for process lifecycle (unit + integration)
- `tests/test_cli_wrap.py` - 10 tests for wrap command
- `tests/test_cli_up.py` - 6 tests for up command

## Decisions Made
- Pipe-based death detection via os.pipe() with WORTHLESS_LIVENESS_FD env var
- Provider env map: openai->OPENAI_BASE_URL, anthropic->ANTHROPIC_BASE_URL
- Session token via secrets.token_urlsafe(32) for proxy-child auth
- Daemon mode via start_new_session=True (setsid equivalent)
- Port 0 parsing via threaded stdout reader for uvicorn startup line

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- wrap and up commands ready for end-to-end integration testing
- Process lifecycle infrastructure available for status command (future plan)
- All 29 tests passing across 3 test files

---
*Phase: 04-cli*
*Completed: 2026-03-27*
