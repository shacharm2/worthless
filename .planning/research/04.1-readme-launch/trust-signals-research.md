# Trust Signals for Security Open Source Projects

**Date:** 2026-03-31
**Purpose:** Research what trust signals successful cybersecurity and secrets-management repos use, and which are appropriate for Worthless pre-release.

---

## 1. External Validation Badges

### What top repos actually display

| Badge | Who uses it | Free? | Pre-release viable? |
|-------|-------------|-------|---------------------|
| **OpenSSF Scorecard** | sigstore, kubernetes, curl, OpenSSF projects | Yes (GitHub Action) | **Yes** -- runs on any public repo. Scores 0-10 on branch protection, CI, dependencies, fuzzing, SAST, etc. |
| **CII Best Practices** (now OpenSSF Best Practices) | curl, OpenSSL, Linux kernel, Let's Encrypt | Yes (self-assessment questionnaire) | **Yes** -- passing level achievable pre-release. Documents what you DO, not what you claim. |
| **SLSA Build Level** | sigstore, Go ecosystem, npm | Yes (slsa-github-generator) | **Maybe** -- Level 1-2 achievable with GitHub Actions provenance. Level 3 requires more infra. |
| **SonarCloud** | Many OSS projects (free for public repos) | Yes | **Yes** -- free for public repos, shows code quality, security hotspots, coverage. |
| **Semgrep / Snyk badges** | Less common in READMEs | Free tier exists | **Meh** -- these are CI tools, not trust badges. Running them matters; displaying badges is unusual. |
| **OSS-Fuzz / ClusterFuzzLite** | curl, systemd, openssl, envoy | Yes (Google-sponsored) | **Not yet** -- requires C/C++/Rust targets or Python with atheris. Worth pursuing post-v1 for the Rust reconstruction service. |
| **Codecov / Coveralls** | Ubiquitous in quality-conscious repos | Yes | **Yes** -- coverage badge is table stakes. |
| **Sigstore / SLSA provenance** | Python packages via PyPI attestations | Yes | **Yes** -- `uv publish` supports PyPI attestations natively as of 2025. |

### Recommended badge set for Worthless pre-release

```
CI Status | Coverage | OpenSSF Scorecard | License (MIT) | PyPI Version | OpenSSF Best Practices (passing)
```

**Why these six:**
- CI + Coverage = "it works and we test it"
- OpenSSF Scorecard = automated, third-party, credible
- Best Practices = self-assessed but structured, shows security discipline
- License + PyPI = adoption friction reducers

**What to skip for now:**
- Snyk/Semgrep badges (run in CI but don't badge -- it's not the norm)
- OSS-Fuzz (need Rust targets first)
- SLSA Level 3 (overkill pre-release)
- "Evaluated by X" badges that don't exist in the ecosystem

### Setup effort

| Badge | Setup time | Maintenance |
|-------|------------|-------------|
| OpenSSF Scorecard | 30 min (add GitHub Action `ossf/scorecard-action`) | Zero -- auto-runs weekly |
| OpenSSF Best Practices | 2-3 hours (fill questionnaire at bestpractices.coreinfrastructure.org) | Quarterly review |
| SonarCloud | 1 hour (connect repo, add GitHub Action) | Zero |
| Codecov | 30 min (add to CI, upload token) | Zero |
| PyPI attestations | Built into `uv publish` | Zero |

---

## 2. Security Evaluation Claims

### What's credible

| Claim | Credibility | Examples |
|-------|-------------|---------|
| "Audited by [named firm]" | **Gold standard** -- Trail of Bits, Cure53, NCC Group, X41 | WireGuard (audited by X41), Signal (audited by multiple firms), age (audited by Cure53) |
| "OpenSSF Scorecard X/10" | **High** -- automated, reproducible, third-party | Any repo can get this for free |
| "Fuzzing by OSS-Fuzz" | **High** -- Google infrastructure, continuous | curl, systemd, envoy |
| "Mutation testing: X% killed" | **Medium-high** -- unusual and impressive when present | Very few repos advertise this; it's a differentiator |
| "Property-tested with Hypothesis" | **Medium** -- shows testing sophistication | Common in Haskell/Rust ecosystems, rare in Python security tools |
| "SAST clean (Semgrep/Bandit)" | **Medium** -- expected for security tools, not a differentiator | Most don't badge this, just run it |
| "Evaluated by LLM" | **Zero credibility** -- no repo does this, would invite ridicule | Nobody. Do not do this. |
| "Tested against leaked-key datasets" | **Low-medium** -- interesting but no standard methodology exists | Some secret scanners (TruffleHog, GitLeaks) reference this for their detection engines, but not as a badge |
| "CVE-free" | **Counterproductive** -- implies you track CVEs, invites scrutiny on a pre-release | Nobody badges this |

### What Worthless should claim

**Do:**
- Name crypto primitives explicitly (XOR secret sharing, AES-256-GCM, HMAC-SHA256)
- State architectural invariants as verifiable promises
- Link to SECURITY.md with responsible disclosure
- Show OpenSSF Scorecard score
- Mention mutation testing and property testing in a "Testing" section (see section 3)

**Do not:**
- Claim "military-grade encryption" or "unhackable"
- Reference LLM evaluation
- Claim CVE-free status
- Use "bank-level security" or similar marketing language

---

## 3. Mutation Testing / Property Testing as Trust Signal

### How security projects present test maturity

**Almost no security project prominently badges mutation testing or property testing.** But the ones that mention it get disproportionate credibility:

| Project | What they show | Where |
|---------|---------------|-------|
| **Hypothesis itself** | Property testing examples, links to academic papers | README |
| **cryptography (pyca)** | Mentions Hypothesis in contributing docs, not README | CONTRIBUTING.md |
| **rust-crypto ecosystem** | Property tests mentioned in changelogs and security advisories | Release notes |
| **WireGuard** | Formal verification mentioned (not property testing per se) | Whitepaper |

### Is Worthless's test suite worth advertising?

**Yes, but in the right place and framing.** 32 test files + 32 mutation tests + Hypothesis property tests is genuinely unusual for a Python project, especially a security tool. This is a legitimate differentiator.

**How to present it:**

```markdown
## Testing

Worthless has 32 test files covering all security-critical paths:

- **Unit + integration tests** for every crypto operation
- **Mutation testing** (mutmut) -- verifies tests catch real bugs, not just exercise code
- **Property-based testing** (Hypothesis) -- randomized invariant checking for:
  - Byte-length preservation across split/reconstruct
  - Tamper detection on every shard modification
  - Decoy indistinguishability (chi-squared validation)
- **SAST** (Semgrep, Bandit) in CI
- **Dependency auditing** (pip-audit) in CI
```

**Do NOT:**
- Put mutation kill rate in a badge (too niche, invites "only X%?" criticism)
- Claim "100% coverage" unless literally true
- Lead with testing -- it's a trust signal, not the value proposition

**Framing that works:** "We test like a security library, not a CLI tool." This sets the right expectation without overselling.

---

## 4. "What We Don't Protect Against" Sections

### Best examples in the wild

#### age (filippo.io/age)
- "age does not have a built-in concept of revocation."
- "age does not support arbitrary metadata."
- Explicit non-goals in the design document, linked from README.
- **Effect:** Builds enormous trust. Readers think "if they're this honest about limitations, the things they DO claim must be solid."

#### WireGuard
- "WireGuard does not concern itself with key distribution."
- "WireGuard is not a silver bullet for privacy."
- Short, direct statements in the whitepaper and website FAQ.

#### git-crypt
- "git-crypt does not encrypt file names or other metadata."
- "git-crypt does not support revoking access to previously encrypted files."
- These are in the README itself, not buried in docs.

#### Signal Protocol
- Documentation explicitly states what forward secrecy covers and what it doesn't.
- Separates "transport security" from "at-rest security."

#### SOPS
- Documents that keys (YAML/JSON) are NOT encrypted, only values.
- Explains exactly what metadata is visible.

#### minisign
- "minisign does not aim to be compatible with OpenPGP."
- "minisign does not support encryption."
- Extremely focused non-goals.

### Pattern: how to write non-goals that build trust

1. **Be specific, not vague.** "Does not protect against a compromised host OS" is useful. "Not a silver bullet" is noise.
2. **Explain why.** "We don't encrypt file names because git needs them for diffing" is better than just "we don't encrypt file names."
3. **Frame as design decisions, not apologies.** These are intentional scope boundaries, not missing features.
4. **Keep it short.** 3-7 bullet points. More than that and it reads like a liability disclaimer.
5. **Place it after the security model section.** Reader should understand what you DO before learning what you don't.

### Recommended "Non-Goals" section for Worthless

```markdown
## What Worthless Does NOT Protect Against

- **Compromised client machine.** If an attacker has code execution on the machine
  running `worthless enroll`, they can observe the full API key before splitting.
  Worthless protects keys in transit and at rest on the server, not on a pwned endpoint.

- **Compromised server + KMS simultaneously.** If an attacker compromises both the
  Worthless server AND the KMS service holding the encryption key for Shard B, they
  can reconstruct the API key. This is the standard threat model for any key-escrow
  system. Worthless minimizes this window (gate-before-reconstruct, memory zeroing).

- **Provider-side breaches.** Once a request reaches the LLM provider, Worthless has
  no control over how the provider stores or processes the API key or request data.

- **Denial of service against the proxy.** Worthless enforces spend caps and rate
  limits, but cannot prevent an attacker from consuming your budget up to the cap
  if they obtain Shard A.

- **Key rotation.** Worthless does not automatically rotate your upstream API keys.
  If you believe a key is compromised, revoke it at the provider and re-enroll.
```

---

## 5. Comparison Tables

### How security tools compare without attacking alternatives

#### Pattern: "Different tools for different threat models"

**WireGuard** does this masterfully by never mentioning OpenVPN or IPSec by name in the main README. Instead, the whitepaper has a "Comparison" section that focuses on architectural differences (kernel vs userspace, cryptographic agility vs opinionated) without calling alternatives bad.

**age** compares to GPG with a "Why not GPG?" section that's framed as "GPG solves a different, harder problem. age solves a narrower problem better."

**Signal** never names competitors. Just explains their protocol properties.

#### Pattern: Feature matrix focused on approach, not checkboxes

The worst comparison tables are checkbox matrices where your tool has all green and competitors have red Xs. This reads as dishonest.

**Better approach (used by Infisical, SOPS docs):**

| Aspect | Tool A approach | Tool B approach | Our approach |
|--------|----------------|-----------------|------------|
| Key storage | Centralized vault | File-based | Split between client and server |
| Access control | ACL-based | File permissions | Spend caps + rate limits |
| Threat model | Insider threat | At-rest encryption | Stolen key protection |

**Key principle:** Compare approaches and architectures, not features. Let the reader decide which approach fits their threat model.

#### Recommended framing for Worthless

Do NOT compare to Vault, SOPS, or age -- they solve different problems. Instead, compare to:

1. **"Just using environment variables"** -- the status quo for most developers
2. **"Provider-side usage limits"** -- what OpenAI/Anthropic offer natively
3. **"Secret scanning (GitLeaks, TruffleHog)"** -- detection vs prevention

Frame as layers of defense, not competitors:

```markdown
## How Worthless Fits Your Security Stack

| Layer | Tool | What it does |
|-------|------|-------------|
| **Prevention** | Worthless | Keys are worthless to steal -- split storage + spend caps |
| **Detection** | GitLeaks, TruffleHog | Finds keys already leaked in code/history |
| **Rotation** | Provider dashboards | Revoke and reissue compromised keys |
| **Storage** | Vault, SOPS, 1Password | Encrypt secrets at rest |

Worthless complements these tools. Use secret scanning to find leaks.
Use Worthless so leaks don't matter.
```

This positions Worthless as additive, not competitive. Much more credible for a pre-release project.

---

## 6. Responsible Disclosure / SECURITY.md

### Standards for pre-release

| Mechanism | When to use | Setup |
|-----------|-------------|-------|
| **GitHub Private Vulnerability Reporting** | **Now** -- free, zero-effort, built into GitHub | Settings > Security > Enable private vulnerability reporting |
| **SECURITY.md in repo root** | **Now** -- standard expectation | File pointing to GitHub's private reporting |
| **Dedicated email (security@domain)** | When you have a domain and team | Requires email infrastructure |
| **Bug bounty (HackerOne, Bugcrowd)** | Post-v1, when you have budget | Expensive, requires triage capacity |
| **security.txt (RFC 9116)** | When you have a website | `.well-known/security.txt` |

### What top repos include in SECURITY.md

**Minimal (pre-release appropriate):**

```markdown
# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.x (pre-release) | Yes |

## Reporting a Vulnerability

**Do not open a public issue for security vulnerabilities.**

Use [GitHub's private vulnerability reporting](link) to report security issues.

You will receive an acknowledgment within 48 hours and a detailed response
within 7 days indicating next steps.

## Security Model

See [SECURITY_RULES.md](SECURITY_RULES.md) for the project's security invariants
and mandatory constraints.

## Disclosure Policy

We follow coordinated disclosure. We will:
1. Confirm the vulnerability within 48 hours
2. Provide an estimated fix timeline within 7 days
3. Credit the reporter in the fix commit and release notes (unless anonymity requested)
4. Publish a security advisory via GitHub after the fix is released
```

**What the best repos add:**
- PGP key for encrypted reports (curl, Linux kernel)
- Scope definition ("in scope: proxy, CLI, crypto. Out of scope: documentation site")
- Known issues / accepted risks section
- Link to threat model document

### Recommendation for Worthless

1. Enable GitHub Private Vulnerability Reporting immediately
2. Create `SECURITY.md` in repo root with the minimal template above
3. Add scope definition (crypto, proxy, CLI are in scope)
4. Link to SECURITY_RULES.md for invariants
5. Skip PGP key, bug bounty, and security.txt until post-v1

---

## 7. Summary: Trust Signal Prioritization for Worthless Pre-Release

### Tier 1: Do now (before public release)

| Signal | Effort | Impact |
|--------|--------|--------|
| SECURITY.md with responsible disclosure | 30 min | High -- expected for any security project |
| GitHub Private Vulnerability Reporting | 5 min | High -- zero-effort, maximum credibility |
| OpenSSF Scorecard GitHub Action | 30 min | High -- automated, third-party validation |
| "What we don't protect against" section in README | 1 hour | High -- the single most trust-building section |
| Name crypto primitives in README | 30 min | High -- shows you know what you built |
| Coverage badge (Codecov) | 30 min | Medium -- table stakes |

### Tier 2: Do for v1 launch

| Signal | Effort | Impact |
|--------|--------|--------|
| OpenSSF Best Practices badge (passing level) | 3 hours | High -- structured self-assessment |
| SonarCloud integration | 1 hour | Medium -- free code quality dashboard |
| SLSA Level 1 provenance | 2 hours | Medium -- supply chain trust |
| "How Worthless fits your stack" comparison | 1 hour | Medium -- positions without attacking |
| Testing section highlighting mutation/property tests | 30 min | Medium -- differentiator |

### Tier 3: Post-v1

| Signal | Effort | Impact |
|--------|--------|--------|
| Professional security audit | $$$ + weeks | Very high -- the gold standard |
| OSS-Fuzz integration (Rust service) | Days | High -- continuous fuzzing |
| Bug bounty program | Ongoing cost | Medium -- shows confidence |
| SLSA Level 3 | Days | Medium -- full provenance chain |
| CVE numbering authority registration | Hours | Low until you have CVEs to issue |

---

## Sources

- [OpenSSF Scorecard](https://securityscorecards.dev/) -- automated security health checks
- [OpenSSF Best Practices](https://www.bestpractices.dev/) -- self-assessment badge program
- [age design document](https://age-encryption.org/design) -- exemplary non-goals section
- [WireGuard whitepaper](https://www.wireguard.com/papers/wireguard.pdf) -- comparison without attacking
- [git-crypt README](https://github.com/AGWA/git-crypt) -- honest limitations
- [SOPS README](https://github.com/getsops/sops) -- crypto transparency
- [Infisical README](https://github.com/Infisical/infisical) -- badge strategy
- [Signal Protocol docs](https://signal.org/docs/) -- security claims done right
- [minisign](https://jedisct1.github.io/minisign/) -- focused non-goals
- [GitHub Private Vulnerability Reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
- [SLSA framework](https://slsa.dev/) -- supply chain integrity levels
- [PyPI attestations](https://docs.pypi.org/attestations/) -- publish-time provenance
