# Domain Pitfalls

**Domain:** Split-key API proxy / key security
**Researched:** 2026-03-14

## Critical Pitfalls

Mistakes that cause security failures or architectural rewrites.

### Pitfall 1: Reconstructed Key Lives in Process Memory

**What goes wrong:** The entire point of split-key is "key never exists in one place." But during every proxied request, the proxy XORs shards together and holds the full key in Python process memory. A memory dump, core dump, or `/proc/pid/mem` read exposes every key the proxy has reconstructed. The key you "eliminated" is alive in RAM for the duration of every request -- and in Python, string objects are immutable and not zeroed on deallocation, so the key may persist in the heap long after the request completes.
**Why it happens:** XOR reconstruction is the obvious implementation. Python's garbage collector is non-deterministic, and `str`/`bytes` objects cannot be reliably wiped. Developers assume "transient" means "safe."
**Consequences:** An attacker with process access (malicious dependency, container escape, debug endpoint) can extract full keys from memory. The security promise is hollow.
**Prevention:**
- Minimize reconstruction window: reconstruct, inject into the outbound HTTP header, then immediately overwrite the `bytearray` (use mutable `bytearray`, never `bytes` or `str`).
- In the Rust hardening phase, use `zeroize` crate to guarantee memory zeroing on drop.
- Never log, serialize, or store the reconstructed key -- it exists only as a local variable in the request handler.
- Document this as a known limitation in the PoC phase. The Python PoC is "better than plaintext env vars" but not "memory-safe." Rust phase is where the real promise is delivered.
**Detection:** Audit for any code path where the reconstructed key is assigned to anything other than a short-lived local `bytearray`. Search for the reconstructed value appearing in logs, error messages, or exception tracebacks.
**Phase:** PoC must use `bytearray` + explicit zeroing. Rust hardening phase delivers the real guarantee.
**Confidence:** HIGH -- this is a well-documented limitation of secret sharing schemes. Wikipedia's secret sharing article and multiple cryptography courses note "the secret must exist in one place when reassembled."

---

### Pitfall 2: Weak or Predictable Shard Generation

**What goes wrong:** XOR secret sharing requires Shard A to be generated from a CSPRNG. If the random bytes are predictable (e.g., using Python's `random` module instead of `secrets`/`os.urandom`), an attacker who obtains Shard B can brute-force Shard A trivially. The split becomes security theater.
**Why it happens:** `random.randbytes()` exists in Python 3.9+ and looks correct. Developers reach for `random` out of habit. The code works identically -- only the entropy source differs.
**Consequences:** Complete key recovery from a single shard. The entire security model collapses.
**Prevention:**
- Use `secrets.token_bytes(len(key))` or `os.urandom(len(key))` exclusively. Never import `random` in any security-related module.
- Add a linting rule or test that fails if `import random` appears in the crypto/splitting module.
- Assert shard length equals key length (XOR one-time-pad requires equal-length key).
**Detection:** `grep -r "import random" src/` in CI. Unit test that verifies shard entropy (statistical randomness test on generated shards).
**Phase:** PoC phase, day one. Non-negotiable.
**Confidence:** HIGH -- PEP 506 explicitly created the `secrets` module because developers were misusing `random` for security purposes.

---

### Pitfall 3: Shard B at Rest Is the Crown Jewel

**What goes wrong:** The server stores Shard B. If Shard B is stored in plaintext (SQLite column, config file, environment variable), compromising the server gives the attacker half the key. Combined with a leaked client shard (in a `.env` file committed to git, in CI logs, in a Docker layer), the key is fully reconstructed. You have moved the "one thing to steal" problem from "API key" to "Shard B" without actually improving the threat model.
**Why it happens:** Developers focus on the splitting ceremony and forget that storage security of shards matters just as much as storage security of the original key.
**Consequences:** The security improvement is marginal: instead of stealing one secret, the attacker steals two -- but both may be in predictable locations.
**Prevention:**
- Encrypt Shard B at rest using a key derived from a passphrase or hardware token (even a simple Fernet-encrypted blob in SQLite is better than plaintext).
- In the PoC, use SQLite with Shard B encrypted via a server-side secret (loaded from env var). This means compromise requires env var + database, not just one.
- Document the threat model honestly: split-key protects against "attacker steals one thing" (leaked env var, compromised CI, stolen laptop). It does NOT protect against "attacker owns the server process."
- Never store both shards in the same trust boundary.
**Detection:** Review storage layer -- if Shard B can be read with a simple `SELECT` query without decryption, this pitfall is active.
**Phase:** PoC phase. Storage encryption for Shard B from the start.
**Confidence:** HIGH -- this is the fundamental limitation of 2-of-2 secret sharing that every cryptography textbook covers.

---

### Pitfall 4: XOR Without Authentication Enables Shard Tampering

**What goes wrong:** Plain XOR provides confidentiality (if the random pad is strong) but zero integrity. An attacker who can modify Shard A in transit can flip arbitrary bits in the reconstructed key. The proxy then sends a corrupted key to the upstream API, which either fails (denial of service) or -- worse -- could be manipulated to produce a valid different key if the attacker knows the key structure.
**Why it happens:** XOR is chosen for simplicity. Adding authentication (HMAC, authenticated encryption) feels like over-engineering for a PoC.
**Consequences:** Denial of service (corrupted keys always fail). In adversarial scenarios, bit-flipping attacks on known-structure keys.
**Prevention:**
- Store a hash/HMAC of the original key at enrollment time. After XOR reconstruction, verify the HMAC before using the key. This adds ~1ms and catches tampering or corruption.
- Use HMAC-SHA256 with a server-side secret as the HMAC key.
- Reject and alert on HMAC mismatch -- this indicates tampering or data corruption.
**Detection:** Test: tamper with one byte of Shard A, send to proxy, verify the request is rejected (not forwarded with a bad key).
**Phase:** PoC phase. The HMAC check is trivial to implement and prevents a class of subtle bugs (corrupted shards producing silent failures).
**Confidence:** HIGH -- XOR's lack of authentication is a textbook property.

---

### Pitfall 5: Timing Side-Channels in Shard Validation

**What goes wrong:** If the proxy compares shards, tokens, or reconstructed keys using standard Python `==`, the comparison short-circuits on the first differing byte. An attacker can measure response times to determine correct bytes one at a time.
**Why it happens:** `==` is the natural comparison operator. Constant-time comparison requires deliberate use of `hmac.compare_digest()`.
**Consequences:** Shard or token values can be recovered byte-by-byte through timing analysis. Realistic over localhost (low network jitter), harder over the internet but not impossible.
**Prevention:**
- Use `hmac.compare_digest()` for ALL security-sensitive comparisons (shard validation, HMAC verification, token checks).
- Never use `==` to compare secrets, shards, or derived values.
- Add a linting rule or code review checklist item.
**Detection:** `grep -r "== " src/` in security-critical paths. Look for any comparison of `bytes`/`str` values that contain secret material.
**Phase:** PoC phase. One-line fix per comparison site.
**Confidence:** HIGH -- timing attacks on string comparison are well-documented (Wikipedia, NIST publications).

## Moderate Pitfalls

### Pitfall 6: Proxy Becomes a Single Point of Failure

**What goes wrong:** Every API call now routes through the proxy. If the proxy crashes, hangs, or has a bug, all LLM API calls fail. Developers who relied on direct API calls now have a new dependency that can break their entire workflow.
**Prevention:**
- Implement a health check endpoint.
- Graceful degradation: if the proxy can't start, `worthless wrap` should warn loudly rather than silently breaking all API calls.
- Keep the proxy minimal -- resist feature creep that increases crash surface.
- Consider a "bypass mode" for emergencies (with appropriate warnings).
**Phase:** PoC phase. The proxy must be more reliable than not having it.

### Pitfall 7: Environment Variable Injection Breaks Existing Tooling

**What goes wrong:** `worthless wrap` overrides `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` env vars to point through the proxy. But many tools have hardcoded assumptions about these env vars (SDK initialization, config file precedence, multi-key setups). Overriding them can break tools that: (a) validate key format before making requests, (b) use the key for non-HTTP purposes (e.g., SDK telemetry), (c) read keys from config files that take precedence over env vars.
**Prevention:**
- Study how the OpenAI and Anthropic Python SDKs resolve API keys and base URLs. The proxy should work by overriding `OPENAI_BASE_URL` / `ANTHROPIC_BASE_URL` to point to localhost, and providing a "dummy" key that the proxy recognizes as a shard identifier.
- Test against actual SDK versions, not assumptions about SDK behavior.
- Document which SDK versions are tested and supported.
**Detection:** Integration tests that actually instantiate the OpenAI and Anthropic Python clients and make a (mocked) request through the proxy.
**Phase:** PoC phase. This is the core UX -- if wrapping breaks tools, the product is useless.

### Pitfall 8: Latency Overhead Kills the UX Promise

**What goes wrong:** Adding a localhost proxy hop adds latency to every API call. For streaming responses (which most LLM usage involves), per-chunk proxy overhead can degrade perceived performance. If the proxy buffers responses instead of streaming them, the UX is significantly worse.
**Prevention:**
- Implement proper HTTP streaming (chunked transfer encoding) from day one. Do not buffer full responses.
- Benchmark: the proxy should add <5ms to time-to-first-byte on localhost.
- Use `httpx` with async streaming for upstream calls, not `requests`.
- FastAPI with `StreamingResponse` for downstream delivery.
**Detection:** Benchmark test comparing direct API call vs. proxied call latency.
**Phase:** PoC phase. Streaming must work from the start -- retrofitting it is painful.

### Pitfall 9: Key Leakage Through Error Messages and Tracebacks

**What goes wrong:** Python's default exception handling includes local variables in tracebacks. If the reconstructed key is a local variable when an exception occurs, it appears in the traceback -- in logs, in stderr, in error reporting services. One unhandled exception in the request path and the key is in plaintext in your log file.
**Prevention:**
- Wrap the reconstruction + upstream call in a try/except that explicitly zeros the key bytearray before re-raising.
- Configure structured logging that never includes raw exception tracebacks in production.
- Use a custom exception handler in FastAPI that sanitizes responses.
- Never include request headers (which contain the reconstructed key) in error logs.
**Detection:** Trigger an intentional error during a proxied request and inspect the log output for any key material. Automated test for this.
**Phase:** PoC phase. The logging constraint is already in PROJECT.md -- enforce it architecturally, not just by policy.

### Pitfall 10: Provider API Format Differences Cause Silent Failures

**What goes wrong:** OpenAI and Anthropic have different authentication header formats (`Authorization: Bearer sk-...` vs. `x-api-key: sk-ant-...`), different error response formats, different rate limiting headers, and different streaming protocols (SSE variations). A generic proxy that doesn't understand these differences will produce confusing errors.
**Prevention:**
- Implement provider-specific adapters from the start, not a generic passthrough.
- Each adapter knows: auth header format, base URL, streaming format, error response structure.
- Test against real (or well-mocked) provider responses, including error cases.
**Detection:** Integration tests per provider that verify: auth header injection, streaming passthrough, error forwarding.
**Phase:** PoC phase. Two providers (OpenAI + Anthropic) means two adapters.

## Minor Pitfalls

### Pitfall 11: Port Conflicts on Localhost

**What goes wrong:** The proxy binds to a localhost port. If that port is in use (another dev tool, another proxy instance), the proxy fails to start. Developers in complex environments (Docker, multiple projects) hit this constantly.
**Prevention:** Default to a high, uncommon port (e.g., 19876). Support `--port` flag. On conflict, try the next port and report which port was used. Store the active port in a state file for `worthless wrap` to read.
**Phase:** PoC phase. Minor but affects first-run experience.

### Pitfall 12: Enrollment UX Exposes the Key Momentarily

**What goes wrong:** During `worthless enroll`, the user provides their API key (paste, env var, file). The key exists in the terminal's scrollback buffer, shell history, and process memory during the split operation. If enrollment is not carefully designed, the key is more exposed during "protection" than it was before.
**Prevention:**
- Use `getpass`-style input (no echo to terminal).
- Clear the key variable immediately after splitting.
- Warn users to clear terminal scrollback.
- Support reading from a file descriptor or pipe (`echo $KEY | worthless enroll --stdin`) to avoid shell history.
**Phase:** PoC phase. First impression matters.

### Pitfall 13: No Key Rotation Story

**What goes wrong:** Keys get rotated (by the provider, by policy, after a suspected breach). If re-enrollment requires manual shard management, users will skip rotation or break their setup. The enrollment ceremony that was charming the first time becomes a burden on the tenth.
**Prevention:** Design enrollment to be idempotent and non-destructive. `worthless enroll` with an existing key should update shards cleanly. Support `worthless rotate` that re-splits with new randomness without changing the upstream key.
**Phase:** Can defer to post-PoC, but the data model must support it from day one (versioned shards, not overwrite-only).

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|-------------|---------------|------------|
| PoC (Python + SQLite) | Memory safety of reconstructed keys (#1) | Use `bytearray` + explicit zeroing; document limitation honestly |
| PoC (Python + SQLite) | Weak RNG for shard generation (#2) | `secrets.token_bytes()` only, lint rule to block `import random` |
| PoC (CLI enrollment) | Key exposure during enrollment (#12) | `getpass`-style input, immediate zeroing |
| PoC (Proxy core) | Streaming not implemented (#8) | Async streaming from day one with `httpx` + `StreamingResponse` |
| PoC (Proxy core) | Error messages leak keys (#9) | Sanitizing exception handler, logging policy enforced architecturally |
| PoC (Provider support) | Provider-specific differences (#10) | Adapter pattern per provider, not generic passthrough |
| PoC (Wrap CLI) | Env var override breaks SDKs (#7) | Override base URL, not just API key; test against real SDK clients |
| Hardening (Rust) | Believing Python PoC memory guarantees are sufficient | Rust `zeroize` crate for real guarantees; this is the phase that delivers the security promise |
| Hardening (Pen-test) | Testing only happy path | Explicitly test: memory dumps, shard tampering, timing attacks, error-path key leaks |

## LLMjacking Threat Landscape Context

The threat this project addresses is real and escalating. Key context for prioritization:

- **Operation Bizarre Bazaar** (Dec 2025-Jan 2026): 35,000 attack sessions targeting exposed AI infrastructure, averaging 972 attacks/day. Stolen API keys resold on Discord/Telegram marketplaces.
- **$82K Gemini bill** (Feb 2026): The origin story for this project. Three-person team, 48 hours from normal to bankruptcy.
- **Supply chain attacks**: Malicious npm packages actively harvesting API keys for 9 LLM providers (Anthropic, OpenAI, Google, etc.).
- **Implication for Worthless**: The threat model is not theoretical. Attackers are automated, fast, and monetized. The proxy must assume keys are actively being hunted, not just passively at risk.

## Sources

- [Secret Sharing - Wikipedia](https://en.wikipedia.org/wiki/Secret_sharing) - Reconstruction requires key to exist in one place
- [PEP 506 - secrets module](https://peps.python.org/pep-0506/) - Why `random` is insufficient for security
- [Python secrets module docs](https://docs.python.org/3/library/secrets.html) - CSPRNG usage
- [Timing attack - Wikipedia](https://en.wikipedia.org/wiki/Timing_attack) - Side-channel via comparison timing
- [KeePass CVE-2023-32784](https://www.sysdig.com/blog/keepass-cve-2023-32784-detection/) - Memory dump credential extraction
- [Memory Dumping Attacks](https://www.anjuna.io/blog/memory-dumping-attacks-are-not-just-a-theoretical-concern) - Memory-resident secrets vulnerability
- [Operation Bizarre Bazaar - Pillar Security](https://www.pillar.security/blog/operation-bizarre-bazaar-first-attributed-llmjacking-campaign-with-commercial-marketplace-monetization) - LLMjacking campaign details
- [$82K Gemini API Key Theft - The Register](https://www.theregister.com/2026/03/03/gemini_api_key_82314_dollar_charge/) - Origin incident
- [Malicious npm packages harvesting keys](https://thehackernews.com/2026/02/malicious-npm-packages-harvest-crypto.html) - Supply chain key theft
- [Reverse Proxy Bottleneck](https://perlod.com/tutorials/resolve-reverse-proxy-bottleneck/) - Proxy performance issues
- [OWASP Cryptographic Failures](https://www.authgear.com/post/cryptographic-failures-owasp) - Implementation anti-patterns
