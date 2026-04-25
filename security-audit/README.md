# Security Audit Docs

`security-audit/` is the internal security-analysis tree for the current `worthless` codebase.

It exists to answer questions that do not belong in `engineering/` or `docs/`:

- what the security-relevant flows and states are
- what sensitive data crosses which boundaries
- which SR rules are strongly enforced, weakly enforced, or only documented
- where the implementation and the public claims drift apart

## Boundary

- `security-audit/` is internal analysis.
- `engineering/` documents how the current codebase works.
- `docs/` is public-facing and should not contain candid internal audit material.

## Current contents

Seed artifacts derived from the current codebase:

- [functionality-inventory.md](functionality-inventory.md)
- [state-machines.md](state-machines.md)
- [data-flows.md](data-flows.md)
- [sr-coverage-matrix.md](sr-coverage-matrix.md)
- [tooling-evaluation.md](tooling-evaluation.md)

## Adversarial review (2026-03-30)

Static adversarial review package for the current Worthless Python PoC:

- [attack-map.md](attack-map.md) — attacker types, motivations, and the highest-risk attack chains
- [operator-hardening.md](operator-hardening.md) — practical deployment guidance and non-negotiables
- [redteam-checklist.md](redteam-checklist.md) — manual validation scenarios before stronger claims ship
- [security-claims.md](security-claims.md) — wording guidance so product claims match the actual boundary

Recommended reading order:

1. `attack-map.md`
2. `operator-hardening.md`
3. `redteam-checklist.md`
4. `security-claims.md`
