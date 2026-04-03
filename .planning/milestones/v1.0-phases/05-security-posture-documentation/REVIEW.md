---
phase: 05
reviewed_by: [code-reviewer, everything-claude-code:security-reviewer, qa-expert]
blockers: 0
warnings: 6
nits: 5
verdict: needs-fixes
---

# Phase 5 Review: Security Posture Documentation

## Blockers (0)

None.

## Warnings (6)

### W1: SR-07 test — attribute access bypasses detection
**File:** `tests/test_security_properties.py` (TestSR07ConstantTimeCompare)
**Reviewer:** code-reviewer

The `suspect_names` check only catches `ast.Name` operands. `self.commitment == other` or `obj.digest == value` are `ast.Attribute` nodes and slip through.

**Fix:** Add `ast.Attribute` branch checking `node.attr in suspect_names`.

### W2: SR-07 test — variable renaming bypass
**File:** `tests/test_security_properties.py`
**Reviewer:** security-reviewer

`computed_hmac == stored_hmac` would pass undetected since neither name is in `suspect_names`. The heuristic limitation is undocumented.

**Fix:** Add comment documenting the heuristic limitation.

### W3: SR-05 evidence overstatement
**File:** `SECURITY_POSTURE.md`
**Reviewer:** security-reviewer

SR-05 reverse-mapping table lists 4 Hypothesis tests as evidence, but those cover `_sanitize_upstream_error` only — not full log output scanning. Best-effort tier is correct but evidence column overstates.

**Fix:** Add "(error sanitization only; no full log capture test)" to SR-05 evidence cell.

### W4: Shard A glossary definition conflates shard with decoy
**File:** `SECURITY_POSTURE.md:46`
**Reviewer:** security-reviewer

Glossary says Shard A "stored as low-entropy decoy" but `split_key()` returns high-entropy output. The decoy is a separate value written to `.env`.

**Fix:** Distinguish Shard A (high-entropy, stored locally) from decoy value (low-entropy `.env` placeholder).

### W5: Forensic logging gap underweighted
**File:** `SECURITY_POSTURE.md`
**Reviewer:** security-reviewer

Missing audit trail for gate denials means attacker probing leaves no trace. "Low" severity may be too generous.

**Fix:** Consider bumping "No gate denial audit log" to Medium in residual risk table.

### W6: Overclaim check is case-sensitive
**File:** `tests/test_security_posture.py`
**Reviewer:** qa-expert

`"SOC 2 certified"` would miss `"Soc 2 Certified"`. Compare against `text.lower()`.

**Fix:** Use case-insensitive check.

## Nits (5)

### N1: Trust boundary diagram test too loose
**Reviewer:** qa-expert
Any fenced code block passes the test. Drop `"┌"` and bare backtick fallbacks.

### N2: `suspect_names` set duplicated
**Reviewer:** code-reviewer
Same set at lines ~598 and ~648. Promote to class constant.

### N3: Duplicate AST traversal logic between two SR-07 test methods
**Reviewer:** code-reviewer

### N4: `TestSecurityPostureExists` is redundant
**Reviewer:** qa-expert
The skipif marker already handles the missing-file case.

### N5: Commit hash in SECURITY_POSTURE.md header stale
**Reviewer:** security-reviewer
References commit before the doc was created.

## Additional Findings

### Missing known limitation
**Reviewer:** security-reviewer
`api_key.decode()` in `app.py:353` creates an immutable `str` copy that cannot be zeroed. Noted in code comment but not in Known Limitations.

### `worthless lock` reference in breach scenario
**Reviewer:** security-reviewer
Breach response says "Re-enroll via `worthless lock`" but no dedicated lock subcommand exists. Should note V1 uses `worthless enroll`.

## Verdict

**needs-fixes** — No blockers, but 6 warnings should be addressed before merging. The most important are W3 (SR-05 evidence overstatement) and W4 (Shard A glossary confusion) which affect the honesty of the posture document.
