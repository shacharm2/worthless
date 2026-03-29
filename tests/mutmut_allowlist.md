# Mutmut Equivalent Mutant Allowlist

Mutants listed here are provably equivalent -- the mutation cannot change
observable behavior.  Each entry includes a one-line proof.

## splitter.py

| Mutant | Mutation | Why equivalent |
|--------|----------|----------------|
| `x_split_key__mutmut_19` | `token_bytes(32)` -> `token_bytes(None)` | `secrets.token_bytes(None)` defaults to 32 bytes per CPython docs. Test `test_nonce_is_exactly_32_bytes` asserts length = 32 regardless. Marked `# mutmut: skip`. |
