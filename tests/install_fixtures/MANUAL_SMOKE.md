# install.sh manual smoke checklist (WOR-235)

Filled out per release. Run the full list before pushing a new install.sh to `worthless.sh`.

## Pre-flight

- [ ] CI green on `tests.yml`, `install-smoke.yml`, Docker integration job
- [ ] `install.sh.sha256` published alongside `install.sh` at `worthless.sh/v<ver>/`
- [ ] cosign / minisign detached signature published alongside SHA file
- [ ] SECURITY.md release-blocking checklist (Worker config audit) signed off
- [ ] Kill-switch break-glass deploy path verified accessible without normal maintainer creds

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

## Sign-off

- [ ] All boxes ticked
- [ ] Release tag pushed
- [ ] Worker config promoted from staging to prod
- [ ] Linear WOR-235 closed with PR + commit references
