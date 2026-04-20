# Install Security

This document covers the supply-chain model for `curl -sSL https://worthless.sh | sh`:

- **Trust roots** — which hosts the installer talks to and what it verifies
- **Release-blocking checklist** — what a release operator must confirm before promoting `install.sh` to `worthless.sh`
- **Kill-switch runbook** — how to yank a compromised installer and notify users

For general vulnerability reporting, see [SECURITY.md](../SECURITY.md).
For the cryptographic architecture, see [SECURITY_POSTURE.md](../SECURITY_POSTURE.md).

---

## Trust roots

Running `curl -sSL https://worthless.sh | sh` depends on four external parties. Each one that can forge TLS or modify the payload can own your box.

| Host | What it serves | How `install.sh` verifies it |
|---|---|---|
| `worthless.sh` (Cloudflare) | `install.sh` itself | TLS only. The user's `curl` trusts the system CA bundle and Cloudflare's cert. |
| `astral.sh` | `uv` installer script | `install.sh` pins `ASTRAL_INSTALLER_SHA256` for a specific `UV_VERSION`. Mismatch → exit `EXIT_NETWORK=10`. |
| Astral release server | `uv` binary + managed Python | `uv`'s own signature verification (out of this installer's scope). |
| `pypi.org` | `worthless` package | `uv` uses HTTPS + hash-locked resolution. |

### What this means

- **Compromised Cloudflare or worthless.sh DNS → game over.** A malicious `install.sh` served from the canonical URL has the user's trust. The only in-script mitigation is the Astral SHA pin, which prevents an attacker from substituting the uv installer underneath a legit `install.sh`.
- **Compromised Astral → the uv pin catches it** *only if* the attacker doesn't also control `worthless.sh` at the same time (because a compromised `install.sh` could simply rewrite the pin).
- **MITM on the user's connection → TLS catches it.**

### What `install.sh` does NOT verify today

- There is no detached signature (cosign / minisign) for `install.sh` itself.
- There is no `install.sh.sha256` manifest published alongside it on `worthless.sh`.

Both are planned — until shipped, users who want stronger guarantees should download `install.sh`, inspect it, and run it locally rather than piping it to `sh`.

---

## Release-blocking checklist

Before promoting `install.sh` to `https://worthless.sh`, the release operator must sign off on every item:

- [ ] CI green on `tests.yml`, `install-smoke.yml`, and the Docker integration job for the tagged commit
- [ ] Manual smoke checklist (`tests/install_fixtures/MANUAL_SMOKE.md`) completed end-to-end
- [ ] `UV_VERSION` and `ASTRAL_INSTALLER_SHA256` in `install.sh` match the uv release the team intends to pin (re-verified with `curl -sSL https://astral.sh/uv/$UV_VERSION/install.sh | sha256sum`)
- [ ] `WORTHLESS_VERSION` in `install.sh` matches the package version published to PyPI
- [ ] Cloudflare Worker config reviewed against last approved baseline (diff reviewed by a second pair of eyes)
- [ ] Kill-switch break-glass path rehearsed within the last 90 days (see below)
- [ ] A second maintainer has independently verified the SHA pin and version triplet

The point of the second-reviewer line is to make a single compromised maintainer credential insufficient for a malicious release.

---

## Kill-switch runbook

If `install.sh` is discovered to be compromised — whether by audit finding, bug report, or CDN misbehavior — the goal is to stop it from being served within 15 minutes.

### Signals that should trigger this runbook

- A maintainer's Cloudflare account shows unexplained Worker edits or KV writes
- A user reports an installer output that does not match the known-good `install.sh`
- The pinned `ASTRAL_INSTALLER_SHA256` is reported as no longer matching `astral.sh/uv/...`
- Security researcher report claims to have modified the served installer

### Immediate actions (do these in order)

1. **Yank.** Update the Cloudflare Worker to respond with HTTP 503 and a short message pointing users to the GitHub release page. This is faster than redeploying a "fixed" `install.sh`.
2. **Invalidate.** Purge the Cloudflare cache for `worthless.sh/*`.
3. **Revoke.** Rotate the Cloudflare API tokens, Cloudflare dashboard password/MFA, and any deploy keys that could publish to `worthless.sh`.
4. **Announce.** Post to the repository's GitHub Security Advisories, the README banner, and any social channels. State the window during which the bad installer could have been served. Advise every user who installed in that window to `worthless revoke` and reinstall from a known-good source.
5. **Diff.** Compare the known-good `install.sh` against what was served. If you cannot reconstruct what was served (no logs), assume worst case.
6. **Post-mortem.** Within 14 days, write up what happened and what controls are added.

### Break-glass access

The Cloudflare Worker must be deployable by a second maintainer without the primary's credentials. That means:

- A non-primary maintainer has Cloudflare role sufficient to edit Worker code and purge cache
- That maintainer has rehearsed the yank step on a staging Worker within the last 90 days
- The rehearsal date is recorded in the release-blocking checklist above

If the break-glass path is stale, the `install.sh` release is blocked until it is rehearsed and re-recorded.
