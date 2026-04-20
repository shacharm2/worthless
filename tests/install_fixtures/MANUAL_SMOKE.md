# install.sh manual smoke checklist (WOR-235)

Filled out per release. Run the full list before pushing a new install.sh to `worthless.sh`.

See also [docs/install-security.md](../../docs/install-security.md) for the full release-blocking checklist and kill-switch runbook.

## Pre-flight

- [ ] CI green on `tests.yml`, `install-smoke.yml`, Docker integration job
- [ ] `UV_VERSION` / `ASTRAL_INSTALLER_SHA256` / `WORTHLESS_VERSION` triplet re-verified (see "Bumping UV_VERSION" below)
- [ ] `install.sh.sha256` published alongside `install.sh` at `worthless.sh/v<ver>/` *(planned — see docs/install-security.md "What install.sh does NOT verify today")*
- [ ] cosign / minisign detached signature published alongside SHA file *(planned — same)*
- [ ] Release-blocking checklist in docs/install-security.md signed off (Worker config diff, second-reviewer sign-off)
- [ ] Kill-switch rehearsal date within last 90 days (see "Kill-switch rehearsal" below)

## Personal Mac (current dev machine)

- [ ] Run `curl -sSL https://worthless.sh | sh`
  - Existing uv: re-run is idempotent (routes to `uv tool upgrade worthless`)
  - Existing pipx-installed worthless: warns with exit 30 + uninstall command
- [ ] Verify activation one-liner matches `$SHELL`
- [ ] After activation, `worthless --version` works
- [ ] Smoke-test `worthless doctor` (if shipped) reports clean

## Fresh DigitalOcean / Hetzner Ubuntu 24.04 droplet

- [ ] Provision smallest box; SSH in
- [ ] Confirm `python3` not installed: `command -v python3` returns nothing
- [ ] `curl -sSL https://worthless.sh | sh`
- [ ] Time-to-success: target <90s on a typical broadband link
- [ ] `source ~/.bashrc && worthless --version` succeeds

## Fresh macOS (clean user account)

- [ ] Create a new user via System Settings (no Homebrew, no Xcode tools)
- [ ] Open Terminal as that user
- [ ] `curl -sSL https://worthless.sh | sh`
- [ ] Verify Gatekeeper does NOT prompt on the uv binary
- [ ] Verify per-shell activation message matches `zsh` (default since Catalina)

## Behind a corporate proxy (squid simulation)

- [ ] `export HTTPS_PROXY=http://localhost:3128 HTTP_PROXY=http://localhost:3128`
- [ ] `curl -sSL https://worthless.sh | sh`
- [ ] If install fails, error message must surface `HTTPS_PROXY` / `UV_PYTHON_INSTALL_MIRROR` / `SSL_CERT_FILE` hints
- [ ] Re-run with `UV_PYTHON_INSTALL_MIRROR` set to internal mirror — must succeed

## WSL2 (Ubuntu under Windows 11)

- [ ] Inside WSL2 home (`~`), `curl -sSL https://worthless.sh | sh` succeeds
- [ ] Inside `/mnt/c/Users/<name>/`, install warns about path location but does not crash

## Negative path

- [ ] Running on Windows native (Git Bash / MINGW): exit 20 with link to `docs.worthless.sh/install/windows`
- [ ] Running on macOS 10.15 (Catalina, in a VM): exit 20 with version requirement message
- [ ] `astral.sh` simulated down (block via `/etc/hosts`): exit 10 with proxy / retry hint

## Bumping UV_VERSION

When pulling in a new uv release, the SHA pin in `install.sh` must be recomputed from the live Astral CDN — not copied from release notes.

- [ ] Set the target version: `NEW=0.11.8`  *(replace with actual)*
- [ ] Fetch + hash the Astral installer:
  ```
  curl -sSL "https://astral.sh/uv/${NEW}/install.sh" | sha256sum
  ```
- [ ] Edit `install.sh`: update `UV_VERSION` and `ASTRAL_INSTALLER_SHA256` together in one commit
- [ ] Re-run `pytest -m docker tests/test_install_docker.py` to confirm the bare-Ubuntu E2E still passes with the new pair
- [ ] Second maintainer independently recomputes the SHA and signs off on the commit

Never accept a SHA256 from a pull request without re-fetching yourself. A malicious PR that bumps both `UV_VERSION` and the SHA together to attacker-controlled values is exactly the attack this pin is supposed to prevent.

## Kill-switch rehearsal

Required at least every 90 days, and blocking for any release where the last rehearsal is older. Full runbook in [docs/install-security.md](../../docs/install-security.md#kill-switch-runbook).

- [ ] Break-glass maintainer (not the primary) logs into Cloudflare
- [ ] Deploys a 503-response Worker to the **staging** domain (not prod)
- [ ] Verifies `curl -sSL https://staging.worthless.sh` returns 503 with the "installer temporarily unavailable" message
- [ ] Reverts the staging Worker
- [ ] Records rehearsal date + maintainer initials in the release checklist

If the break-glass maintainer cannot complete the rehearsal (lost access, MFA broken, role revoked), the issue blocks the next `install.sh` release until resolved.

## Sign-off

- [ ] All boxes ticked
- [ ] Release tag pushed
- [ ] Worker config promoted from staging to prod
- [ ] Linear WOR-235 closed with PR + commit references
