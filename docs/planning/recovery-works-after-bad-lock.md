# Recovery works after a bad lock (WOR-276)

Feature B of WOR-252. Depends on sub-PR 1 gate ([#78](https://github.com/shacharm2/worthless/pull/78)) and sub-PR 2 wire-up ([#84](https://github.com/shacharm2/worthless/pull/84)). Must ship in the same release as Feature A.

## Outcome

A user who loses `.env` to **any** cause — operator typo, corrupted merge, interrupted lock, mid-air swap to a symlink that somehow survives the gate, stray `>` redirect — can one-command their file back. They don't need to know git, don't need to have committed the file, and don't need to read docs to find the backup directory.

## Why this ships with Feature A

Without backups, the gate makes the failure mode better (refuse instead of corrupt) but leaves anyone who already corrupted their `.env` pre-gate with no recovery path. Shipping gate alone is a UX regression for that cohort. A + B together = "safer writes, with a net under them."

## Why out-of-repo

`$XDG_DATA_HOME/worthless/backups/<sha256(repo_root_abspath)>/` — the backup directory **must not** live inside the repo. Inside-repo backups leak via:

- `git add .` committing plaintext secrets
- npm/pnpm/pypi publish bundling the dir into packages
- Docker `COPY .` shipping secrets in images
- Dropbox / iCloud / OneDrive silently syncing secrets to the cloud
- GitHub Actions `actions/upload-artifact` catching them

SHA256 of the repo absolute path is the directory name because (a) it's stable across sessions, (b) it doesn't leak the repo name into `/proc/*/cmdline` or process listings, (c) separate checkouts of the same repo (worktrees, bind-mounts) land in distinct buckets because their `.resolve()` absolute paths differ. Symlinks pointing at the same repo *do* resolve to the same bucket — that's correct, because it's the same underlying repo.

## Three sub-features, one PR

### B.1 — Backup on every destructive write

Every call that reaches `safe_rewrite()` creates a byte-identical copy of the pre-write content at:

```text
$XDG_DATA_HOME/worthless/backups/<sha256(repo_root)>/<basename>.<ISO8601_ns>.<pid>.<counter>.bak
```

The timestamp is nanosecond-precision (`time.time_ns()` rendered as `YYYY-MM-DDTHH:MM:SS.nnnnnnnnn`) and the `<counter>` is a monotonically incrementing per-process integer. `<pid>` alone doesn't protect against in-process collisions (two rewrites of the same target by the same process in the same nanosecond on coarse-resolution clocks); `<counter>` does. Inter-process collisions are covered by `<pid>`. The atomic-via-tmp-rename step additionally uses `O_EXCL` so a race that *did* produce the same name would surface as `EEXIST` rather than silent clobber.

- **Hook point:** `_hook_before_replace` already exists in `safe_rewrite()`. Backup goes there — inside the flock, after fsync of the staging tmp, before the atomic rename. Guarantees the backup exists before the window where the user file could be replaced.
- **Durability:** fsync backup file + fsync backup-dir fd before returning.
- **Mode:** `0o600` (user-only). Refuse if the backup dir already exists with a weaker mode (would be a pre-existing config leak).
- **Atomicity:** backup is written to a `.tmp` name then renamed, same pattern as the gate itself.
- **Failure policy:** if backup creation fails for any reason (`ENOSPC`, `EROFS`, parent-dir stat mismatch), refuse the write with new `UnsafeReason.BACKUP`. Better to block a write than to let one succeed without recovery.
- **Rotation:** keep the last 20 backups per target basename per repo. Older ones unlinked best-effort **after** the new backup is safely on disk. Never refuse a write because rotation failed.

### B.2 — `worthless restore` command family

Four flags, one command:

| Invocation | Behaviour |
|---|---|
| `worthless restore` | Interactive: list backups for the current repo, number them, let user pick. Exit 0 if empty. |
| `worthless restore --list` | Non-interactive: print `<timestamp>  <size>  <target>` one per line, newest first. Exit 0 always. |
| `worthless restore <target>` | Restore most recent backup of `<target>`. If the target's current bytes differ from the most recent backup, prompt on stderr (`overwrite <target>? [y/N]`) and read the answer from stdin before proceeding. Non-TTY stdin with no `--force` → refuse with exit 2 (won't silently clobber new edits, won't hang CI). |
| `worthless restore <target> --force` | Bypass the interactive confirmation. Still uses `safe_rewrite()` to write, so gate invariants apply. |
| `worthless restore --all-repos` | List backups across every repo bucket (for "which repo was that in?" recovery). |

Restore writes via `safe_rewrite()` itself — the gate checks the restore target exactly like any other write. This is the same code path, not a bypass.

### B.3 — RECOVERY.md + first-run output

- **`RECOVERY.md`** at the repo root (committed into the tool's own repo, shipped in the wheel). One page. Opens with a code fence: `worthless restore` — that's the recovery command, first thing on the page. Then explains the backup directory, rotation policy, and the `--force` flag.
- **First-run output** — the first time the CLI creates a backup dir for a given repo, print *once* to stderr:
  ```console
  worthless: backups enabled → ~/.local/share/worthless/backups/<bucket>/
  worthless: run `worthless restore` if you need to roll back
  ```
  Controlled by a `.first-run-seen` marker inside the bucket so the message doesn't repeat.

## TDD breakdown — test list (all red before any impl)

### Tests — backup creation (tests/safe_rewrite/test_backup.py)

1. `test_backup_written_on_successful_rewrite` — backup file exists after `safe_rewrite()` returns, content == original bytes.
2. `test_backup_path_is_sha256_of_repo_root` — directory name matches `sha256(str(repo_root.resolve())).hexdigest()`.
3. `test_backup_filename_format` — `<basename>.<iso8601_ns>.<pid>.<counter>.bak`, all four components required. Nanosecond-precision timestamp + per-process counter eliminates in-process collisions on coarse clocks.
4. `test_backup_mode_0600` — backup file is user-read-write only.
5. `test_backup_parent_dir_mode_0700` — bucket dir is user-only.
6. `test_backup_refuses_if_bucket_has_wrong_mode` — pre-existing world-readable bucket refuses with `UnsafeReason.BACKUP`.
7. `test_backup_failure_aborts_write` — patching backup writer to raise `OSError(ENOSPC)` leaves the target untouched.
8. `test_backup_fsync` — backup file fd saw fsync before rename (instrument via `_hook_after_backup_fsync`).
9. `test_backup_atomic_via_tmp_rename` — no partial `.bak` files left behind after SIGKILL between write and rename.
10. `test_backup_rotation_keeps_last_20` — 25 sequential rewrites leave exactly 20 backups, newest kept.
11. `test_backup_rotation_failure_does_not_abort_write` — unlink-failing rotation logs warning, write still succeeds.
12. `test_xdg_data_home_honoured` — `$XDG_DATA_HOME=/tmp/x` → backups under `/tmp/x/worthless/backups/`.
13. `test_xdg_data_home_unset_falls_back_to_local_share` — unset → `~/.local/share/worthless/backups/`.
14. `test_no_backup_outside_repo_root` — backups never appear inside `repo_root` tree.

### Tests — restore command (tests/commands/test_restore.py)

15. `test_restore_list_empty_exits_zero` — fresh repo, `worthless restore --list` prints nothing, exit 0.
16. `test_restore_list_newest_first` — 3 backups → listed in descending timestamp order.
17. `test_restore_file_writes_via_safe_rewrite` — instrument `safe_rewrite` → restore invokes it with the backup content.
18. `test_restore_prompts_if_target_changed_since_backup` — backup made, target edited post-backup; `restore` with stdin=`n\n` aborts (exit 1, target untouched); stdin=`y\n` proceeds; non-TTY stdin without `--force` refuses with exit 2.
19. `test_restore_force_bypasses_confirmation_prompt` — `--force` still passes the gate; only the interactive confirmation is bypassed.
20. `test_restore_all_repos_across_buckets` — two bucket dirs → both listed, grouped by bucket.
21. `test_restore_interactive_picker` — stdin=`1\n`, picks first backup, restores it. Stdin=`q\n` exits clean.
22. `test_restore_refuses_symlinked_backup` — backup dir replaced with symlink → refuse (same invariants).
23. `test_restore_refuses_non_existent_target` — `worthless restore /doesnotexist` → exit non-zero, stderr mentions no backups.

### Tests — first-run output (tests/test_first_run.py)

24. `test_first_run_prints_backup_path_once` — first CLI invocation prints the path; second does not.
25. `test_first_run_marker_file_created` — `.first-run-seen` exists after first invocation.
26. `test_first_run_marker_mode_0600` — marker is user-only.
27. `test_first_run_message_to_stderr_not_stdout` — stdout usable for pipelines.
28. `test_recovery_md_shipped_in_wheel` — `importlib.resources.files("worthless").joinpath("RECOVERY.md").is_file()`.
29. `test_recovery_md_first_line_is_the_command` — contract: the first fenced block contains `worthless restore`.

### Tests — chaos (tests/safe_rewrite/test_backup_chaos.py)

30. `test_sigkill_between_backup_write_and_rename_leaves_no_ghost_tmp` — crash-consistency across the backup window.
31. `test_sigkill_between_backup_rename_and_target_rename_leaves_backup_intact` — if the target rename never happens, the backup survives (recovery possible).
32. `test_concurrent_rewrites_do_not_collide_on_backup_filename` — (a) two processes writing the same target at the same nanosecond produce two distinct backups (the `.<pid>.` component). (b) The **same** process doing two back-to-back rewrites of the same target inside the same nanosecond clock tick (simulated by monkeypatching `time.time_ns`) also produces two distinct backups (the `.<counter>.` component). `O_EXCL` on the tmp write asserts no silent clobber if the collision-avoidance ever fails.

### Tests — integration / e2e (tests/test_e2e_recovery.py)

33. `test_lock_then_restore_round_trip` — E2E: `worthless lock`, corrupt the `.env`, `worthless restore <target>` with stdin=`y\n` (confirms the overwrite-on-divergence prompt), assert content == pre-lock original. A `--force` variant asserts the same outcome without a prompt.
34. `test_restore_preserves_bom_crlf_and_export` — round-trip a file with BOM + CRLF + `export KEY=val`, restore, assert byte-identical.
35. `test_restore_after_failed_write_still_works` — simulate a rewrite that refuses mid-way; assert backup was never created (nothing to restore), no ghost backup dir.

**Target: 35 tests red, implementation brings them green.** No test added after the fact.

## Implementation order — atomic commits

1. **`feat(safe-rewrite): add backup hook + UnsafeReason.BACKUP`** — just the constant, the enum value, and the `_backup_before_replace` helper as a stub that raises NotImplementedError. Red tests 1-14 confirm the seam.
2. **`feat(safe-rewrite): write byte-identical backup under $XDG_DATA_HOME`** — fill in the stub. Tests 1-6, 12, 13, 14 green.
3. **`feat(safe-rewrite): atomic backup via tmp-rename + fsync + mode 0o600`** — durability + mode invariants. Tests 4, 5, 7, 8, 9 green.
4. **`feat(safe-rewrite): rotate backups to last 20 per target`** — tests 10, 11 green.
5. **`feat(safe-rewrite): chaos-resistant backup window`** — tests 30, 31, 32 green.
6. **`feat(cli): worthless restore --list`** — read-only first. Tests 15, 16, 20 green.
7. **`feat(cli): worthless restore <target>`** — write path uses `safe_rewrite()`. Tests 17, 18, 19, 22, 23 green.
8. **`feat(cli): worthless restore interactive picker + --all-repos`** — tests 21, 20 green.
9. **`feat(cli): first-run output + marker`** — tests 24-27 green.
10. **`docs: RECOVERY.md shipped in wheel`** — tests 28, 29 green.
11. **`test(e2e): lock → corrupt → restore round trip`** — tests 33-35 green. Must run against the **real** CLI entrypoint, not unit-level mocks.

Each commit leaves the tree green for the tests it claims to cover. No "big bang" commit that green-lights 30+ tests at once — that's how regressions slip through.

## Branch + PR

- Branch: `feat/wor-276-recovery-works` off `feat/wor-252-sub-pr-2-wire-callers` (so restore uses the post-allowlist gate).
- One PR, not three. The user-visible outcome is "recovery works" — splitting backups from restore ships one without the other and breaks the demo.
- Title: `feat: recovery works after a bad lock (WOR-276)`
- Merge order in main: Feature A PRs merged first (#78 → #84), then Feature B PR. Feature A alone must never reach `main`.

## Out of scope — explicit

- **Cross-machine backup sync.** Backups are local-only. If the user loses their laptop, they lose the backup. That's a different feature.
- **Encrypted backups.** The backup directory has the same sensitivity as `.env` itself; filesystem perms (`0o700` / `0o600`) are the threat model. Disk-level encryption is the user's responsibility.
- **Time-travel beyond 20 revisions.** 20 is enough for "I just broke it" recovery. Long-term history belongs in git.
- **Global restore UI (TUI).** `--list` + interactive picker cover the common cases. A richer UI is a later ticket if-and-only-if usage data says it's worth it.
- **Automatic recovery.** `worthless` never silently restores. Every restore is a user-initiated command. Silent restore is indistinguishable from corruption from the user's side.

## Risks

- **Disk fills → backups fail → writes refuse.** Mitigation: rotation caps at 20 backups × ~1 MiB file size = ~20 MiB per repo ceiling. Log warning at 80% of ceiling.
- **User deletes `~/.local/share/worthless/backups/` manually.** Next write recreates it. First-run marker re-fires. Acceptable.
- **Symlinked `$XDG_DATA_HOME`.** Gate invariants already reject `O_NOFOLLOW` violations on the backup path. Extend the existing `_dev_ino_match` helper rather than duplicate logic.

## Acceptance checklist (before merging)

- [ ] All 35 tests green on macOS + Linux CI.
- [ ] Chaos tests (30-32) run under both SIGKILL and errno injection.
- [ ] `worthless restore` appears in `--help` output.
- [ ] `RECOVERY.md` linked from the project README.
- [ ] First-run output verified manually in a fresh `$HOME` container.
- [ ] No backup directory appears inside any test-fixture repo after a full test run.
- [ ] Pre-commit stack green.

---

## Where's the finish line? (definition of done)

**One demo sentence must be true:**

> *"I corrupted my `.env`. I ran `worthless restore`. I got my file back. I didn't read any docs."*

That's it. If a human sitting at a fresh checkout can do that without reading the README, we're done. If they need to look up a flag, a path, or a command — we're not done.

The 35 tests are the **proof** that the sentence is true. The acceptance checklist is the **evidence**. Neither is the finish line; the sentence is.

## Progress dashboard

Progress is measurable at every commit because every commit green-lights a named subset of the 35 tests. Run this single command at any point to see where you are:

```bash
uv run pytest tests/safe_rewrite/test_backup.py \
              tests/safe_rewrite/test_backup_chaos.py \
              tests/commands/test_restore.py \
              tests/test_first_run.py \
              tests/test_e2e_recovery.py \
              -n 0 -p no:rerunfailures -o "addopts=" --timeout=90 -v
```

The output is the progress bar. No spreadsheet, no status meeting.

### Milestones (ordered, each is a merge-gate for the next)

| # | Milestone (what's true when green) | Tests covered | Progress = |
|---|---|---|---|
| M1 | **Seam exists.** Backup hook wired into `safe_rewrite`, `UnsafeReason.BACKUP` in the enum, stub raises NotImplementedError. | 0 green (tests 1-14 confirm the seam, still red) | 0 / 35 |
| M2 | **Backups get written.** Happy path: a `safe_rewrite` call leaves a byte-identical `.bak` under `$XDG_DATA_HOME`. | 1-6, 12-14 | 9 / 35 |
| M3 | **Backups are durable.** Atomic tmp-rename + fsync + 0o600. Failure aborts the write. | 4, 5, 7, 8, 9 (cumulative: 1-9, 12-14) | 12 / 35 |
| M4 | **Backups don't pile up.** Rotation caps at 20 per target. Rotation failure doesn't abort. | 10, 11 (cumulative: 1-14) | 14 / 35 |
| M5 | **Backups survive SIGKILL.** Chaos coverage of the backup window. | 30-32 (cumulative: 1-14 + 30-32) | 17 / 35 |
| M6 | **Backups are discoverable.** `worthless restore --list` works. | 15, 16, 20 | 20 / 35 |
| M7 | **Restore works.** `worthless restore <file>` writes via the gate. Mtime check prevents clobber. | 17, 18, 19, 22, 23 | 25 / 35 |
| M8 | **Restore is usable.** Interactive picker + `--all-repos`. | 21 (cumulative) | 26 / 35 |
| M9 | **Users find out backups exist.** First-run output + marker. | 24-27 | 30 / 35 |
| M10 | **Recovery doc ships.** `RECOVERY.md` in the wheel, first line is the command. | 28, 29 | 32 / 35 |
| M11 | **End-to-end round-trip proven.** Lock → corrupt → restore → byte-identical content. | 33-35 | 35 / 35 → **DONE** |

**Rule:** each milestone corresponds to exactly one commit in §*Implementation order — atomic commits* above. If a commit claims milestone Mn but the dashboard shows < Mn tests green, the commit is not done — back it out, don't stack Mn+1 on top.

### What's left — today's view

As of this writing:

- Feature A (WOR-275 "Writes can't destroy your data") — **shipped in code.** Draft PRs [#78](https://github.com/shacharm2/worthless/pull/78) and [#84](https://github.com/shacharm2/worthless/pull/84). Awaiting Feature B before either can merge to `main`.
- Feature B (this plan, WOR-276 "Recovery works after a bad lock") — **0 / 35 tests written.** First step: `everything-claude-code:planner` to validate breakdown, then `everything-claude-code:tdd-guide` to drive the red-first protocol.
- Feature C (WOR-277 "No plaintext leaks anywhere") — not started, independent of A+B, can ship separately.

### Measuring at every step

Three questions you can answer at any commit without asking me:

1. **Which milestone am I on?** → look at the last line of the latest commit message against §*Implementation order*.
2. **Am I actually there?** → run the progress dashboard command above and count green tests.
3. **What's the next move?** → read the next row of the milestone table.

No meetings, no status docs, no guessing.

---

## Feeding this plan into `everything-claude-code` agents

This plan is the input to two expert agents, **not a replacement for them:**

1. **`everything-claude-code:planner`** consumes this doc and produces the breakdown it thinks is correct. If its breakdown disagrees with this doc, the planner wins on technical details; this doc wins on outcome + scope + out-of-scope list. Reconciliation is the human's call.
2. **`everything-claude-code:tdd-guide`** consumes the post-planner breakdown and drives the red-first TDD protocol commit by commit. Its three checkpoints (first red test / full red suite / implement until green) map onto the milestone table above.

The sequence is therefore:

```text
this plan  →  planner agent  →  tdd-guide agent  →  milestones green  →  PR
```

Not this plan → code. The planner and tdd-guide are required collaborators, not optional polish.
