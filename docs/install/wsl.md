# Install on WSL2 (Windows + Linux subsystem)

WSL2 is the **documented happy path for Windows developers**. worthless
works inside WSL2 like a native Linux install, with one important
caveat about where you run it.

## TL;DR

- Run `worthless` **inside your WSL home** (`~`), NOT inside `/mnt/c/...`
- Everything else is identical to [linux.md](./linux.md)

## 0. Prerequisites

- Windows 10 (build 19041+) or Windows 11
- WSL2 enabled with a Linux distro installed (Ubuntu-24.04 recommended)
- Inside WSL: a regular non-root user (this is the WSL default)

```bash
# Verify you're in WSL, not Windows
uname -srm
# Expected: Linux 5.x.x-microsoft-standard-WSL2 x86_64

# Confirm you're a non-root user
id -un
# Expected: NOT 'root' — should be your username
```

## 1. Install (inside WSL)

**Critical: open WSL via "wsl" in Start menu, NOT by clicking into a
Windows folder.** Your `pwd` should be `/home/<you>` or similar — NOT
`/mnt/c/...`.

```bash
cd ~                                    # always start from your WSL home
curl -sSL https://worthless.sh | sh
```

What happens:
1. Same as Linux install — `uv` + `worthless` to `~/.local/bin`
2. install.sh detects WSL via `/proc/version` containing "microsoft"
3. If it detects you're in `/mnt/*`, it warns you (but doesn't abort)

**The /mnt/c warning matters.** Read the next section.

## 1a. Why /mnt/c matters

Windows-mounted drives (`/mnt/c`, `/mnt/d`) speak NTFS via WSL's 9P
proxy. Python file I/O across that boundary is ~5-20x slower than
native ext4. uv's Python install + worthless's SQLite reads will be
SLOW from `/mnt/c`.

If install.sh prints:

```
WSL detected, running from a Windows-mounted path (/mnt/...).
Install will succeed but uv operations from /mnt/c are slow.
For best performance, install from your Linux home (~).
```

→ `cd ~` and re-run. Don't fight it.

## 2. Verify install

```bash
worthless --version
# worthless 0.3.3
```

## 3. First lock

Inside WSL, your projects can live two places:
- `~/projects/myapp` — fast (ext4) ✅
- `/mnt/c/Users/<you>/myapp` — slow (NTFS via 9P) ⚠️

Both work. We strongly recommend the WSL-home path.

```bash
cd ~/projects/myapp     # or wherever your .env is
cat .env
# OPENAI_API_KEY=<your-real-openai-key-here>

worthless
```

WSL2 typically does NOT have a session bus by default → worthless uses
the **file-backed keystore** at `~/.worthless/.fernet-key` (mode 0600).
**No popups.** No Windows Credential Manager involvement.

`.env` after lock:

```diff
- OPENAI_API_KEY=<your-real-openai-key-here>
+ OPENAI_API_KEY=<decoy-prefix>...
+ OPENAI_BASE_URL=http://127.0.0.1:8787/openai-<alias>/v1
```

## 4. Point your app at the proxy

Your WSL-side app: same as Linux, dotenv picks it up.

**Special case — Windows-side tools accessing WSL services:**

If you have Windows-side tools (a Windows IDE plugin, a Windows
browser tab) that need to reach the proxy, WSL2 forwards `localhost`
both ways. Your Windows tool can hit `http://localhost:8787/...` and
WSL2 routes it to the WSL-side proxy automatically.

If you have a Windows-side tool that needs the URL by IP (not
localhost), get the WSL2 IP:

```bash
hostname -I | awk '{print $1}'
```

Use that IP from Windows-side tools.

## 5. Verify

```bash
curl -s "http://127.0.0.1:8787/openai-<alias>/v1/models" \
  -H "Authorization: Bearer $(grep OPENAI_API_KEY .env | cut -d= -f2)"
```

Expected: JSON list of models.

## 6. Daily use

| You do | What survives | What you do |
|---|---|---|
| Close WSL terminal | Proxy keeps running (WSL2 keeps the distro alive) | Nothing |
| `wsl --shutdown` from Windows | **Proxy is gone** + WSL state cleared | `worthless up` next time you start WSL |
| Reboot Windows | Proxy is gone | `worthless up` |
| Sleep / wake Windows | WSL usually survives — check `worthless status` | Maybe `worthless up` |

**WSL2 idles aggressively.** If you don't use WSL for a few minutes
and Windows decides to suspend it, the proxy's process state is
suspended too — usually transparent, but if you see hangs, check
that the WSL distro is still running with `wsl -l -v` from
PowerShell.

## 7. Uninstall (manual, until WOR-435 ships)

```bash
worthless down
uv tool uninstall worthless
rm -rf ~/.worthless
```

(WSL doesn't use Secret Service by default — no `secret-tool clear`
needed.)

## Common failures specific to WSL

| Symptom | Cause | Fix |
|---|---|---|
| Install runs but is unreasonably slow (~minutes) | You're in `/mnt/c/...` | `cd ~` and re-run |
| `worthless` works in WSL but Windows app can't reach `127.0.0.1:8787` | WSL2 should auto-forward, but firewalls/VPNs sometimes break it | Get WSL IP via `hostname -I` and use that explicitly |
| `worthless lock` writes `127.0.0.1:8787` but a *Docker container running INSIDE WSL* can't reach it | Container's loopback ≠ WSL's loopback | See [docker.md](./docker.md) |
| `wsl --shutdown` then proxy is gone | Expected — WSL state was cleared | `worthless up` after restarting WSL |
| GitHub credential helper conflicts | WSL's git might use Windows-side credential helper | Unrelated to worthless; configure git separately |

## What worthless does NOT defend against on WSL

- Windows-side compromise. If your Windows host is owned, it can read
  WSL filesystem at the kernel level. worthless can't help.
- WSL1 (legacy). worthless requires WSL2 (the kernel-based one). WSL1
  doesn't expose enough Linux primitives.
- Antivirus interference. Some Windows AV software scans `/mnt/c`
  paths and can interfere with file I/O. If you see weird errors
  doing key writes, check your AV exclusions.

## Why WSL is the recommended Windows path

worthless does not run natively on Windows. Native Windows requires
Win32 APIs (`DPAPI` for the keystore equivalent of macOS Keychain) and
a Windows-shaped install (`%APPDATA%`, `winget`, etc.) — none of which
are built today. The `Smoke (windows, py3.13)` CI check verifies that
on native Windows, install.sh exits with code 20 + a doc link to this
guide.

So if you're on Windows: install WSL2 first, then follow this guide.
