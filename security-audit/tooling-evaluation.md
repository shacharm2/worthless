# Tooling Evaluation

## Current recommendation split

### Semgrep

Best for:

- fast local/CI guardrails
- custom project-specific rules
- suppression hygiene checks
- obvious secret/header/log misuse patterns

### CodeQL

Best for:

- high-value cross-function path/data-flow checks
- stronger proof for gate-before-reconstruct-style properties
- source-to-sink checks that are awkward in Semgrep

### AI-assisted review

Best for:

- state-machine and workflow mismatch hunting
- generating hypotheses about missing controls or stale assumptions
- helping maintainers understand the codebase faster

Not appropriate as the sole enforcement layer.

## Current recommendation

- keep Semgrep as the main custom guardrail layer
- add CodeQL only for a small number of high-value path/data-flow checks
- use AI review as a supplement for exploration and review, not as the source of truth

## Relation to engineering docs

This audit split mirrors the engineering-docs split:

- `engineering/` explains the system
- `security-audit/` evaluates the system
- public docs should only claim what those two internal layers support
