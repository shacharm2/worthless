# Concerns

## Highest-Value Gaps

### No runnable product path yet

The repo currently validates building blocks but does not contain a runnable system. There is no HTTP server, no CLI, no enrollment path, and no upstream caller. The main product promise is therefore still architectural intent rather than a usable end-to-end implementation.

Affected areas:
- `src/worthless/` has no proxy package
- `src/worthless/` has no CLI package

### Core invariant not yet enforceable

The defining invariant for Worthless is gate-before-reconstruct. That behavior cannot be validated yet because no application layer composes `ShardRepository`, `reconstruct_key()`, and `get_adapter()`.

### Python memory semantics remain the honest limitation

The current zeroing approach is reasonable for a Python PoC, but the repo does not yet contain the harder runtime isolation boundary described in research. If product claims get ahead of implementation, this becomes a credibility risk.

Relevant files:
- `src/worthless/crypto/splitter.py`
- `.planning/research/ARCHITECTURE.md`

## Secondary Concerns

### Repository/open-connection simplicity is a future bottleneck

`ShardRepository` opens a new SQLite connection per method call. That is fine for the current PoC scope but will become a scaling and latency concern once a real proxy starts handling concurrent traffic.

Relevant file:
- `src/worthless/storage/repository.py`

### Provider coverage is intentionally narrow

Only two provider paths are supported:
- `/v1/chat/completions`
- `/v1/messages`

That is appropriate for the current milestone, but anything claiming generic provider compatibility would be inaccurate.

Relevant file:
- `src/worthless/adapters/registry.py`

### Adapter relay is transport-transparent by design

The adapters do not parse provider semantics, usage metadata, or cost data. That keeps them clean, but it means future proxy metering logic must live elsewhere and must not be bolted into the adapters ad hoc.

Relevant file:
- `src/worthless/adapters/types.py`

## Documentation Drift

`.planning/ROADMAP.md` currently shows:
- Phase 2 complete
- Phase 1 as `0/2 Not started`

That conflicts with:
- `.planning/phases/01-crypto-core-and-storage/01-01-SUMMARY.md`
- `.planning/phases/01-crypto-core-and-storage/01-02-SUMMARY.md`
- `gsd-progress` output, which reports Phase 1 complete

This is a planning-state accuracy issue. Any status visualization should trust `gsd-progress` and phase artifacts over the static roadmap progress table.

## Missing Operational Concerns

No implementation exists yet for:
- structured logging policy
- metrics or spend tracking
- configuration management
- key enrollment UX
- client-side shard persistence
- authn/authz around protected keys

Those concerns are not bugs in current code; they are the still-unbuilt center of the system.
