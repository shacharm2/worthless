# Security Rule Gap Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a repo-specific security audit package that maps functionality, state machines, and data flows to `SECURITY_RULES.md`, then identifies missing rules, missing enforcement, and high-value tooling upgrades.

**Architecture:** Treat this as a graph-and-controls audit, not a generic code review. First inventory the repo's real execution flows and trust boundaries, then derive state machines and data flows, then crosswalk those artifacts against the SRs, tests, Semgrep rules, Ruff/Bandit checks, CI, and documented non-goals. Finish with a gap report that separates "missing rule," "missing enforcement," "missing evidence," and "out-of-scope/non-goal."

**Tech Stack:** Python, FastAPI, Typer, Semgrep, Ruff, Bandit, pytest, Mermaid, Markdown, GitHub Actions

---

## File Structure

**Existing files to read heavily**
- `README.md`
- `SECURITY_RULES.md`
- `SECURITY_POSTURE.md`
- `docs/ARCHITECTURE.md`
- `docs/research/threat-model.md`
- `.semgrep/worthless-rules.yml`
- `.github/workflows/sast.yml`
- `.pre-commit-config.yaml`
- `src/worthless/cli/commands/lock.py`
- `src/worthless/cli/default_command.py`
- `src/worthless/cli/bootstrap.py`
- `src/worthless/crypto/splitter.py`
- `src/worthless/crypto/types.py`
- `src/worthless/storage/repository.py`
- `src/worthless/proxy/app.py`
- `src/worthless/proxy/rules.py`
- `src/worthless/proxy/metering.py`
- `src/worthless/adapters/openai.py`
- `src/worthless/adapters/anthropic.py`
- `src/worthless/mcp/server.py`
- `tests/test_invariants.py`
- `tests/test_security_properties.py`
- `tests/test_proxy_hardening.py`

**Artifacts to create**
- `docs/security-audit/functionality-inventory.md`
- `docs/security-audit/state-machines.md`
- `docs/security-audit/data-flows.md`
- `docs/security-audit/sr-coverage-matrix.md`
- `docs/security-audit/missing-rules.md`
- `docs/security-audit/tooling-options.md`

## Task 1: Build The Functionality Inventory

**Files:**
- Create: `docs/security-audit/functionality-inventory.md`
- Read: `README.md`
- Read: `docs/ARCHITECTURE.md`
- Read: `SECURITY_POSTURE.md`
- Read: `src/worthless/cli/**`
- Read: `src/worthless/proxy/**`
- Read: `src/worthless/storage/**`
- Read: `src/worthless/crypto/**`
- Read: `src/worthless/adapters/**`
- Read: `src/worthless/mcp/server.py`

- [ ] Enumerate user-visible flows: `worthless`, `lock`, `unlock`, `scan`, `status`, `up`, `down`, `wrap`, `revoke`, MCP usage.
- [ ] For each flow, record entrypoint, primary modules, external dependencies, persistence touches, trust boundaries, and security-sensitive data handled.
- [ ] Split flows into four buckets: enrollment, local secret handling, proxy request handling, lifecycle/operations.
- [ ] Mark which flows are architecture-critical versus convenience/operational.
- [ ] Save one concise table per flow in `docs/security-audit/functionality-inventory.md`.

## Task 2: Derive State Machines

**Files:**
- Create: `docs/security-audit/state-machines.md`
- Read: `src/worthless/cli/default_command.py`
- Read: `src/worthless/cli/commands/lock.py`
- Read: `src/worthless/cli/commands/up.py`
- Read: `src/worthless/cli/commands/down.py`
- Read: `src/worthless/proxy/app.py`
- Read: `src/worthless/proxy/rules.py`
- Read: `src/worthless/storage/repository.py`

- [ ] Create a Mermaid state machine for enrollment: discovered key -> split -> Shard A persisted -> Shard B persisted -> decoy written -> success/failure cleanup.
- [ ] Create a Mermaid state machine for request handling: request received -> rules evaluated -> denied or decrypt -> reconstruct -> upstream dispatch -> cleanup -> response.
- [ ] Create a Mermaid state machine for daemon/proxy lifecycle: stopped -> starting -> healthy -> degraded -> stopping -> stopped.
- [ ] Create a Mermaid state machine for revocation/unlock flows if they materially alter secret state.
- [ ] For every transition, list the exact code location that currently implements it.
- [ ] Add a short "security significance" note under each diagram so the state machine doubles as audit evidence.

## Task 3: Derive Data Flow And Trust-Boundary Diagrams

**Files:**
- Create: `docs/security-audit/data-flows.md`
- Read: `src/worthless/crypto/splitter.py`
- Read: `src/worthless/crypto/types.py`
- Read: `src/worthless/storage/repository.py`
- Read: `src/worthless/proxy/app.py`
- Read: `src/worthless/adapters/openai.py`
- Read: `src/worthless/adapters/anthropic.py`

- [ ] Create a source-to-sink inventory for the following sensitive values: full API key, Shard A, Shard B ciphertext, Shard B plaintext, commitment, nonce, decoy key, provider auth header.
- [ ] For each sensitive value, capture: origin, transformations, storage locations, transmission paths, logging exposure points, cleanup points, and invariants.
- [ ] Draw a trust-boundary diagram separating client machine, local key storage, proxy boundary, reconstruction boundary, upstream provider, and CI/developer tooling.
- [ ] Distinguish "intended flow" from "observed code path" if the Python PoC differs from the target Rust-sidecar architecture.
- [ ] Call out every point where immutable copies, serialization, logging, exceptions, subprocesses, or headers could widen exposure.

## Task 4: Build The SR Coverage Matrix

**Files:**
- Create: `docs/security-audit/sr-coverage-matrix.md`
- Read: `SECURITY_RULES.md`
- Read: `SECURITY_POSTURE.md`
- Read: `.semgrep/worthless-rules.yml`
- Read: `.pre-commit-config.yaml`
- Read: `.github/workflows/sast.yml`
- Read: `tests/test_invariants.py`
- Read: `tests/test_security_properties.py`

- [ ] Build one row per SR with these columns: rule text, affected flows, code locations, threat(s) mitigated, static enforcement, test enforcement, runtime enforcement, docs evidence, known bypasses, residual risk.
- [ ] Separate "rule exists but weakly enforced" from "rule absent entirely."
- [ ] Mark enforcement type precisely: Semgrep, Ruff, Bandit, pygrep, unit test, property test, CI workflow, human review only, or planned only.
- [ ] Add a suppressions section listing every `nosemgrep` and explain whether the exception is justified, temporary, or unreviewed.
- [ ] Add a "coverage confidence" rating per SR: strong, medium, weak, planned.

## Task 5: Identify Missing Rules, Missing Enforcement, And Missing Evidence

**Files:**
- Create: `docs/security-audit/missing-rules.md`
- Read: `docs/security-audit/functionality-inventory.md`
- Read: `docs/security-audit/state-machines.md`
- Read: `docs/security-audit/data-flows.md`
- Read: `docs/security-audit/sr-coverage-matrix.md`
- Read: `docs/research/threat-model.md`

- [ ] Classify every gap into one of four buckets:
- [ ] Missing security rule: repo functionality exposes a security requirement not expressed in `SECURITY_RULES.md`.
- [ ] Missing enforcement: rule exists but current automation is absent or too weak.
- [ ] Missing evidence: implementation may be correct, but there is no durable proof in tests/docs/tooling.
- [ ] Intentional non-goal: important risk, but explicitly outside product guarantees.
- [ ] Prioritize gaps by exploitability and blast radius, not by implementation convenience.
- [ ] For each gap, propose the cheapest credible enforcement mechanism first, then stronger options.

**Likely gaps to test explicitly**
- [ ] Logging/telemetry sinks beyond upstream error sanitization.
- [ ] Secret egress into headers, exceptions, metrics, traces, and subprocess environments.
- [ ] Gate-before-decrypt as control-flow/data-flow property, not just source ordering.
- [ ] Suppression hygiene for `nosemgrep`.
- [ ] Swap/page-lock/process-isolation guarantees that are architectural today but not enforceable in Python.
- [ ] Bootstrap/install/supply-chain rules that protect the product before runtime.
- [ ] CLI and daemon lifecycle transitions that may leave partially-protected or partially-restored secret state.

## Task 6: Benchmark External Enforcement Patterns

**Files:**
- Create: `docs/security-audit/tooling-options.md`
- Read: `.semgrep/worthless-rules.yml`
- Read: `.github/workflows/sast.yml`
- Read: `.pre-commit-config.yaml`

- [ ] Compare current repo practice against Semgrep's recommended model for custom rules: create, verify/test, then deploy to IDE/PR/pre-commit.
- [ ] Document how custom-rule ecosystems handle repo-specific invariants:
- [ ] Semgrep custom rules and guardrails for secure coding conventions.
- [ ] CodeQL custom queries and query packs for architecture-specific flows and path queries.
- [ ] Joern/code-property-graph workflows for graph-level control-flow and data-flow audits.
- [ ] AI-assisted review options for logic flaws and repository-wide context.
- [ ] For each tool, record fit, setup cost, deterministic/non-deterministic behavior, strengths, and limitations for this repo.

**Current recommendation to validate**
- [ ] Keep Semgrep for high-signal repo-local guardrails.
- [ ] Add tested Semgrep rules for secret-to-log and secret-to-header/dataflow checks where feasible.
- [ ] Use CodeQL path queries for cross-function source-to-sink and gate-order properties that outgrow Semgrep CE.
- [ ] Use AI review as a supplement for logic/state-machine gaps, never as the sole enforcement mechanism.

## Task 7: Turn The Audit Into A Backlog

**Files:**
- Read: `docs/security-audit/missing-rules.md`
- Read: `docs/security-audit/tooling-options.md`

- [ ] Convert every high-priority gap into a concrete issue with owner, severity, expected artifact, and verification method.
- [ ] Separate documentation work from enforcement work from architecture work.
- [ ] Make sure each issue answers: what risk is reduced, what signal proves success, and what tool owns enforcement.
- [ ] Close the audit only after the backlog exists and every gap has one of: fix issue, accepted non-goal, or rejected false positive.

## Deliverable Standard

The audit is complete only when:

- `docs/security-audit/functionality-inventory.md` explains how the repo actually works.
- `docs/security-audit/state-machines.md` captures the security-critical states and transitions.
- `docs/security-audit/data-flows.md` traces sensitive data through the system.
- `docs/security-audit/sr-coverage-matrix.md` maps each SR to evidence and enforcement.
- `docs/security-audit/missing-rules.md` separates missing rule vs missing enforcement vs missing evidence.
- `docs/security-audit/tooling-options.md` recommends which tools should own which checks.
- Follow-up issues exist for every material gap.

## Notes For The Auditor

- Favor repo-specific invariants over generic CWE shopping.
- Do not confuse "documented in SECURITY_POSTURE" with "enforced in code."
- Do not confuse "tested once" with "continuously enforced."
- Treat planned Rust-sidecar controls separately from guarantees provided by the current Python PoC.
- Treat AI findings as hypothesis generators that still require deterministic validation.
