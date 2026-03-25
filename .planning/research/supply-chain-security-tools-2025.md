# Python Supply Chain Security Tools (2025-2026)

Research date: 2025-03-25
Context: Worthless project (uv, Python 3.12, security-first architecture)

---

## 1. Lockfile Integrity

### uv built-in: `--locked` / `--frozen`
- **What**: `uv sync --locked` errors if uv.lock doesn't match pyproject.toml. `--frozen` installs exactly what's in the lockfile without checking pyproject.toml.
- **Pre-commit**: Yes, trivially (`uv lock --check` as a hook)
- **Maintained**: Yes (core uv feature)
- **License**: MIT (Apache-2.0 dual)
- **Verdict**: Use this. It's the baseline for reproducible builds.

### uv.lock hash verification
- uv stores hashes in uv.lock for all resolved packages. Issue [#4924](https://github.com/astral-sh/uv/issues/4924) tracked auditing hash handling. As of 2025, hashes are verified during install.
- **Note**: CVE-2025-54368 revealed a ZIP parsing differential between uv and pip that could allow crafted wheels to extract differently. Fixed in uv v0.8.6+. Keep uv updated.

---

## 2. SLSA / SBOM Generation

### Syft (Anchore)
- **What**: Generates SBOMs in SPDX and CycloneDX formats from container images, filesystems, and lockfiles.
- **Pre-commit**: Not typical (CI gate, not commit-time). Could be scripted as a hook.
- **Maintained**: Yes, actively
- **License**: Apache-2.0
- **uv support**: Reads requirements.txt; uv.lock support unclear (may need `uv export`).

### cosign + Sigstore
- **What**: Keyless signing/verification of artifacts. Signs SBOMs, container images, attestations.
- **Pre-commit**: Not practical (signing happens at release, not commit). Use in CI.
- **Maintained**: Yes, very actively (Linux Foundation / OpenSSF)
- **License**: Apache-2.0

### in-toto
- **What**: Framework for securing the entire software supply chain. Defines layouts (expected steps) and links (evidence of execution).
- **Pre-commit**: No. It's a CI/CD framework.
- **Maintained**: Yes (CNCF project)
- **License**: Apache-2.0

### PyPI PEP 740 Attestations
- **What**: Sigstore-based attestations on PyPI. If a package uses Trusted Publishing + GitHub Actions, attestations are produced automatically. As of March 2026, 132,360+ packages have attestations.
- **Consumer side**: Verification in pip/uv is being developed but not yet integrated as of early 2026. Track progress at [Are we PEP 740 yet?](https://trailofbits.github.io/are-we-pep740-yet/)
- **Verdict**: Important to watch. Not yet actionable for pre-commit/install verification.

---

## 3. Dependency Confusion Prevention

### GuardDog (Datadog)
- **What**: Scans PyPI packages for malicious indicators including typosquatting (Levenshtein distance against top 5000 packages), obfuscated code, suspicious network calls, and binary artifacts.
- **Pre-commit**: Yes, can be run as a hook to scan new dependencies.
- **Maintained**: Yes, actively (OpenSSF sandbox project as of March 2025)
- **License**: Apache-2.0
- **Verdict**: Best open-source option for typosquatting and malicious package detection. Run it when adding new deps.

### pypi-scan (IQTLabs)
- **What**: Scans PyPI for typosquatting specifically.
- **Pre-commit**: Could be adapted but not designed for it.
- **Maintained**: Minimal activity since 2023.
- **License**: Apache-2.0
- **Verdict**: GuardDog is strictly better.

### Phylum
- **What**: Commercial supply chain security platform. Scans for malware, typosquatting, dependency confusion, author risk.
- **Pre-commit**: Yes, official pre-commit hook.
- **Maintained**: Yes, but **free Community tier was sunset Feb 2025**. Teams/Enterprise only.
- **License**: Proprietary (paid)
- **Verdict**: Powerful but no longer free. Evaluate if budget allows.

### Socket.dev
- **What**: Supply chain security platform. Deep package inspection (behavior analysis, not just CVE matching). Supports Python, JS, Go.
- **Pre-commit**: GitHub App integration (PR comments). No standalone pre-commit hook.
- **Maintained**: Yes, actively (VC-funded startup)
- **License**: Free tier for open source, paid for private repos.
- **Verdict**: Excellent for CI/PR gates. Not a pre-commit hook.

---

## 4. Binary/Wheel Verification

### uv hash verification (built-in)
- uv verifies hashes of downloaded distributions against lockfile hashes.
- Use `uv sync --locked` to ensure lockfile integrity + hash match.
- **Verdict**: This is your primary defense. Combined with `--locked`, tampered wheels will fail.

### pip --require-hashes
- pip equivalent. Not relevant since project uses uv.

### PEP 740 attestations (future)
- Will eventually allow verifying that a wheel was built by a specific CI identity. Not yet integrated into installers.

---

## 5. Vulnerability Scanning (pip-audit and alternatives)

### pip-audit (Trail of Bits / Google)
- **What**: Audits Python environments against PyPA Advisory Database and OSV.
- **Pre-commit**: Yes, works as pre-commit hook.
- **Maintained**: Yes, actively (Trail of Bits)
- **License**: Apache-2.0
- **uv compatibility**: Works with requirements.txt. Use `uv export --format requirements-txt | pip-audit -r /dev/stdin`.
- **Verdict**: Still the gold standard for OSS Python vuln scanning.

### uv-secure
- **What**: Scans uv.lock directly (no venv needed). Queries OSV for advisory data.
- **Pre-commit**: Yes, designed for it.
- **Maintained**: Yes (community, Owen Lamont)
- **License**: MIT
- **uv compatibility**: Native uv.lock support -- this is its primary purpose.
- **Verdict**: Best uv-native option. Lighter than pip-audit for lockfile scanning.

### uv-audit (PyPI package)
- **What**: Another community tool wrapping vulnerability scanning for uv.
- **Pre-commit**: Possible.
- **Maintained**: Newer, less established than uv-secure.
- **License**: MIT
- **Verdict**: Watch but prefer uv-secure or pip-audit for now.

### uv audit (built-in, planned)
- **What**: Feature request [#9189](https://github.com/astral-sh/uv/issues/9189) for native `uv audit` command. Actively discussed. Some implementation work has begun (--service-format, --service-url options mentioned in changelogs).
- **Verdict**: When shipped, this will likely become the default. Monitor.

### PySentry
- **What**: Checks PyPA Advisory Database, PyPI, and OSV.dev simultaneously. Supports uv.lock, poetry.lock, pyproject.toml, requirements.txt.
- **Pre-commit**: Possible.
- **Maintained**: Newer tool, less established.
- **License**: Open source.
- **Verdict**: Promising multi-source scanner. Evaluate when more mature.

### osv-scanner (Google)
- **What**: General-purpose vulnerability scanner using OSV database. Supports many ecosystems including Python.
- **Pre-commit**: Yes.
- **Maintained**: Yes, actively (Google)
- **License**: Apache-2.0
- **Verdict**: Good alternative to pip-audit, especially if scanning multiple ecosystems.

### Safety (safetycli)
- **What**: Commercial vulnerability scanner with free tier. Malicious package detection.
- **Pre-commit**: Yes, via pre-commit-hooks-safety.
- **Maintained**: Yes, but freemium model changed -- free tier limited.
- **License**: Proprietary database (tool is open source).
- **Verdict**: Database quality is good but free tier limitations make pip-audit preferable.

---

## 6. Sigstore / cosign for Pre-commit

**Short answer: Not practical for pre-commit hooks.**

Sigstore/cosign are designed for:
- Signing release artifacts in CI/CD
- Verifying downloaded artifacts
- Attesting build provenance

They require OIDC identity flows that don't fit the pre-commit model. Use them in:
- CI release pipelines (sign wheels/containers)
- Deployment verification (verify signatures before deploy)

---

## 7. uv-Specific Security Features Summary

| Feature | Status | Notes |
|---------|--------|-------|
| Lockfile hashes | Shipping | Hashes stored in uv.lock, verified on install |
| `--locked` flag | Shipping | Errors if lockfile doesn't match pyproject.toml |
| `--frozen` flag | Shipping | Install from lockfile only, no resolution |
| `uv audit` | In development | Native vulnerability scanning (issue #9189) |
| PEP 740 verification | Planned | Attestation verification in installer |
| ZIP parsing security | Patched | CVE-2025-54368 fixed in v0.8.6 |

---

## Recommended Stack for Worthless

### Pre-commit hooks (.pre-commit-config.yaml)

```yaml
# 1. Lockfile integrity
- repo: local
  hooks:
    - id: uv-lock-check
      name: Verify uv.lock is up to date
      entry: uv lock --check
      language: system
      pass_filenames: false
      files: '(pyproject\.toml|uv\.lock)$'

# 2. Vulnerability scanning (pick one)
- repo: https://github.com/owenlamont/uv-secure
  rev: <latest>
  hooks:
    - id: uv-secure
      name: Scan uv.lock for vulnerabilities

# 3. Secret detection (already critical for Worthless)
- repo: https://github.com/Yelp/detect-secrets
  rev: <latest>
  hooks:
    - id: detect-secrets

# 4. Static security analysis
- repo: https://github.com/PyCQA/bandit
  rev: <latest>
  hooks:
    - id: bandit
      args: ['-r', 'src/']

# 5. Dependency typosquatting (run on dependency changes)
- repo: local
  hooks:
    - id: guarddog-scan
      name: Scan for malicious dependencies
      entry: guarddog pypi verify
      language: system
      pass_filenames: false
      files: '(pyproject\.toml|uv\.lock)$'
```

### CI gates (GitHub Actions)

```yaml
# In addition to pre-commit hooks:
- pip-audit (via uv export)          # Comprehensive vuln scan
- osv-scanner                        # Cross-ecosystem vuln scan
- syft                               # SBOM generation
- cosign sign                        # Sign release artifacts
- guarddog                           # Malicious package scan
- semgrep                            # SAST
```

### Priority order for implementation

1. **uv lock --check** -- Zero cost, catches lockfile drift (do this now)
2. **uv-secure** -- Native uv.lock vuln scanning (do this now)
3. **detect-secrets** -- Critical for a key-management project (do this now)
4. **bandit** -- Already in your test matrix (formalize as pre-commit)
5. **GuardDog** -- Run when adding new dependencies
6. **pip-audit in CI** -- Belt-and-suspenders vuln scanning
7. **Syft SBOM** -- Generate at release time
8. **cosign** -- Sign releases when publishing

---

## Sources

- [pip-audit (GitHub)](https://github.com/pypa/pip-audit)
- [uv-secure (GitHub)](https://github.com/owenlamont/uv-secure)
- [uv audit feature request #9189](https://github.com/astral-sh/uv/issues/9189)
- [uv hash audit #4924](https://github.com/astral-sh/uv/issues/4924)
- [uv security advisory CVE-2025-54368](https://astral.sh/blog/uv-security-advisory-cve-2025-54368)
- [GuardDog (Datadog Security Labs)](https://securitylabs.datadoghq.com/articles/guarddog-identify-malicious-pypi-packages/)
- [GuardDog (OpenSSF)](https://openssf.org/blog/2025/03/28/guarddog-strengthening-open-source-security-against-supply-chain-attacks/)
- [PyPI PEP 740 attestations](https://blog.pypi.org/posts/2024-11-14-pypi-now-supports-digital-attestations/)
- [Are we PEP 740 yet?](https://trailofbits.github.io/are-we-pep740-yet/)
- [Sigstore blog on PyPI attestations GA](https://blog.sigstore.dev/pypi-attestations-ga/)
- [Phylum documentation](https://docs.phylum.io/)
- [Socket.dev](https://socket.dev/)
- [Syft + Sigstore SBOM attestations (Anchore)](https://anchore.com/sbom/creating-sbom-attestations-using-syft-and-sigstore/)
- [Supply chain security in 2025 overview](https://faithforgelabs.com/blog_supplychain_security_2025.php)
- [Defense in depth Python supply chain (Bernat Gabor)](https://bernat.tech/posts/securing-python-supply-chain/)
- [PySentry](http://pysentry.com/)
