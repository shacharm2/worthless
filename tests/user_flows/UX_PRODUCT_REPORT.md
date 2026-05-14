# User-flow UX/Product report

Date: 2026-05-09
Scope: Linear `WOR-439`, first implementation branch `feature/wor-439-user-flow-suite`

## Executive summary

This report started with the first two user-flow lanes:

- `WOR-440`: native CLI operations.
- `WOR-445`: recovery, teammate handoff, rotation, and multi-project drift.

It does not yet protect install/reinstall/uninstall, Docker clean distro
journeys, OpenClaw, or agent/MCP setup. This branch now includes a first CI
lane for Linux/macOS user-flow proof plus uploaded terminal trace artifacts;
Windows native and WSL proof remain explicitly deferred.

The stress-test follow-on branch adds native destructive-state journeys on top
of that baseline. The suite now contains 14 user-flow tests:

- 4 seed tests that already existed.
- 8 tests added by this branch.
- 2 native stress tests added by the stress follow-on branch.

The product confidence gained is centered on "I have a project with `.env`;
Worthless can lock it, tell me what happened, recover it, and avoid corrupting
nearby projects." The product confidence not yet gained is centered on "I am a
new user or agent starting from installation."

## How to use this report

Use the tables below in two directions:

- Bottom up: start from a pytest and understand the UX promise it protects.
- Top down: manually replay a user journey, then use the mapped pytest as the
  first trace point when behavior does not match the expected UX.

Manual checks should use an isolated home:

```bash
export WORTHLESS_HOME="$(mktemp -d)/.worthless"
```

Do not use real provider keys for these local UX checks. Generate fake keys at
runtime:

```bash
python -c 'from tests.helpers import fake_openai_key, fake_anthropic_key; print("OPENAI_API_KEY="+fake_openai_key()); print("ANTHROPIC_API_KEY="+fake_anthropic_key())' > .env
```

Run the automated suite with:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/user_flows -m user_flow -q
```

For literal terminal proof, see [`TERMINAL_TRACES.md`](TERMINAL_TRACES.md).
It is generated from real `worthless` subprocess calls against isolated temp
projects with fake key material and redacted `.env` snapshots.

Refresh the traces with:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run python tests/user_flows/render_traces.py \
  --output tests/user_flows/TERMINAL_TRACES.md
```

## UX promises now protected

| User journey | Product promise | Manual UX signs | Pytest trace |
| --- | --- | --- | --- |
| Existing keyring path, lock | Worthless should not repeatedly ask the native keyring for the same secret material. | On a native keyring machine, repeated lock should not cause repeated prompts. | `test_keychain_call_count.py::test_lock_calls_keyring_get_password_once_on_existing_key_path` |
| Existing keyring path, unlock | Unlock should use the cached keyring path efficiently. | Unlock should restore the original key without multiple keyring prompts. | `test_keychain_call_count.py::test_unlock_calls_keyring_get_password_once_on_existing_key_path` |
| Broken `.env` dogfood recovery | A user who breaks `.env` should see consistent status/doctor guidance and a recoverable path. | `unlock` fails clearly, `status`/`doctor` explain the issue, `doctor --fix` restores a clean state. | `test_doctor_dogfood.py::test_full_dogfood_lock_break_doctor_recover` |
| `wrap` magic moment | A wrapped child process should receive a usable provider base URL and reach the local proxy. | Child process sees provider base URL in its environment and can hit proxy health. | `test_wrap_magic_moment.py::test_wrap_child_reaches_proxy_via_env_url` |
| User-flow environment isolation | User-flow tests must not inherit real Worthless/provider state from the parent shell. | Manual failures should not depend on the developer's real shell env or `~/.worthless`. | `test_native_cli_journeys.py::test_scrubbed_env_deletes_ambient_worthless_overrides` |
| Default `worthless --yes` | A user can run the default command and get keys protected plus provider base URLs added. | Output names protected variables, raw keys disappear, `OPENAI_BASE_URL` and `ANTHROPIC_BASE_URL` are written. | `test_native_cli_journeys.py::test_default_command_yes_detects_and_locks_project_env` |
| Lock/status/scan/unlock | The core native CLI round trip restores the exact original key. | `lock` protects, `status` says protected, `scan` finds no raw key, `unlock` restores the original value. | `test_native_cli_journeys.py::test_lock_status_scan_unlock_round_trip_restores_original_key` |
| Empty project status | A fresh project with no keys should produce plain-English empty states. | `scan` and `status` should not look like a crash or internal diagnostic. | `test_native_cli_journeys.py::test_scan_and_status_empty_states_are_plain_english` |
| Teammate handoff | A copied locked `.env` without local DB/keyring state should fail safely and explain recovery. | `unlock` fails without traceback and mentions no enrollment plus re-locking from the original machine. | `test_recovery_journeys.py::test_teammate_handoff_locked_env_without_db_fails_with_hint` |
| Same-shape key rotation | A user can paste a replacement raw key into the same var and re-run `lock`. | Second `lock` protects the new value; `unlock` restores the new key, not the old one. | `test_recovery_journeys.py::test_rotation_relock_restores_new_raw_key` |
| Different-shape key rotation | Relock should also work when the replacement key has a different provider prefix shape. | Replacing `sk-proj-...` with `sk-...` should not produce `WRTLS-199` or a traceback. | `test_recovery_journeys.py::test_rotation_relock_accepts_different_shape_raw_key` |
| Multi-project isolation | Unlocking one project must not restore or corrupt another project under the same Worthless home. | Project A unlocks to raw key while project B remains protected until explicitly unlocked. | `test_recovery_journeys.py::test_multi_project_unlock_keeps_other_project_protected` |
| Refused rewrite after planned lock | If Worthless refuses to rewrite an unsafe `.env`, the user must not be left half-protected. | `lock` fails without traceback, original `.env` bytes remain, status has no protected phantom row, and explicit scan still finds the raw key. | `test_native_stress_journeys.py::test_lock_rewrite_refusal_leaves_env_and_status_recoverable` |
| Tampered locked `.env` | If a user edits a locked shard value, unlock should fail clearly without destroying evidence. | `unlock` fails without traceback, says the value was modified after lock / commitment mismatch, leaves `.env` unchanged, and status still shows protected state. | `test_native_stress_journeys.py::test_unlock_tampered_locked_env_fails_without_destroying_state` |

## Manual journey scripts

These are smoke scripts for human UX review. They intentionally mirror the
pytest journeys but leave room to judge wording, pacing, and clarity.

### Journey A: native lock/status/scan/unlock

1. Create a temporary project and fake `.env`.
2. Run `worthless lock --env .env`.
3. Run `worthless status`.
4. Run `worthless scan .`.
5. Run `worthless unlock --env .env`.

Expected UX:

- No traceback.
- Raw key is absent after lock.
- Status uses user-facing protected wording.
- Scan does not report the protected shard as a raw secret.
- Unlock restores the exact original key.

Trace first to:

- `test_native_cli_journeys.py::test_lock_status_scan_unlock_round_trip_restores_original_key`

### Journey B: default command

1. Create `.env` with fake OpenAI and Anthropic keys.
2. Run `worthless --yes`.
3. Inspect `.env`.

Expected UX:

- Output mentions both key vars.
- Both raw keys are replaced.
- Provider base URLs are added.
- Proxy startup/health messaging is understandable.

Trace first to:

- `test_native_cli_journeys.py::test_default_command_yes_detects_and_locks_project_env`

### Journey C: teammate handoff failure

1. Lock `.env` under one isolated `WORTHLESS_HOME`.
2. Copy only the locked `.env` into another project.
3. Switch to a fresh isolated `WORTHLESS_HOME`.
4. Run `worthless unlock --env .env`.

Expected UX:

- Command fails safely.
- No traceback.
- Output says no enrollment was found.
- Output tells the user to re-lock from the original machine.

Trace first to:

- `test_recovery_journeys.py::test_teammate_handoff_locked_env_without_db_fails_with_hint`

### Journey D: key rotation and relock

1. Lock an old fake key.
2. Replace the `.env` value with a new raw fake key in the same variable.
3. Run `worthless lock --env .env` again.
4. Run `worthless unlock --env .env`.

Expected UX:

- Second lock succeeds.
- The new raw key is protected.
- Unlock restores the new raw key.
- There is no internal error for prefix/length changes.

Trace first to:

- `test_recovery_journeys.py::test_rotation_relock_restores_new_raw_key`
- `test_recovery_journeys.py::test_rotation_relock_accepts_different_shape_raw_key`

### Journey E: multi-project safety

1. Create two projects under one isolated `WORTHLESS_HOME`.
2. Lock each project's `.env`.
3. Unlock project A.
4. Inspect project B.
5. Unlock project B.

Expected UX:

- Project A restores correctly.
- Project B remains protected after project A unlocks.
- Project B restores correctly only when explicitly unlocked.

Trace first to:

- `test_recovery_journeys.py::test_multi_project_unlock_keeps_other_project_protected`

### Journey F: unsafe rewrite refusal

1. Create `.env` with a fake raw key.
2. Create a hardlink to that same `.env`.
3. Run `worthless lock --env .env`.
4. Run `worthless status`.
5. Run `worthless scan .env`.

Expected UX:

- Lock fails without traceback.
- `.env` still contains the original raw key.
- Status does not claim the key is protected.
- Explicit scan reports the raw key as unprotected without exposing the full value.

Trace first to:

- `test_native_stress_journeys.py::test_lock_rewrite_refusal_leaves_env_and_status_recoverable`

### Journey G: tampered locked value

1. Lock a fake raw key.
2. Replace the locked `.env` value with another shape-valid fake key.
3. Run `worthless unlock --env .env`.
4. Run `worthless status`.

Expected UX:

- Unlock fails without traceback.
- Output says the locked value was modified after lock or has a commitment mismatch.
- `.env` keeps the tampered value for investigation/retry.
- Status still shows protected state; the DB row was not deleted.

Trace first to:

- `test_native_stress_journeys.py::test_unlock_tampered_locked_env_fails_without_destroying_state`

## Current gaps

These are intentionally not covered by this first branch:

| Linear issue | Surface | Status |
| --- | --- | --- |
| `WOR-441` | Install, reinstall, uninstall | Backlog |
| `WOR-442` | Docker and clean distro matrix | Backlog |
| `WOR-443` | OpenClaw install/config/protected request | Backlog |
| `WOR-444` | Agent and MCP driven setup | Backlog |
| `WOR-446` | CI user-flow lane | First pass in this branch: Ubuntu/macOS user flows and trace artifacts; Windows/WSL deferred |

Product-risk gaps still worth promoting into explicit journeys:

- Deleting part of `WORTHLESS_HOME` after lock.
- Corrupt shard DB or fernet material.
- Crash or disk-full during relock.
- Same key across two `.env` files.
- Unlock-all or multi-env state transitions.
- Agent-facing JSON/exit-code contracts.
- Non-TTY install and consent behavior.

## Review rule

If manual UX testing finds a mismatch, record:

1. The journey name above.
2. The exact command and output.
3. The mapped pytest name.
4. Whether the mismatch is product wording, state behavior, platform behavior,
   or test harness drift.

That makes manual UX review traceable back to the automated suite without
turning the tests into vague end-to-end blobs.
