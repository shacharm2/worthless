---
title: "Install — Linux"
description: "Native install on Ubuntu / Debian / Alpine, ~60 seconds."
---

# Install on Linux

Zero to working proxy in ~1-2 minutes on a fast network. Tested on
Ubuntu 22.04 / 24.04, Debian 12, and Alpine 3.20. Other glibc-based distros likely work but
aren't part of the install matrix.

## 0. Prerequisites

```bash
# Check distro + glibc
uname -a; ldd --version 2>&1 | head -1

# Check curl + tar (install if missing)
command -v curl tar
```

You do NOT need: Python pre-installed, pyenv, sudo (for non-root
install), Docker.

If you're missing curl/tar:

```bash
# Debian/Ubuntu
sudo apt-get update && sudo apt-get install -y curl tar ca-certificates

# Alpine (busybox tar can't unzstd — install GNU tar + zstd)
sudo apk add --no-cache bash ca-certificates curl tar zstd
```

## 1. Install

```bash
curl -sSL https://worthless.sh | sh
```

The installer drops `uv` and `worthless` into `~/.local/bin/`. **No
password prompts.** install.sh runs entirely in `$HOME` — it does not
need sudo and won't ask for it.

If you see `your shell may need ~/.local/bin on PATH`, add the
suggested line to your shell rc:

```bash
# bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc

# zsh
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc

# fish
echo 'set -gx PATH "$HOME/.local/bin" $PATH' >> ~/.config/fish/config.fish
```

Then `source` it or open a new terminal.

## 2. Verify install

```bash
worthless --version
```

Expected:

```
worthless 0.3.3
```

## 3. First lock

Linux uses **Secret Service** (GNOME Keyring / KWallet / kwallet5)
where available. On servers without a session bus, worthless falls
back to a file-backed keystore in `~/.worthless/`.

```bash
cd /path/to/your/project
cat .env
# OPENAI_API_KEY=<your-real-openai-key-here>

worthless
```

- On a desktop with GNOME / KDE: a credential prompt may appear once
  (varies by desktop env). Grant access permanently.
- On a server with no DBus session: **no prompt** — worthless uses the
  file-backed fallback at `~/.worthless/.fernet-key` (mode 0600).

`.env` is rewritten (see [README — what `worthless lock` does](./README.md#what-worthless-lock-does-to-your-env)) and the proxy spawns on `127.0.0.1:8787`.

## 4. Point your app at the proxy

Your app reads `.env` via dotenv / direnv / your framework — no code
change. SDK picks up `OPENAI_BASE_URL` automatically.

For systemd-managed services that DON'T inherit `.env`, add the URL
to your service unit's `Environment=` directive:

```ini
[Service]
EnvironmentFile=/path/to/your/project/.env
```

## 5. Verify

See [README — Verify it works](./README.md#verify-it-works) for the
SDK snippet. Same on every platform.

## 6. Daily use

| You do | What survives | What you do |
|---|---|---|
| Close terminal | Proxy keeps running | Nothing |
| `worthless down` | Proxy stops | `worthless up` |
| Reboot machine | **Proxy is gone** | `worthless up` |
| Logout / login | Depends on your session manager — usually proxy dies | `worthless up` |

### Why no auto-start? (the reboot gap)

WOR-175 (Linux systemd user service) ships
the install/uninstall commands in v1.1. Until then, write your own
systemd user unit. Copy-paste this into
`~/.config/systemd/user/worthless-proxy.service` (replace
`/home/youruser` with your actual `$HOME`):

```ini
[Unit]
Description=Worthless local proxy
After=network.target

[Service]
ExecStart=/home/youruser/.local/bin/worthless up
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=default.target
```

Then enable it:

```bash
systemctl --user daemon-reload
systemctl --user enable --now worthless-proxy.service
loginctl enable-linger "$USER"   # so the unit survives logout
```

If you don't want a service file (e.g. ephemeral dev machine), the
fallback is the same shell-rc opt-in pattern — but be aware silently
auto-spawning daemons from your `.bashrc` is a tradeoff worth knowing
about, not the recommended path.

## 7. Uninstall (manual, until WOR-435 ships)

```bash
worthless down
uv tool uninstall worthless

# Purge Secret Service entries (if used)
secret-tool clear service worthless 2>/dev/null

# Wipe local state (file-backed keystore + DB)
rm -rf ~/.worthless
```

## Common failures

| Symptom | Cause | Fix |
|---|---|---|
| "command not found: worthless" | `~/.local/bin` not on PATH | Add to shell rc per §1 |
| Alpine: `uv` install fails on `tar -xzst` | busybox tar can't unzstd | `apk add tar zstd` |
| `Failed to install worthless==X.Y.Z` (network) | curl/uv blocked | Set `HTTPS_PROXY=...` and re-run |
| Proxy starts but health check fails | Port 8787 in use | `lsof -i :8787` to find the squatter |
| Server with no session bus, install hangs at keychain step | `keyring` waiting for unavailable service | This shouldn't happen; if it does, file an issue with `keyring --list-backends` output |
| App in container can't reach `127.0.0.1:8787` | Container loopback ≠ host | See [docker.md](./docker.md) |

## What worthless does NOT defend against on Linux

- A compromised user account. Same as macOS — if attacker has user
  shell access, they can read shard A + the file-backed keystore +
  query the proxy.
- Containerized apps without proper networking — see
  [docker.md](./docker.md).
- Multi-user systems. `~/.worthless/` is mode 0700 but other admins
  on the box can still read it. worthless is a per-user tool.
