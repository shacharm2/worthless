# Install Security

Supply-chain model for `curl -sSL https://worthless.sh | sh`: which hosts
the installer talks to, and what it verifies today.

For general vulnerability reporting, see [SECURITY.md](../SECURITY.md).
For the cryptographic architecture, see [security.md](security.md).

## Trust roots

Running `curl -sSL https://worthless.sh | sh` depends on four external
parties. Anyone who can forge TLS or modify the payload can own your box.

| Host | What it serves | How `install.sh` verifies it |
|---|---|---|
| `worthless.sh` (Cloudflare) | `install.sh` itself | TLS only — the user's `curl` trusts the system CA bundle and Cloudflare's cert. |
| `astral.sh` | `uv` installer script | `install.sh` pins `ASTRAL_INSTALLER_SHA256` for a specific `UV_VERSION`. Mismatch → exit `EXIT_NETWORK=10`. |
| Astral release server | `uv` binary + managed Python | `uv`'s own signature verification (out of this installer's scope). |
| `pypi.org` | `worthless` package | `uv` uses HTTPS + hash-locked resolution. |

### What this means

- **Compromised Cloudflare or `worthless.sh` DNS → game over.** A malicious `install.sh` served from the canonical URL has the user's trust. The only in-script mitigation is the Astral SHA pin, which stops a substituted uv installer underneath a legit `install.sh`.
- **Compromised Astral → the uv pin catches it** *only if* the attacker doesn't also control `worthless.sh` (a compromised `install.sh` could simply rewrite the pin).
- **MITM on the user's connection → TLS catches it.**

## What `install.sh` does NOT verify today

- No detached signature (cosign / minisign) for `install.sh` itself
- No `install.sh.sha256` manifest published alongside it
- No second-reviewer gate on releases (solo maintainer)
- No kill-switch: if `install.sh` is ever discovered to be compromised, there is no pre-wired mechanism to serve a 503 from `worthless.sh` — the only recourse today is revoking Cloudflare credentials and pulling the asset manually

Until those land, users who want stronger guarantees should download
`install.sh`, inspect it, and run it locally rather than piping to `sh`:

```bash
curl -sSL https://worthless.sh -o install.sh
less install.sh    # inspect
sh install.sh
```

Planned hardening is tracked in Linear under [WOR-257](https://linear.app/plumbusai/issue/WOR-257). This file describes what's real today; controls move in here when they ship.
