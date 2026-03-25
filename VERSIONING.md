# Versioning

## Rule

Phase number = minor version. Sub-phases = patch version.

```
Phase N     → v0.N.0
Phase N.X   → v0.N.X
```

Git tags are created on the commit that completes each phase (UAT passed or merge commit).

## Current Version

**v0.3.1** — Phase 3.1: Proxy Hardening (UAT passed 2026-03-25)

## Version History

| Version | Phase | Description | Status |
|---------|-------|-------------|--------|
| `v0.1.0` | Phase 1 | Crypto Core & Storage | Done |
| `v0.2.0` | Phase 2 | Provider Adapters | Done |
| `v0.3.0` | Phase 3 | Proxy Service | Done |
| `v0.3.1` | Phase 3.1 | Proxy Hardening | Done |
| `v0.4.0` | Phase 4 | CLI | Next |
| `v0.5.0` | Phase 5 | Security Posture Docs | Planned |
| `v0.9.0` | — | Cleanup, housekeeping, polish | Planned |
| `v1.0.0` | — | PoC milestone complete | Planned |

## Milestones

| Milestone | Version Range | Goal |
|-----------|---------------|------|
| PoC | `v0.1.0` → `v1.0.0` | Python + SQLite, prove the architecture |
| Harden | `v1.1.0+` | Rust reconstruction service, production hardening |
| Attack | `v1.x.0+` | Pen-testing, red team, security audit |

## For Agents

- **Check `Current Version` above** to know what's built and tested.
- **Worktree branches** inherit the version they branched from. Testing or CI work on top of `v0.3.1` is still `v0.3.1` unless it adds new phase-level functionality.
- **When completing a phase**, update `Current Version` in this file and create a git tag.
- **Don't invent versions.** If your work doesn't complete a phase, don't tag it.
