# install.sh manual smoke checklist (WOR-235)

Filled out per release. Run the full list before pushing a new install.sh
to `worthless.sh`.

For supply-chain trust roots, see
[docs/install-security.md](../../docs/install-security.md).

## Pre-flight

- [ ] CI green on `tests.yml`, `install-smoke.yml`, and the Docker integration job for the tagged commit
- [ ] `UV_VERSION` / `ASTRAL_INSTALLER_SHA256` / `WORTHLESS_VERSION` triplet re-verified (see "Bumping UV_VERSION" below)

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

When pulling in a new uv release, the SHA pin in `install.sh` must be
recomputed from the live Astral CDN — not copied from release notes.

- [ ] Set the target version: `NEW=0.11.8`  *(replace with actual)*
- [ ] Fetch + hash the Astral installer:
  ```
  curl -sSL "https://astral.sh/uv/${NEW}/install.sh" | sha256sum
  ```
- [ ] Edit `install.sh`: update `UV_VERSION` and `ASTRAL_INSTALLER_SHA256` together in one commit
- [ ] Edit `tests/install_fixtures/Dockerfile.ubuntu-with-uv`: bump the pinned
      `https://astral.sh/uv/<VERSION>/install.sh` URL to match the new `UV_VERSION`.
      Without this lockstep bump, the `ubuntu-with-uv` fixture pre-installs
      a version that no longer matches install.sh's pin, install.sh re-installs,
      and `verify_uv_reuse.sh` fails because the uv hash changed.
- [ ] Re-run `pytest -m docker tests/test_install_docker.py` to confirm the bare-Ubuntu E2E still passes with the new pair

Never accept a SHA256 from a pull request without re-fetching yourself.
A malicious PR that bumps both `UV_VERSION` and the SHA together to
attacker-controlled values is exactly the attack this pin is supposed
to prevent.

## Sign-off

- [ ] All boxes ticked
- [ ] Release tag pushed
- [ ] Linear WOR-235 closed with PR + commit references
