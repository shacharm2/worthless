# SR Coverage Matrix

## Summary view

| SR | Current status | Main evidence | Main gap pattern |
|---|---|---|---|
| SR-01 | Moderate | mutable key-material APIs, targeted Semgrep, tests | boundary conversions and suppressions |
| SR-02 | Best-effort | zeroing helpers and close/cleanup paths | Python/runtime copy limits |
| SR-03 | Strong by design, moderate by proof | rules-before-reconstruct structure, tests | docs/runtime drift and heuristic proof |
| SR-04 | Mixed | sanitized errors and repr redaction tests | broad runtime logging/debug claim mismatch |
| SR-05 | Best-effort | gitleaks and denylist-style controls | runtime proof weaker than posture language |
| SR-06 | Planned | architecture intent only | not implemented in current Python PoC |
| SR-07 | Moderate | implementation path is correct | Semgrep/tests are heuristic rather than path-sensitive |
| SR-08 | Strong | Ruff ban, Semgrep, tests | low current concern |

## Key current findings

### SR-03

- The runtime gate currently uses spend cap, token budget, and rate limit.
- `TimeWindowRule` exists in code but is not currently wired into the app's active rules engine.
- Historical docs/tickets around model allowlists do not fully match the current runtime stance.

### SR-04

- Non-debug error handling is intentionally sanitized.
- `--debug` still prints full tracebacks by design.
- Public or posture language about broad redaction/telemetry safety should be treated carefully.

### SR-05

- Denylist and scan-style protections are stronger in static tooling than in any broad runtime guarantee.

### SR-07

- The core implementation path is correct, but current enforcement proof is weaker than ideal because it relies on heuristics and tests rather than stronger path/data-flow tooling.

## Suppression review

The repo uses targeted suppressions in security-sensitive areas, especially around:

- unavoidable library boundary conversions
- SQLite and Fernet byte conversions
- key-material handling that cannot remain purely mutable across third-party APIs

These suppressions are not automatically wrong, but they are part of the enforcement surface and should be reviewed as such.
