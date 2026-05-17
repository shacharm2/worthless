# Cross-Platform Shard Storage: macOS Keychain & Windows Credential Manager

Research date: 2026-04-04
Context: Worthless stores Shard B (~50 bytes) in the OS credential store. Must be headless after enrollment.

---

## 1. macOS Keychain

### 1.1 When does macOS show "allow/deny" dialogs?

macOS shows keychain access prompts based on **Access Control Lists (ACLs)** attached to each keychain item. The dialog appears when:

- The requesting binary is **not listed** in the item's ACL (partition list)
- The requesting binary's **code signature has changed** since it was authorized
- The keychain is **locked** (e.g., after reboot, before first login)

The check is **per-binary per-item**. If binary `/usr/bin/security` creates an item, that binary is automatically in the ACL. A different binary reading the same item triggers a prompt -- unless the item was created with `-A` (allow all apps).

**Key insight:** The authorization is tracked by the **binary's path and code signature hash**. If the binary moves or its signature changes, authorization is invalidated.

### 1.2 Pre-authorizing access at enrollment time

Yes. Two approaches:

**Option A: `-A` flag (allow all applications)**
```bash
security add-generic-password \
  -a "worthless-shard-b" \
  -s "worthless" \
  -w "<base64-shard>" \
  -A \
  -U
```
The `-A` flag sets the ACL to "any application may access without warning." This is what we want. Apple marks it "insecure, not recommended" because any local process can read it -- but our shard is useless alone (2-of-3 sharing), so this is acceptable.

**Option B: `-T /path/to/binary` flag (specific app)**
```bash
security add-generic-password \
  -a "worthless-shard-b" \
  -s "worthless" \
  -w "<base64-shard>" \
  -T /usr/bin/security \
  -T /path/to/python3 \
  -U
```
The `-T` flag adds specific binaries to the ACL. Problem: if the Python binary path changes (virtualenv recreated, `uv` creates new env), the ACL entry becomes stale and prompts reappear.

**Verdict: Use `-A`.** The shard is worthless alone. The `-T` approach is fragile with Python/virtualenv tooling.

### 1.3 What happens when the binary path changes?

When using `-T` (specific app authorization):
- macOS tracks the binary by **path + code directory hash** (CDHash for signed binaries, or just path for unsigned)
- If you recreate a virtualenv, the Python binary at the new path is a **different entry** in the ACL
- Result: **prompt reappears** asking the user to authorize the new binary
- This is the root cause of [keyring issue #619](https://github.com/jaraco/keyring/issues/619) -- "Multiple Password Popups on macOS for Multiple Credentials When the Python Binary Updates"

When using `-A`:
- No binary-specific ACL entries exist
- Path changes are irrelevant
- **No prompts ever appear** (for any app, from any path)

### 1.4 Non-interactive shell access (launchd, SSH, agents)

`security find-generic-password -w` works from non-interactive shells **if**:
1. The login keychain is **unlocked** (it unlocks automatically at GUI login, stays unlocked for the session)
2. The item's ACL permits access (use `-A` at creation)

**Critical edge case:** If the user logs in via SSH only (no GUI login), the login keychain may be **locked**. Solutions:
- `security unlock-keychain -p <password> ~/Library/Keychains/login.keychain-db` (requires storing the keychain password somewhere -- chicken-and-egg)
- Use `security create-keychain` to create a **custom keychain** with a known password, unlocked programmatically at startup
- For launchd agents (run at GUI login): keychain is unlocked, no issue
- For launchd daemons (run at boot, no user session): keychain is **not available** -- use a custom keychain or fall back to file-based storage

**Practical answer for Worthless:** Our target users are developers running Claude Code / Cursor on their Mac. They have a GUI session. The login keychain is unlocked. `security find-generic-password -w` will work silently with `-A` items.

### 1.5 Unsigned binary behavior

**TCC (Transparency, Consent, Control):** Does NOT gate keychain access. TCC protects Camera, Microphone, Contacts, Desktop/Documents folders, etc. Keychain has its own ACL system, separate from TCC.

**Gatekeeper:** Only applies to **launching** applications (quarantine check on first run). Does not affect keychain API access at runtime. A pip-installed binary that has already been launched (or was never quarantined because it was downloaded via `pip`/`curl` rather than a browser) faces no Gatekeeper restrictions on keychain access.

**SIP (System Integrity Protection):** Protects system directories. Irrelevant for user keychain access.

**Code signing and keychain:** The ACL system tracks signed binaries by CDHash and unsigned binaries by path. With `-A`, signing status is irrelevant -- all apps are permitted.

**Python `keyring` library behavior:** Per [issue #457](https://github.com/jaraco/keyring/issues/457), "secrets are accessible without a password prompt to any Python script (even if you've set them in a different venv)." This confirms that the macOS keychain backend (`SecItemCopyMatching` via pyobjc) works without prompts for items created by any Python process, because macOS treats all Python binaries as the "same application" when the ACL is permissive.

**Bottom line:** Unsigned binaries from `pip install` can read keychain items without restriction, provided the ACL permits it.

### 1.6 API options comparison

| Approach | Popup-free? | Virtualenv-safe? | Complexity | Notes |
|----------|-------------|-------------------|------------|-------|
| `security` CLI (subprocess) | Yes with `-A` | Yes (reads via `/usr/bin/security`) | Low | Most reliable. `/usr/bin/security` is Apple-signed, path-stable. |
| `keyring` Python lib | Yes with `-A` items | Mostly (see #619) | Medium | Uses `SecItemCopyMatching` via pyobjc. May trigger prompts if item was created with `-T` pointing to different Python binary. |
| Rust `security-framework` crate | Yes with `-A` | N/A (static binary) | Medium | Good for the Reconstruction service (Rust). Binary path is stable. |

**Recommendation: Use `security` CLI via subprocess.** The `/usr/bin/security` binary is always at the same path, always Apple-signed, and always in the ACL for items it creates. It sidesteps all Python binary path issues.

### 1.7 Secure Enclave reality check

**What the Secure Enclave actually protects:**

Per [Apple's Keychain Data Protection docs](https://support.apple.com/guide/security/keychain-data-protection-secb0694df1a/web):

- Keychain items are encrypted with **AES-256-GCM**
- The **metadata key** (used for queries) is protected by Secure Enclave but **cached in the Application Processor** for fast lookups
- The **per-item secret value key** requires a **round-trip through the Secure Enclave** for decryption

So yes, on Apple Silicon, generic passwords do get Secure Enclave involvement for decrypting the actual secret value. However:

- The secret value is **not stored inside the Secure Enclave** -- it's encrypted on disk with a key that the Secure Enclave manages
- The Secure Enclave performs the key unwrapping, not the storage
- **Only elliptic curve private keys** created with `kSecAttrTokenIDSecureEnclave` are truly "inside" the Secure Enclave (never extractable)
- Generic passwords are encrypted-at-rest with hardware-managed keys, which is still excellent protection

**For Worthless:** The Secure Enclave provides meaningful protection for Shard B at rest (the disk encryption key is hardware-bound). It does NOT prevent a local process with ACL access from reading the decrypted value at runtime. This is fine -- our threat model is key exfiltration from disk/backups, not local process isolation.

---

## 2. Windows Credential Manager

### 2.1 DPAPI (Data Protection API)

**How it works:**
- `CryptProtectData` encrypts data using a key derived from the user's **login credentials** (password hash + SID + master key)
- `CryptUnprotectData` decrypts -- only works for the **same user account** on the **same machine**
- The master key is stored in `%APPDATA%\Microsoft\Protect\{SID}\`
- Decryption goes through LSA (Local Security Authority) via **local RPC** -- never touches the network

**Headless operation:** Yes. DPAPI works without any UI. The LSA service handles key material in the background. No prompts, no dialogs. Works from:
- Background processes
- Windows services (if running as the same user who encrypted)
- SSH sessions (if the user profile is loaded -- which it is for interactive SSH)
- Scheduled tasks (if configured to run as the user)

**Does NOT work from:**
- A different user account (even admin)
- A Windows service running as `LocalSystem` (unless `CRYPTPROTECT_LOCAL_MACHINE` flag was used, which makes it machine-scoped, not user-scoped)
- If the user's password was **reset by an admin** (vs. changed by the user themselves) -- master key derivation breaks

**Trust domain assessment:** DPAPI user-scope means any process running as the same user can decrypt. This is the same trust domain as filesystem ACLs but with the added protection that:
1. The data is encrypted at rest (file copy doesn't help)
2. The encryption key is derived from credentials (disk theft doesn't help unless you also have the password)
3. Backup files are useless without the master key

For Worthless, this is sufficient -- the shard is worthless alone, and same-user access is the expected trust boundary.

### 2.2 Windows Credential Manager (CredWrite/CredRead)

**API:** `CredWrite` / `CredRead` / `CredDelete` (Win32 API in `advapi32.dll`)

**Size limits:**
- `CRED_TYPE_GENERIC`: credential blob maximum is **512 bytes** (CRED_MAX_CREDENTIAL_BLOB_SIZE / 2 for pre-Vista, 5*512 = 2560 for Vista+)
- Our shard is ~50 bytes -- well within limits
- Note: [keyring issue #355](https://github.com/jaraco/keyring/issues/355) documents that exceeding the limit produces an obscure "stub received bad data" error (Win32 error 1783)

**Background process access:** Yes, completely headless. CredRead/CredWrite are pure API calls with no UI component. They internally use DPAPI for encryption. Works from:
- Background services (running as the user)
- SSH sessions
- Scheduled tasks
- Any process with the user's token

**Python `keyring` support:** The `keyring` library uses `WinVaultKeyring` as the default backend on Windows, which wraps CredRead/CredWrite. Works out of the box:
```python
import keyring
keyring.set_password("worthless", "shard-b", base64_shard)  # enrollment
shard = keyring.get_password("worthless", "shard-b")        # every request
```

### 2.3 DPAPI vs. Credential Manager: which to use?

| Feature | Raw DPAPI | Credential Manager |
|---------|-----------|-------------------|
| API | `CryptProtectData`/`CryptUnprotectData` | `CredWrite`/`CredRead` |
| Storage | You manage the encrypted blob (file) | OS manages storage |
| Size limit | Arbitrary | 512-2560 bytes |
| Python support | `win32crypt` (pywin32) | `keyring` (built-in backend) |
| Headless | Yes | Yes |
| Backup/roaming | No (local only) | Optional (domain roaming) |

**Verdict: Use Credential Manager (via `keyring`).** It's higher-level, OS-managed, has excellent Python support, and is backed by DPAPI internally. No reason to go lower-level.

### 2.4 WSL2

WSL2 runs a real Linux kernel in a lightweight VM. It does **not** have native access to Windows Credential Manager.

**Bridge options:**

1. **`keyring_wincred`** ([github.com/ilpianista/keyring_wincred](https://github.com/ilpianista/keyring_wincred)): A Python `keyring` backend that calls Windows Credential Manager from WSL2 via PowerShell interop (`powershell.exe` with inline C#). Works but adds ~200ms latency per call and requires `powershell.exe` to be accessible from WSL2.

2. **Git Credential Manager (GCM)**: Well-tested bridge for Git credentials, but not a general-purpose secret store API.

3. **Linux kernel keyring** (`keyctl`): WSL2 supports the Linux kernel keyring (`@s` session keyring, `@u` user keyring). This is process-scoped or session-scoped, NOT persisted across reboots by default.

4. **`libsecret` / `gnome-keyring`**: Requires a D-Bus session, which WSL2 may or may not have configured.

**Recommendation for WSL2:**
- Primary: Use `keyring_wincred` backend to bridge to Windows Credential Manager
- Fallback: Encrypted file on disk (using DPAPI-equivalent via the PowerShell bridge, or a key derived from a machine-local secret)
- The WSL2 case is a lower-priority edge case. Most WSL2 users can use the native Windows CLI directly.

---

## 3. Comparative Summary

| Property | macOS Keychain | Windows Credential Manager |
|----------|---------------|---------------------------|
| Encryption at rest | AES-256-GCM, key managed by Secure Enclave (Apple Silicon) | DPAPI (user password-derived key) |
| Headless read | Yes (with `-A` ACL) | Yes (always) |
| GUI prompt risk | With `-T` ACL: yes if binary changes. With `-A`: none. | None |
| Background process | Yes (login keychain must be unlocked) | Yes (user profile must be loaded) |
| SSH-only session | Risky (keychain may be locked) | Works (profile loaded on interactive SSH) |
| Unsigned binary | No restriction (with `-A`) | No restriction |
| Python `keyring` | Works (macOS backend) | Works (WinVault backend) |
| Size limit | No practical limit | 512-2560 bytes (ample for 50B) |
| Survives reboot | Yes (persisted in keychain file) | Yes (persisted by OS) |
| Survives password reset | Yes | No (DPAPI master key invalidated) |

---

## 4. RECOMMENDATION: Exact API Sequence

### macOS: Enrollment (interactive, one-time)

```bash
# Store shard in login keychain, allow all apps
security add-generic-password \
  -a "worthless-shard-b" \
  -s "com.worthless.shard" \
  -l "Worthless API Key Shard B" \
  -w "$(echo -n '<raw-shard-bytes>' | base64)" \
  -A \
  -U
```

Flags:
- `-a`: account name (identifier)
- `-s`: service name (used for lookup)
- `-l`: label (human-readable in Keychain Access.app)
- `-w`: password value (the base64-encoded shard)
- `-A`: allow ALL applications (no prompts ever)
- `-U`: update if exists (idempotent)

### macOS: Read (headless, every request)

```bash
security find-generic-password \
  -a "worthless-shard-b" \
  -s "com.worthless.shard" \
  -w
```

Returns the password value to stdout. Exit code 0 on success, 44 if not found.

**Python implementation:**
```python
import subprocess

def read_shard_macos() -> bytes:
    result = subprocess.run(
        ["security", "find-generic-password",
         "-a", "worthless-shard-b",
         "-s", "com.worthless.shard",
         "-w"],
        capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise RuntimeError(f"Keychain read failed: {result.stderr}")
    import base64
    return base64.b64decode(result.stdout.strip())
```

### macOS: Delete (unenrollment)

```bash
security delete-generic-password \
  -a "worthless-shard-b" \
  -s "com.worthless.shard"
```

### Windows: Enrollment (interactive, one-time)

```python
import keyring

def store_shard_windows(shard: bytes) -> None:
    import base64
    keyring.set_password("com.worthless.shard", "worthless-shard-b", base64.b64encode(shard).decode())
```

### Windows: Read (headless, every request)

```python
import keyring

def read_shard_windows() -> bytes:
    import base64
    value = keyring.get_password("com.worthless.shard", "worthless-shard-b")
    if value is None:
        raise RuntimeError("Shard not found in Credential Manager")
    return base64.b64decode(value)
```

### Windows: Delete (unenrollment)

```python
import keyring
keyring.delete_password("com.worthless.shard", "worthless-shard-b")
```

### Cross-platform wrapper (recommended)

```python
import platform
import base64

def read_shard() -> bytes:
    """Read Shard B from OS credential store. Headless, no prompts."""
    system = platform.system()
    if system == "Darwin":
        return _read_shard_macos()
    elif system == "Windows":
        return _read_shard_windows()
    elif system == "Linux":
        return _read_shard_linux()  # kernel keyring or libsecret
    else:
        raise RuntimeError(f"Unsupported platform: {system}")
```

### Edge cases to handle

| Scenario | Detection | Fallback |
|----------|-----------|----------|
| macOS keychain locked (SSH-only) | `security` returns error 36 | Prompt user to unlock or use `security unlock-keychain` |
| Windows password reset by admin | `keyring.get_password` returns None | Re-enrollment required |
| WSL2 | `platform.system() == "Linux"` + check for `/proc/sys/fs/binfmt_misc/WSLInterop` | Use `keyring_wincred` backend |
| Linux headless server | No keychain available | Encrypted file with `WORTHLESS_SHARD_KEY` env var |

---

## 5. Security Assessment

**Threat: Local process reads shard**
- Both macOS (`-A`) and Windows (same-user DPAPI) allow any local process by the same user to read the shard
- This is acceptable because the shard is worthless alone (2-of-3 sharing)
- The server holds Shard C, the user holds Shard A -- local compromise of Shard B does not expose the API key

**Threat: Disk theft / backup exfiltration**
- macOS: Shard encrypted with Secure Enclave-managed key -- useless without the hardware
- Windows: Shard encrypted with DPAPI -- useless without the user's login credentials
- Both platforms provide meaningful at-rest protection

**Threat: Memory dump**
- After `security find-generic-password -w`, the shard is briefly in process memory
- Standard memory hygiene applies (zero after use, use `bytearray` not `bytes` per SR-01)
- Not specific to credential store choice

---

## Sources

- [Apple Keychain Data Protection](https://support.apple.com/guide/security/keychain-data-protection-secb0694df1a/web)
- [Apple Secure Enclave](https://support.apple.com/guide/security/the-secure-enclave-sec59b0b31ff/web)
- [Apple Developer: Protecting Keys with Secure Enclave](https://developer.apple.com/documentation/security/protecting-keys-with-the-secure-enclave)
- [security(1) man page](https://ss64.com/mac/security.html)
- [keyring Python library](https://pypi.org/project/keyring/)
- [keyring issue #457: secrets accessible without prompt](https://github.com/jaraco/keyring/issues/457)
- [keyring issue #619: multiple popups on binary change](https://github.com/jaraco/keyring/issues/619)
- [keyring issue #355: credential size limit](https://github.com/jaraco/keyring/issues/355)
- [keyring_wincred for WSL](https://github.com/ilpianista/keyring_wincred)
- [HackTricks: macOS Keychain](https://hacktricks.wiki/en/macos-hardening/macos-red-teaming/macos-keychain.html)
- [Microsoft: CryptProtectData](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)
- [Microsoft: CREDENTIAL structure](https://learn.microsoft.com/en-us/windows/win32/api/wincred/ns-wincred-credentiala)
- [DPAPI internals](https://tierzerosecurity.co.nz/2024/01/22/data-protection-windows-api.html)
- [Scripting macOS Keychain partition IDs](https://mostlikelee.com/blog-1/2017/9/16/scripting-the-macos-keychain-partition-ids)
- [Jan-Piet Mens: Storing passwords in macOS keychain](https://jpmens.net/2021/04/18/storing-passwords-in-macos-keychain/)
