# Installer & Package Signing with Cosign/Sigstore

> "Trust, but verify" — and give users the tools to verify.

Research for signing the worthless installer script and PyPI package using
Sigstore's keyless signing. Goal: minimum viable signing that a solo dev can
maintain, that actually protects users from tampered artifacts.

---

## 1. Signing the Installer Script (install.sh)

### How Cosign Keyless Signing Works

Cosign keyless signing (aka "identity-based signing") uses Sigstore's Fulcio CA
and Rekor transparency log. No long-lived keys to manage. The signer proves
their identity via OIDC (GitHub Actions' OIDC token), and Fulcio issues a
short-lived certificate. The signature + certificate + transparency log entry
together form the verification chain.

### What Gets Produced

For a blob (install.sh), cosign produces:
- `install.sh.sig` — the detached signature (base64-encoded)
- `install.sh.pem` — the Fulcio certificate (short-lived, but the Rekor log
  entry makes it permanently verifiable)

### Signing Command (in CI)

```bash
cosign sign-blob install.sh \
  --yes \
  --output-signature install.sh.sig \
  --output-certificate install.sh.pem
```

In GitHub Actions with `id-token: write` permission, cosign automatically uses
the workflow's OIDC token. No keys, no passwords, no secrets to configure.

### User Verification Command

```bash
# Download the script and its signature
curl -sSL https://worthless.sh/install.sh -o install.sh
curl -sSL https://worthless.sh/install.sh.sig -o install.sh.sig
curl -sSL https://worthless.sh/install.sh.pem -o install.sh.pem

# Verify: proves this was signed in our GitHub Actions workflow
cosign verify-blob install.sh \
  --signature install.sh.sig \
  --certificate install.sh.pem \
  --certificate-identity "https://github.com/shacharm2/worthless/.github/workflows/release.yml@refs/tags/*" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com"
```

The `--certificate-identity` pins verification to our specific workflow file.
The `--certificate-oidc-issuer` pins it to GitHub Actions OIDC. Together, they
prove the script was signed by a GitHub Actions run of our release workflow, not
by some random person with a cosign key.

### For Users Without Cosign

Most users will `curl | sh` without verifying. That is fine. The signing exists
for security-conscious users and for incident response (proving a specific
version was the one we shipped). We should document verification in the README
and display a SHA256 checksum prominently for the "I don't want to install
cosign" crowd:

```bash
# Quick integrity check (no cosign needed)
echo "abc123...expected-hash  install.sh" | sha256sum -c
```

---

## 2. PyPI Package Signing

### PyPI Trusted Publishing (OIDC) — What It Does

PyPI Trusted Publishing lets you publish from GitHub Actions without storing
API tokens. You configure a "trusted publisher" on PyPI that maps your GitHub
repo + workflow + environment to your PyPI project. The workflow gets a
short-lived OIDC token that PyPI accepts directly.

**What it does NOT do:** Trusted Publishing authenticates the *upload*. It does
not sign the package artifact itself. It proves "this upload came from this
GitHub repo's CI," but the `.whl`/`.tar.gz` files themselves are unsigned.

### PEP 740 — Digital Attestations (The New Hotness)

PEP 740 (accepted, live on PyPI since late 2024) adds *package attestations* —
Sigstore-based signatures attached to each uploaded file. This is the real
signing story for PyPI.

**How it works:**
- `pypi-attestations` (part of the `pypa/gh-action-pypi-publish` action) signs
  each dist file with the workflow's OIDC identity via Sigstore
- The attestation is uploaded alongside the package to PyPI
- PyPI stores and displays attestation status on the package page
- Users can verify with `python -m pypi_attestations verify`

**Since `pypa/gh-action-pypi-publish@v1.12+`**, attestation generation is
**enabled by default** when you use trusted publishing. You get it for free.

### What You Actually Need to Do

1. Configure Trusted Publishing on PyPI (one-time setup)
2. Use `pypa/gh-action-pypi-publish` in your release workflow
3. Attestations are generated and uploaded automatically

That's it. No additional sigstore integration needed for PyPI.

### Verification by Users

```bash
# Install the verification tool
pip install pypi-attestations

# Verify a downloaded package
python -m pypi_attestations verify worthless-1.0.0.tar.gz \
  --identity "https://github.com/shacharm2/worthless/.github/workflows/release.yml@refs/tags/*" \
  --issuer "https://token.actions.githubusercontent.com"
```

Or users can check the PyPI web UI, which now shows attestation status with a
green checkmark.

---

## 3. GitHub Actions Release Workflow

```yaml
name: Release

on:
  push:
    tags:
      - "v*"

permissions:
  contents: write      # upload release assets
  id-token: write      # OIDC for cosign + PyPI trusted publishing

jobs:
  build:
    name: Build distribution
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6
        with:
          persist-credentials: false

      - uses: actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405 # v6
        with:
          python-version: "3.13"

      - name: Install build tools
        run: python -m pip install --disable-pip-version-check build

      - name: Build sdist and wheel
        run: python -m build

      - name: Upload dists
        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4
        with:
          name: dist
          path: dist/

  sign-installer:
    name: Sign installer script
    runs-on: ubuntu-latest
    needs: [build]
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6
        with:
          persist-credentials: false

      - name: Install cosign
        uses: sigstore/cosign-installer@dc72c7d5c4d10cd6bcb8cf6e3fd625a9e5e537da # v3

      - name: Sign install.sh
        run: |
          cosign sign-blob install.sh \
            --yes \
            --output-signature install.sh.sig \
            --output-certificate install.sh.pem

      - name: Generate SHA256
        run: sha256sum install.sh > install.sh.sha256

      - name: Upload signature artifacts
        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4
        with:
          name: installer-signatures
          path: |
            install.sh
            install.sh.sig
            install.sh.pem
            install.sh.sha256

  publish-pypi:
    name: Publish to PyPI
    runs-on: ubuntu-latest
    needs: [build]
    environment: pypi  # must match the trusted publisher config on PyPI
    steps:
      - name: Download dists
        uses: actions/download-artifact@95815c38cf2ff2164869cbab79da8d1f422bc89e # v4
        with:
          name: dist
          path: dist/

      # Attestations are generated automatically by this action
      # when using trusted publishing (OIDC). No extra config needed.
      - name: Publish to PyPI
        uses: pypa/gh-action-pypi-publish@76f52bc884231f62b54f72e44af12044e0e68f2a # v1.12
        with:
          attestations: true

  github-release:
    name: Create GitHub Release
    runs-on: ubuntu-latest
    needs: [build, sign-installer, publish-pypi]
    steps:
      - name: Download all artifacts
        uses: actions/download-artifact@95815c38cf2ff2164869cbab79da8d1f422bc89e # v4

      - name: Create release
        uses: softprops/action-gh-release@c95fe1489396fe8a9eb87c0abf8aa5b2ef267fda # v2
        with:
          files: |
            dist/*
            installer-signatures/install.sh
            installer-signatures/install.sh.sig
            installer-signatures/install.sh.pem
            installer-signatures/install.sh.sha256
          generate_release_notes: true
          body: |
            ## Verify installer signature

            ```bash
            cosign verify-blob install.sh \
              --signature install.sh.sig \
              --certificate install.sh.pem \
              --certificate-identity "https://github.com/shacharm2/worthless/.github/workflows/release.yml@refs/tags/${{ github.ref_name }}" \
              --certificate-oidc-issuer "https://token.actions.githubusercontent.com"
            ```

            ## Quick integrity check

            ```bash
            sha256sum -c install.sh.sha256
            ```
```

### PyPI Trusted Publisher Setup (One-Time)

1. Go to https://pypi.org/manage/project/worthless/settings/publishing/
2. Add a new trusted publisher:
   - **Owner:** `shacharm2`
   - **Repository:** `worthless`
   - **Workflow:** `release.yml`
   - **Environment:** `pypi`
3. Done. No API tokens needed.

---

## 4. Verification Instructions for Users

### Full Verification (installer)

```bash
# 1. Download everything
curl -sSL https://worthless.sh/install.sh -o install.sh
curl -sSL https://worthless.sh/install.sh.sig -o install.sh.sig
curl -sSL https://worthless.sh/install.sh.pem -o install.sh.pem

# 2. Verify with cosign
cosign verify-blob install.sh \
  --signature install.sh.sig \
  --certificate install.sh.pem \
  --certificate-identity-regexp "https://github.com/shacharm2/worthless/.*" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com"

# 3. If verification passes, run it
bash install.sh
```

### Quick Verification (no cosign)

```bash
# Compare SHA256 against the value shown on the GitHub release page
curl -sSL https://worthless.sh/install.sh | sha256sum
```

### PyPI Package Verification

```bash
pip download worthless --no-deps
python -m pypi_attestations verify worthless-*.tar.gz \
  --identity "https://github.com/shacharm2/worthless/.github/workflows/release.yml@refs/tags/*" \
  --issuer "https://token.actions.githubusercontent.com"
```

Or just check the PyPI project page — attested packages show a provenance badge.

---

## 5. DNS/Domain Considerations for worthless.sh

### DNSSEC

**Enable DNSSEC on worthless.sh.** This prevents DNS spoofing that could
redirect users to a malicious install script. Most registrars support this with
a toggle. For `.sh` TLD, the registry supports DNSSEC.

Steps:
1. Enable DNSSEC at your registrar (or DNS provider if they manage the zone)
2. The registrar submits DS records to the `.sh` registry
3. Verify with: `dig +dnssec worthless.sh`

### CAA Records

CAA records restrict which CAs can issue certificates for your domain. Add:

```
worthless.sh.  IN CAA 0 issue "letsencrypt.org"
worthless.sh.  IN CAA 0 issuewild "letsencrypt.org"
worthless.sh.  IN CAA 0 iodef "mailto:security@worthless.sh"
```

Adjust the CA to match whoever issues your TLS cert (Let's Encrypt if using
Cloudflare/Vercel, or the specific provider).

### HTTPS-Only

The install URL MUST be HTTPS. The install script landing page should redirect
HTTP to HTTPS. If hosting on Cloudflare/Vercel/Netlify, this is automatic.

### Hosting the Signatures

The simplest approach: host `install.sh`, `install.sh.sig`, `install.sh.pem`,
and `install.sh.sha256` as static files on the same domain. Options:

1. **GitHub Pages** — serve from a `gh-pages` branch or `/docs` folder
2. **Cloudflare Pages** — deploy static files, free tier is plenty
3. **S3 + CloudFront** — if you want CDN distribution

The release workflow can push these files to whichever hosting you choose. A
simple approach is a post-release workflow that copies the artifacts to the
hosting location.

---

## 6. Minimum Viable Signing Setup (Solo Dev v1)

### Phase 1: Ship Now (1-2 hours)

1. Set up PyPI Trusted Publishing (10 min, web UI only)
2. Add the release workflow above to `.github/workflows/release.yml`
3. Write `install.sh` and commit it to the repo root
4. Push a tag, let CI handle everything

This gives you:
- Signed PyPI attestations (PEP 740, automatic)
- Signed installer with cosign keyless
- SHA256 checksum for quick verification
- GitHub Release with all artifacts

### Phase 2: Polish Later

- Enable DNSSEC on worthless.sh
- Add CAA records
- Deploy signature files to worthless.sh alongside install.sh
- Add verification instructions to README
- Add a `worthless verify-installer` CLI command that wraps the cosign call

### What NOT to Do

- **Don't generate and manage GPG keys.** Sigstore keyless is strictly better
  for a solo dev — no key management, no revocation headaches, no "I lost the
  key" disasters.
- **Don't sign with a stored cosign key.** Keyless is the point. A stored key
  is another secret to protect.
- **Don't roll your own transparency log.** Rekor is free and public.
- **Don't block install on verification.** Offer it, document it, but the
  default `curl | sh` path should still work. Security-conscious users will
  verify; others won't, and that's their choice.

---

## Summary

| Artifact | Signing Method | Automatic? | User Verification |
|----------|---------------|------------|-------------------|
| PyPI package | PEP 740 attestations via `gh-action-pypi-publish` | Yes (with trusted publishing) | PyPI badge or `pypi_attestations verify` |
| install.sh | Cosign keyless blob signing | Yes (in release workflow) | `cosign verify-blob` with identity pinning |
| GitHub Release | GitHub's built-in provenance | Yes | GitHub UI shows "verified" |

Total ongoing maintenance burden: **zero**. Everything is keyless and runs in
CI. No secrets to rotate, no keys to protect.
