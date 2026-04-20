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

- [functionality-inventory.md](functionality-inventory.md)
- [state-machines.md](state-machines.md)
- [data-flows.md](data-flows.md)
- [sr-coverage-matrix.md](sr-coverage-matrix.md)
- [tooling-evaluation.md](tooling-evaluation.md)

These are initial seed artifacts derived from the current codebase and the existing audit research.
