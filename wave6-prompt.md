You're on branch `gsd/v1.1-wave6-release` for the Worthless project.

Implement the remaining v1.1 tickets to ship 0.2.0.

## Tickets (from Linear)

| Ticket | Title | Notes |
|--------|-------|-------|
| WOR-165 | README quickstart rewrite for pip install | Rewrite for `pip install worthless` flow, not Docker |
| WOR-166 | PyPI publish pipeline + first publish | GitHub Actions workflow, trusted publishing |
| WOR-167 | Version bump to 0.2.0 | pyproject.toml, __init__.py if exists |
| WOR-174 | worthless service install — macOS launchd | `worthless service install` creates LaunchAgent plist |
| WOR-175 | worthless service install — Linux systemd | `worthless service install` creates systemd user unit |
| WOR-180 | End-to-end smoke test | Prove the product promise: enroll → wrap → request succeeds |

## Prior waves (all merged to main)
- Wave 1: version, tagline, spend cap, request counter
- Wave 2: worthless down + PID hardening
- Wave 3: rules engine (spend_cap, rate_limit, token_budget, time_window)
- Wave 4: SKILL.md + deploy verification + lock UX
- Wave 5: OS keyring for Fernet key (PR #45, pending merge)

## Implementation order suggestion
1. WOR-167 — version bump (trivial, do first)
2. WOR-165 — README rewrite (depends on knowing the install command)
3. WOR-174 + WOR-175 — service install (can parallel)
4. WOR-180 — E2E smoke test (validates everything works)
5. WOR-166 — PyPI publish pipeline (last — needs everything else done)

## Constraints
- No direct commits to main — feature branch + PR
- Commit after each ticket, push at end
- Use beads (bd) from main worktree: `cd /Users/shachar/Projects/worthless/worthless && bd <command>`
- Follow CLAUDE.md testing matrix
- Linear sync: update ticket status via API (key in ~/.zshrc)
