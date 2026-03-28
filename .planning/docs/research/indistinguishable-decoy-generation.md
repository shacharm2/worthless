# Research: Indistinguishable Decoy Generation

## Problem
Current decoys use repeating `WRTLS` filler pattern — instantly recognizable as fake to any attacker who eyeballs the .env. This undermines the core value prop: keys should be *worthless to steal*, meaning an attacker shouldn't be able to tell if what they found is real or decoy.

## Requirements
1. **Indistinguishable from real keys** — same entropy, same character distribution, same length, same prefix format
2. **Detectable by worthless** — the system must still know it's a decoy without relying on entropy threshold
3. **Non-reconstructable** — knowing the decoy doesn't help recover the real key

## Current Approach (broken)
- Low-entropy filler (`WRTLS` repeat) → scanner filters by Shannon entropy < 4.5
- Prefix preserved (`sk-proj-` + 8 hex chars from shard hash)
- **Weakness**: Any human or script can spot the pattern

## Research Directions

### A: Cryptographic decoy with embedded marker
Generate a high-entropy random string that LOOKS like a real key. Embed a hidden marker (HMAC tag) that only worthless can verify.
- Decoy = prefix + HMAC(fernet_key, alias)[:N] + random_fill
- Detection: recompute HMAC, check if first N chars match
- Pro: indistinguishable to outsiders, deterministic for worthless
- Con: requires fernet_key access for detection (scan in CI without ~/.worthless?)

### B: Registry-based detection
Store decoy values (or their hashes) in the enrollments table. Scanner checks if a found value matches a known decoy hash.
- Pro: no entropy dependency, works with any decoy format
- Con: requires DB access for scanning (same CI concern)

### C: Provider API ping
Generate a real-looking decoy, then verify it's actually invalid by pinging the provider's API.
- Pro: ground truth — if the API rejects it, it's not a real key
- Con: network dependency, rate limits, costs, privacy (sending decoy to provider)

### D: Hybrid — high-entropy decoy + hash registry
Generate random-looking decoys (high entropy, correct format per provider). Store hash(decoy) in enrollments table. Scanner checks hash match for is_protected.
- Detection path: hash(found_value) in enrollment_hashes → protected
- Pro: indistinguishable, no entropy hack, works offline with DB
- Con: scan without DB access falls back to "unknown" (acceptable?)

## Measurement
Before implementing, define "indistinguishable":
- Statistical test: can a classifier distinguish real keys from decoys? (character frequency, bigram distribution, entropy profile)
- Automated: generate 100 real keys + 100 decoys, run distinguisher — should be ~50% accuracy (random chance)

## Impact on Current Architecture
- Remove `_ENTROPY_THRESHOLD` dependency from scanner
- Implement the hash-based enrollment lookup we already have a TODO for in scanner.py
- Change `_make_decoy()` in lock.py to generate high-entropy output
- Update `scan_files()` to use hash comparison instead of entropy filtering

## Priority
HIGH — this is a core security property, not a cosmetic issue. An attacker who can distinguish real from fake keys defeats the entire threat model.
