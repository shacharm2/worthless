# Security Claims Discipline

Date: 2026-03-30
Purpose: keep product, README, and operator language aligned with the current code.

## Current Problem

The repo contains strong security language that outruns the implementation in a
few places. That is dangerous for two reasons:

1. Operators deploy with the wrong assumptions.
2. Attackers read the same copy and optimize around the gap.

## Claims To Avoid Today

Do not say:

- "We make your API keys worthless to steal."
- "Your key now has a hard cap."
- "One half stays on your machine."
- "Unauthorized usage is blocked unless the attacker has the key."

Why:

- Same-user local code can reconstruct everything.
- Remote deployments can collapse the two-shard boundary if Shard A is server-readable.
- Metering is not yet robust enough for a strict spend claim.
- The current system mostly enforces token-based guardrails, not a true provider-billed dollar ceiling.

## Claims That Are Defensible Today

Safer replacements:

- "Worthless reduces the value of leaked API keys by splitting credential material and routing usage through a guardrail proxy."
- "The current Python PoC is strongest for local developer use and tightly controlled internal deployments."
- "Denied requests are evaluated before server-side key reconstruction."
- "The current proxy provides best-effort token-budget enforcement with known accounting limitations."
- "The project does not protect against host compromise or same-user untrusted code execution."

## Claims That Should Be Conditional

Only say these after the matching hardening exists:

- "Hard spend cap"
  Condition: provider-correct cost accounting, reservations, and bounded reconciliation

- "Worthless to steal"
  Condition: no server-readable Shard A in remote mode, explicit client auth, isolated reconstruction, and clear local-boundary caveats

- "One half stays on your machine"
  Condition: deployment mode actually requires client-held Shard A and does not allow server fallback

- "Production-ready for team deployments"
  Condition: trusted proxy handling, client auth, hardening docs, and safe distribution paths

## Suggested README Positioning

Suggested top-line replacement:

"Worthless makes leaked API keys materially less useful by splitting key material and enforcing usage guardrails before reconstruction."

Suggested scope statement:

"Today’s Python PoC is best suited for local developer protection and controlled internal deployments. Remote multi-user deployments need additional hardening before stronger claims are justified."

Suggested limitation statement:

"Current budget enforcement is closer to a best-effort usage guardrail than a strict provider-billed spending ceiling."

## Messaging Rules

When talking to users:

- Separate local mode from self-hosted multi-user mode.
- Separate token accounting from actual provider billing.
- Separate leaked-key protection from host-compromise protection.
- Say "reduces value" before saying "prevents abuse."

## Review Trigger

Re-run claim review whenever any of these change:

- auth model
- alias inference
- shard loading rules
- provider accounting
- deployment guidance
- bootstrap and package distribution
