# Security Quick Wins (fold into WOR-300 CI)

## Already in place (confirmed during WOR-305)

- Semgrep (general SAST)
- Bandit (Python SAST)
- Gitleaks (secret scanning)
- CodeQL (GitHub Advanced Security)
- deptry (Python supply-chain)
- Zizmor (GitHub Actions unpinned-uses + artipacked + credential persistence)
- shellcheck (install.sh + verify scripts; WOR-305)

## To add in WOR-300 scope

### Custom semgrep rules (`.semgrep/install-sh.yml`)

```yaml
rules:
  - id: install-sh-curl-must-pin-sha256
    message: "curl | sh without preceding sha256 verification — embed a pin"
    patterns:
      - pattern-either:
          - pattern: 'curl $URL | sh'
          - pattern: 'curl -fsSL $URL | sh'
          - pattern: 'wget -qO- $URL | sh'
      - pattern-not-inside: |
          sha256sum -c
    severity: ERROR
    languages: [bash, sh]

  - id: no-eval-curl
    message: "eval $(curl ...) — opaque execution, blocks LLM audit"
    patterns:
      - pattern: 'eval $(curl ...)'
      - pattern: 'eval `curl ...`'
    severity: ERROR
    languages: [bash, sh]

  - id: no-base64-payload
    message: "Base64-encoded payload in install.sh — blocks LLM audit"
    patterns:
      - pattern: 'echo $X | base64 -d | sh'
      - pattern: 'echo $X | base64 -d | bash'
    severity: ERROR
    languages: [bash, sh]
```

### GitHub repo rulesets

- **Protected tag patterns**: `v*` — no force-push, no delete (threat-model F-34/35)
- **Require signed commits + tags** on main (threat-model F-12)
- **Actions environment `worthless-sh-prod`** with required reviewers for Worker deploy
- **Scoped short-lived Cloudflare API token** in that environment (not account-wide)

### Deploy-time verification (`.github/workflows/deploy-worker.yml`)

Before `wrangler deploy`:
1. Verify tag ref signature against maintainer keyring
2. Verify `sha256(install.sh)` matches expected value (from release notes)
3. Verify `install.sh.sig` via `cosign verify-blob`
4. Abort deploy if any check fails

### Sigstore signing (~20 lines of CI)

GitHub Action on release tag:
```yaml
- uses: sigstore/cosign-installer@<sha>
- run: |
    cosign sign-blob --yes install.sh \
      --output-signature install.sh.sig \
      --output-certificate install.sh.cert
- uses: softprops/action-gh-release@<sha>
  with:
    files: |
      install.sh
      install.sh.sig
      install.sh.cert
```

Users verify:
```sh
cosign verify-blob --certificate-identity=https://github.com/shacharm2/worthless/.github/workflows/release.yml@refs/tags/v0.3.0 \
  --certificate-oidc-issuer=https://token.actions.githubusercontent.com \
  --signature install.sh.sig \
  --certificate install.sh.cert \
  install.sh
```

## Deferred to WOR-302/303 (Trust tier tickets)

- **OSSF Scorecard** badge + action (WOR-302)
- **SLSA provenance** on releases (WOR-303)
- **SBOM** (CycloneDX) on releases (WOR-303)
- **Socket.dev** integration (WOR-303)

## Nice-to-have (separate sub-tickets)

- `shfmt` pre-commit hook (shell formatter)
- `dotenv-linter` on `.env.example` files
- Renovate bot for SHA-pinned GitHub Actions auto-bumps
- Secret scanning post-release (`truffleHog` on published artifacts)

## Rejected / not worth the noise

- Generic "curl uses TLS" callout — per HN consensus, pure theater
- Unused Snyk scans beyond Open Source monitor (WOR-305 trimmed this)
