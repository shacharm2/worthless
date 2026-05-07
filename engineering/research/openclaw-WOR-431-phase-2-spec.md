# WOR-431 Phase 2 — `lock`/`unlock`/`doctor` OpenClaw Magic Integration

> **Status:** Approved spec. Phase 2.a shipped; Phase 2.b–2.f pending.
> **Linear:** [WOR-431](https://linear.app/plumbusai/issue/WOR-431) (epic [WOR-421](https://linear.app/plumbusai/issue/WOR-421); WOR-321 merged here as Duplicate)
> **Branch:** `feature/wor-421-openclaw-research-doc`
> **Worktree:** `/Users/shachar/Projects/worthless/worthless-wor421-openclaw`
> **Source of truth:** This file (in git, durable across machine moves). Linear gets the executive summary; verifier agents (Jenny/karen) cite this file by section. Implementation log at the bottom tracks what's actually shipped vs spec.

---

## Context

**Problem:** Today, OpenClaw's docker integration test (WOR-213, Done) only proves *proof-of-protocol*: with a hand-cooked `openclaw.json`, the proxy hop works. Real users never hand-write that file. They install OpenClaw, OpenClaw writes its own default config without our proxy baseUrl, and someone has to mutate it. Without that mutation, worthless protects nothing for OpenClaw users.

**Solution:** Extend `worthless lock` to silently detect OpenClaw and (a) inject `models.providers.worthless-<provider>` into `openclaw.json`, (b) install the embedded `SKILL.md` into `~/.openclaw/workspace/skills/worthless/`. Symmetric undo on `unlock`. Diagnostic surface on `doctor`. No new namespace. No prompts. No flags.

**User constraint (verbatim):** *"magic UX, needs to be really fucking robust and able to retreat cleanly on all errors. i want extensive tests on it"*

**Scope folded in:** WOR-321 ("worthless lock multi-config detection — .env + openclaw.json") collapses entirely into Phase 2.b here. Close WOR-321 as duplicate during Phase 2.f.

---

## Locked decisions (do not re-litigate)

| # | Decision | Why |
|---|---|---|
| L1 | **Best-effort + idempotent retry** rollback (NOT 2-phase commit) | OpenClaw integration is downstream of `lock` core; failures there must never roll back `.env`/DB writes |
| L2 | **`.env`/DB success + OpenClaw failure → exit code 0**, events surfaced in `--json` | The `.env` is the binding contract; OpenClaw is enhancement |
| L3 | **We own `~/.openclaw/workspace/skills/worthless/`** — overwrite stale content | Treating it as ours is documented in SKILL.md itself |
| L4 | **Real-container e2e deferred to WOR-432** | Phase 2 ships unit + functional + injection + concurrency; WOR-432 spins up the actual container |
| L5 | **Shard A in `openclaw.json` apiKey is OK** | Non-secret on its own; matches `.env` wrap-mode behavior; SR-04 not violated |
| L6 | **No new `openclaw` namespace** — extend existing commands; install-skill is plumbing not user-facing | Three of four planned verbs already have homes (lock/unlock/doctor) |

---

## Existing code to reuse

| File | What we reuse |
|---|---|
| `src/worthless/openclaw/config.py` | Phase 1 canonical parser. `set_provider()`, `unset_provider()`, `get_provider()`, `locate_config_path()`, `_file_lock()`, `_atomic_write_json()`, `OpenclawConfigError`. Atomic-write + flock guarantees inherit. |
| `src/worthless/cli/commands/lock.py` | Existing `_pass1_db_writes` + `_batch_rewrite` flow. Extend AFTER `_batch_rewrite` succeeds. |
| `src/worthless/cli/commands/unlock.py` | Existing `_unlock_alias` flow. Extend BEFORE or AFTER (independent). |
| `src/worthless/cli/commands/doctor.py` | Existing diagnostic command (already exists for stuck DB/.env states). Add `_check_openclaw()` row group. |
| `src/worthless/proxy/wrap.py` | `_PROVIDER_ENV_MAP` — single source of truth for provider names. Phase 2 must reference, never duplicate. |
| `tests/openclaw/openclaw-config/openclaw.json` | Real-schema fixture (treat as read-only after the bind-mount-pollution fix in WOR-432). |

---

## New files

```
src/worthless/openclaw/
  integration.py          # detect(), apply_lock(), apply_unlock(), health_check()
  skill.py                # install(), uninstall(), current_version()
  errors.py               # OpenclawErrorCode enum, OpenclawIntegrationEvent dataclass
  skill_assets/
    __init__.py           # empty (importlib.resources marker)
    SKILL.md              # placeholder; Phase 3 fills

tests/openclaw/
  test_integration_detect.py
  test_integration_apply_lock.py
  test_integration_apply_unlock.py
  test_integration_concurrency.py
  test_integration_injection.py
  test_integration_idempotency.py
  test_integration_round_trip.py
```

---

## Spec — Acceptance Criteria (Jenny-checkable)

Each AC is a single verifiable assertion. Implementation is "done" when **all** are PASS.

| ID | AC | How to verify |
|---|---|---|
| AC1 | `lock` on a host without OpenClaw produces byte-identical behavior to Phase 1 | Diff `--json` output before/after Phase 2 changes on a no-OpenClaw fixture |
| AC2 | `lock` on a host WITH OpenClaw produces a populated `openclaw.json` AND an installed skill folder, in one invocation, with no prompts | Functional test: bring up tmp_path with both `.env` and `openclaw.json`, run `lock`, assert both files mutated correctly |
| AC3 | `unlock` removes both side-effects cleanly (provider entries removed, skill folder removed) | Round-trip test RT-01 |
| AC4 | `doctor` shows OpenClaw status with traffic lights (skill installed? PATH ok? baseUrl matches running proxy?) | Functional test against mocked `health_check()` payload |
| AC5 | Every entry in §Failure Modes maps to a passing test in §Test Matrix | Coverage report shows all F01–F53 IDs referenced in test docstrings |
| AC6 | `--json` output is parseable by a fixture "Pi" consumer | `tests/test_integration_json_schema.py` parses output into `OpenclawIntegrationReport` Pydantic model |
| AC7 | `lock` → `unlock` round-trip leaves `openclaw.json` byte-identical to its pre-`lock` state | RT-01 (sort_keys=True from Phase 1 makes byte-comparison deterministic) |
| AC8 | Concurrency stress test (CONC-45) passes 50/50 iterations | CI smoke gate |
| AC9 | Phase 1's full test suite still passes (27/27, 93% coverage) | Regression — Phase 1 invariants unchanged |
| AC10 | `.env`/DB success + OpenClaw stage failure → exit code 0 with surfaced events (per L2) | Failure-injection tests INJ-20, INJ-21, F-XS-40, F-XS-41 |
| AC11 | `lock` core never rolls back due to OpenClaw failure (per L1) | Verify in F-XS-40: poison openclaw.json write, assert .env still gets the locked Shard A |
| AC12 | No private-repo references introduced in any Phase 2 file (per the segmentation rule from CLAUDE.md) | pre-commit `segmentation` hook |
| AC13 | All new files have ruff-clean code, 80%+ coverage, type hints | `uv run ruff check` + `uv run pytest --cov` |
| AC14 | WOR-321 closed as duplicate of WOR-431; WOR-431 description updated with Phase 2 plan summary | Linear state check |

---

## Failure modes (53 enumerated → test ID mapping)

### Detection-stage (F01–F04, F36)
| ID | Failure | Recovery | Test |
|---|---|---|---|
| F01 | `~` cannot expand (broken HOME) | Treat as "absent". Debug log. | U-DET-01 |
| F02 | `~/.openclaw/` is a regular file | Treat as "absent". Debug log. | F-DET-02 |
| F03 | `~/.openclaw/workspace/` is a dangling symlink | Treat as "absent". Debug log. | F-DET-03 |
| F04 | `os.access(workspace_dir, os.R_OK)` False | Treat as "absent" + warn in `--json`. | F-DET-04 |
| F36 | Read-only `$HOME` (some CI runners) | Detect via `os.access(home, os.W_OK)`; treat as "absent" upstream. | F-DET-36 |

### `openclaw.json` read-stage (F10–F16)
| ID | Failure | Recovery | Test |
|---|---|---|---|
| F10 | Malformed JSON | `OpenclawConfigError` from Phase 1 → `openclaw.config_unreadable` event. **Continue lock core.** | U-CFG-10 |
| F11 | `models` is array | Same as F10. | U-CFG-11 |
| F12 | `models.providers` is list | Same as F10. | U-CFG-12 |
| F13 | `worthless-<p>` exists with non-worthless `baseUrl` | **Conflict.** Skip that provider, emit `openclaw.provider_conflict`. Lock core succeeds. | F-CFG-13 |
| F14 | Non-`worthless-*` provider present (e.g. `openai`, `anthropic`) | Touch nothing. Phase 1 `set_provider()` already preserves siblings. | U-CFG-14 |
| F15 | `openclaw.json` is a symlink | Refuse to follow. `openclaw.symlink_refused`. | F-CFG-15 |
| F16 | Mode world-writable | Warn, proceed. Don't chmod. | F-CFG-16 |

### Write-stage (F20–F24)
| ID | Failure | Recovery | Test |
|---|---|---|---|
| F20 | EACCES on dir | `_atomic_write_json` raises OSError. `openclaw.write_failed`. | INJ-20 |
| F21 | ENOSPC mid-write | Phase 1 fsync + replace ensures original untouched. | INJ-21 |
| F22 | Lock contention with sidecar | flock with 5s timeout via `fcntl.LOCK_EX | LOCK_NB` retry-with-backoff. | CONC-22 |
| F23 | `openclaw.json` deleted between detect and write | `set_provider()` recreates it. `openclaw.config_recreated` event. | F-CFG-23 |
| F24 | Crash between writing provider 1 and provider 2 | Each `set_provider()` is atomic + idempotent. Re-run completes. | IDEM-24 |

### Skill-install-stage (F30–F35)
| ID | Failure | Recovery | Test |
|---|---|---|---|
| F30 | `~/.openclaw/workspace/skills/` doesn't exist | `mkdir(parents=True, exist_ok=True)`. | U-SKL-30 |
| F31 | Skill folder exists with stale content | **Overwrite** (per L3). SHA-256 compare → replace whole folder if any diff. | F-SKL-31 |
| F32 | Skill folder owned by other UID/GID | Refuse. `openclaw.skill_foreign_owner` event. Lock core succeeds. | F-SKL-32 |
| F33 | Disk full mid-copy | **Stage-then-rename**: tempdir under `skills/.worthless.tmp.<pid>/`, then rename. | INJ-33 |
| F34 | Folder is a symlink | Refuse. | F-SKL-34 |
| F35 | Case-insensitive FS collisions (macOS APFS) | `Path.resolve()` canonicalize before all comparisons. | F-SKL-35 |

### Cross-stage (F40–F47)
| ID | Failure | Recovery | Test |
|---|---|---|---|
| F40 | `.env` succeeded, `openclaw.json` failed | Per L1/L2: surface in `--json`, exit 0. Re-run idempotent. | F-XS-40 |
| F41 | `.env` + config OK, skill failed | Same. | F-XS-41 |
| F42 | SIGTERM/SIGKILL between phases | Each phase atomic; next run reconciles via idempotency. No journal. | IDEM-42 |
| F43 | Daemon hot-reloads on `os.replace` | Desired. Emit `openclaw.config_updated` for log readability. | F-XS-43 |
| F44 | Config orphan (no daemon running) | Not our problem. Doctor surfaces hint. | F-XS-44 |
| F45 | Two `lock` invocations parallel (two terminals) | Phase 1 flock + skill-install flock at `~/.openclaw/workspace/skills/.worthless.lock`. | CONC-45 |
| F46 | `unlock` runs while `lock` mid-flight | flock serializes. | CONC-46 |
| F47 | `sudo -E` HOME mismatch | Detect via `getpwuid(geteuid()).pw_dir`. Emit `openclaw.home_mismatch`. | F-XS-47 |

### Platform (F50–F53)
| ID | Failure | Recovery | Test |
|---|---|---|---|
| F50 | Native Windows | Already refused (WRTLS-110). No code path. | n/a |
| F51 | WSL with Windows-side OpenClaw | Out of scope; do NOT probe `/mnt/c`. | F-PLAT-51 |
| F52 | macOS Apple Silicon vs Intel | No path difference. | F-PLAT-52 |
| F53 | Linux with `XDG_CONFIG_HOME` | Phase 1 already probes `~/.config/openclaw/` fallback. | F-PLAT-53 |

---

## Sub-phases (atomic commits, in order)

### Phase 2.0 — Invariant gate (NO CODE)

- Re-run Phase 1's full test suite — confirm 27/27, 93% coverage.
- Read `engineering/research/openclaw.md` §Corrections.
- Read `engineering/research/openclaw-WOR-431-skill-authoring.md`.
- Confirm `_PROVIDER_ENV_MAP` in `wrap.py` matches the providers we'll write.
- **Output:** one-paragraph note in PR description.

### Phase 2.a — Module skeleton + detection (TDD)

**Files NEW:**
- `src/worthless/openclaw/integration.py` — `detect()`, dataclass `IntegrationState`. Pure functions, no CLI imports.
- `src/worthless/openclaw/skill.py` — `install()`, `uninstall()`, `current_version()`. Reads `skill_assets/` via `importlib.resources.files()` (Python 3.12+).
- `src/worthless/openclaw/skill_assets/__init__.py` — empty.
- `src/worthless/openclaw/skill_assets/SKILL.md` — placeholder (Phase 3 owns content).
- `src/worthless/openclaw/errors.py` — `OpenclawErrorCode` enum, `OpenclawIntegrationEvent` dataclass.

**Tests added (unit only, no real FS):** U-DET-01, U-DET-02, U-CFG-10, U-CFG-11, U-CFG-12, U-CFG-14, U-SKL-30.

**Deliverable:** `from worthless.openclaw import integration; integration.detect()` returns `IntegrationState` without mutating anything.

### Phase 2.b — `lock` integration (closes WOR-321)

**Files MODIFIED:**
- `src/worthless/cli/commands/lock.py` — extend after `_batch_rewrite` succeeds: `try: integration.apply_lock(planned_updates, json_events_sink) except OpenclawIntegrationError as e: events_sink.append(...)` — never re-raise into lock-core path.
- Add `--json` to lock if not already there (Phase 1 may have added it).

**Tests added:** F-CFG-13, F-CFG-15, F-CFG-16, F-CFG-23, F-XS-40, F-XS-41, F-XS-43, IDEM-24, IDEM-42.

**Deliverable:** `worthless lock` on host with OpenClaw produces populated `openclaw.json` + installed skill. On host without, byte-identical to Phase 1.

### Phase 2.c — `unlock` integration

**Files MODIFIED:**
- `src/worthless/cli/commands/unlock.py` — invoke `integration.apply_unlock(aliases_being_unlocked, events_sink)`. Order independent of `_unlock_alias`.

**Tests added:** RT-01, RT-02, RT-03, F-XS-46.

**Deliverable:** `lock` → `unlock` leaves zero residue. `worthless doctor` reports clean.

### Phase 2.d — `doctor` extension

**Files MODIFIED:**
- `src/worthless/cli/commands/doctor.py` — `_check_openclaw()` row group: skill present + version match, `worthless-*` providers in config, baseUrl matches running proxy port, daemon PATH includes `worthless`.
- `--fix` mode: re-invoke `apply_lock` for known aliases missing from config; re-install skill if missing.

**Tests added:** U-DOC-01..05.

### Phase 2.e — Concurrency + injection harness (TESTS ONLY)

**Files NEW:**
- `tests/openclaw/test_integration_concurrency.py` (multiprocessing — 50 iter)
- `tests/openclaw/test_integration_injection.py` (mock.patch on `os.replace`, `Path.write_text`, our copy helper)
- `tests/openclaw/test_integration_idempotency.py`
- `tests/openclaw/test_integration_round_trip.py`

**Tests added:** CONC-22, CONC-45, CONC-46, INJ-20, INJ-21, INJ-33, IDEM-24, IDEM-42, RT-01, RT-02, RT-03.

### Phase 2.f — Documentation + Linear sync

- `engineering/research/openclaw.md` — append §Phase-2-implementation-notes (rollback decision, idempotency rationale).
- `.claude/module_ir.md` — add `openclaw/` modules entry.
- Linear: close WOR-321 as duplicate; update WOR-431 with executive summary linking back to this spec file.

---

## Test matrix

### Unit (no I/O, pure)
U-DET-01, U-DET-02, U-CFG-10, U-CFG-11, U-CFG-12, U-CFG-14, U-SKL-30, U-DOC-01..05.

### Functional (real tmp_path, real FS)
F-DET-02, F-DET-03, F-DET-04, F-DET-36, F-CFG-13, F-CFG-15, F-CFG-16, F-CFG-23, F-SKL-31, F-SKL-32, F-SKL-34, F-SKL-35, F-XS-40, F-XS-41, F-XS-43, F-XS-44, F-XS-47, F-PLAT-51, F-PLAT-52, F-PLAT-53.

### Failure injection (mock-patched)
INJ-20 (EACCES on `os.replace`), INJ-21 (ENOSPC on `os.fsync`), INJ-33 (mid-copy raise; staging dir cleaned).

### Concurrency (multiprocessing)
CONC-22 (flock timeout), CONC-45 (50× parallel `lock`), CONC-46 (`lock` vs `unlock` race).

### Idempotency
IDEM-24 (lock twice = same state), IDEM-42 (SIGKILL between phases → next run reconciles).

### Round-trip
RT-01 (lock → unlock = byte-identical pre-lock state), RT-02 (lock A → lock B → unlock A; B survives), RT-03 (lock → manually delete config → unlock; emits `openclaw.config_missing`).

### Real-container e2e — DEFERRED to WOR-432
E2E-01: spin daemon, run `lock`, send request, verify proxy hit. Out of scope here.

---

## Risk register

| ID | Severity | Risk | Mitigation |
|---|---|---|---|
| R1 | HIGH | Stage-3 failure → user thinks lock fully worked, hits OpenClaw error later | `--json` surfaces; `doctor` shows; F-XS-40 enforces |
| R2 | HIGH | Foreign-UID skill folder silently overwritten or skipped | Refuse + emit `openclaw.skill_foreign_owner`; F-SKL-32 |
| R3 | LOW | Daemon reads torn config | Phase 1 atomic-write contract; regression test |
| R4 | MEDIUM | Phase 1 flock timeout never fires → hang | Explicit 5s timeout; CONC-22 |
| R5 | LOW | Embedded SKILL.md cached by `importlib.resources` | Use `importlib.resources.files()`; U-SKL-30 |
| R6 | MEDIUM | `worthless-*` collision with user's manual provider | F-CFG-13: detect, refuse, surface conflict |
| R7 | HIGH | Probe `/mnt/c` on WSL → broken atomic-write | Phase 1 doesn't probe; F-PLAT-51 enforces |
| R8 | MEDIUM | macOS case-insensitive FS divergence | `Path.resolve()` before compare; F-SKL-35 |
| R9 | MEDIUM | `--json` schema drift across `lock`/`unlock`/`doctor` | Single Pydantic `OpenclawIntegrationReport` model in `errors.py` |
| R10 | LOW | Phase 3 SKILL.md changes break Phase 2 install | Phase 2 install is content-agnostic — copies whole `skill_assets/` dir |
| R11 | LOW | New provider in `_PROVIDER_ENV_MAP` without integration mapping | Unit test asserts every key has matching `worthless-<key>` entry |
| R12 | LOW | Adversarial 1GB `openclaw.json` DoS | 10MB sanity cap in `read_config`; new test U-CFG-13b |

---

## Verification (how Jenny / karen / tdd-guide check this)

**Goal-backward verification questions:**

1. *"On a fresh OpenClaw container with no skill installed, does running `worthless lock` against a `.env` produce a working setup?"* → AC2.
2. *"After `unlock`, is `openclaw.json` byte-identical to before `lock`?"* → AC7 (RT-01).
3. *"If I poison openclaw.json mid-write, does the user still get `.env` protection?"* → AC11 (F-XS-40, INJ-20).
4. *"Are all 53 failure modes tested?"* → AC5 (test docstrings reference F-IDs).
5. *"Is the JSON output stable enough for Pi to parse?"* → AC6 (Pydantic schema test).

**Run commands:**

```bash
cd /Users/shachar/Projects/worthless/worthless-wor421-openclaw
uv run pytest tests/openclaw/ -v --cov=worthless.openclaw --cov-report=term-missing
uv run ruff check src/worthless/openclaw/ src/worthless/cli/commands/{lock,unlock,doctor}.py tests/openclaw/
uv run pytest tests/test_openclaw_config.py  # Phase 1 regression — must stay green
```

**Manual smoke (50 min, before merge):**

1. Pull `ghcr.io/openclaw/openclaw:latest`, bind-mount a fresh tempdir as openclaw home.
2. Run `worthless lock` against a fixture `.env` — assert openclaw.json gets `worthless-openai` baseUrl + skill folder appears.
3. `docker exec openclaw openclaw skills check --json` — assert `worthless` present.
4. `docker exec openclaw openclaw agent --local --json --message "test prompt"` — assert mock-upstream got the real key, shard-A absent.
5. `worthless unlock` — assert openclaw.json reverted, skill folder removed.

---

## Out of scope (explicit)

- Authoring `SKILL.md` content (Phase 3, separate ticket).
- Real-container e2e (WOR-432).
- A new `worthless openclaw` namespace.
- Rolling back `.env`/DB on OpenClaw stage failure (per L1).
- Supporting OpenClaw-on-Windows-host + worthless-in-WSL.
- `--openclaw` / `--no-openclaw` flags. Behavior is automatic.

---

## Implementation log

Tracks deviations from spec discovered during execution. Each entry: what
was discovered, when (commit), and how it was reconciled.

### Phase 2.a — module skeleton + detect()

**Status:** ✅ Shipped on `feature/wor-421-openclaw-research-doc` over commits
`fdbdf7a` → `967f441`. PR [#143](https://github.com/shacharm2/worthless/pull/143)
(draft).

**Tests:** 63/63 pass (35 Phase 2.a + 27 Phase 1 regression + 1 frontmatter
regression). Coverage 92% on new modules. Ruff + GHAS clean. CI green.

**Deviations from spec:**

1. **SKILL.md frontmatter ADDED in Phase 2.a (was Phase 3 territory).**
   The Phase 2.a placeholder originally had only a `Version:` line; live
   testing against `ghcr.io/openclaw/openclaw:latest` showed OpenClaw silently
   ignores files without YAML frontmatter, breaking the discoverability
   precondition for Phase 2.b. Minimum-viable frontmatter (name, description,
   `metadata.openclaw.requires.bins:[worthless]`) shipped early; Phase 3
   replaces the body content but keeps the frontmatter shape. Regression
   test `test_skill_md_has_minimum_yaml_frontmatter_for_openclaw_discovery`
   pins the minimum keys.

2. **`pyproject.toml` `[tool.setuptools.package-data]` ADDED.** Latent bug
   pre-dating Phase 2.a: without `package-data`, SKILL.md gets dropped from
   built wheels. Spec didn't call this out; live wheel build would have
   regressed silently. Now declared for `worthless.openclaw.skill_assets`.

3. **`importlib.resources` SWAPPED for `Path(__file__).parent`.** GitHub's
   hosted Semgrep app refuses to honor inline `# nosemgrep` directives for
   the `python37-compatibility-importlib2` rule; rather than fight per-line
   suppression, swapped to an equivalent `Path(__file__).parent / "skill_assets"`
   approach. Same correctness for source + wheel installs; eliminates the
   false-positive lint hit. Documented in commit `1d6fb25`.

**Bugs filed during cleanup (beads):**

- `worthless-u7hk` (P1): `worthless revoke` leaks Fernet key in keychain.
  Discovered while cleaning up Phase 2.a test residue. NOT introduced by
  this work — pre-existing. Surfaced in WOR-421 epic comment for visibility.
- `worthless-wca6` (P2): tests accumulate keychain entries; needs
  `conftest.py` finalizer.
- `worthless-rxi2` (P1): SKILL.md missing frontmatter (above). **Closed —
  fixed in `967f441`.**
- `worthless-3jsd` (P3): `IntegrationState.notes` always empty.
  **Closed — not a bug**; notes are populated only on failures, empty on
  benign absence (per docstring intent).

**Failure-mode coverage as of Phase 2.a end:** 12/53 covered with tests
(F01–F04, F36, F30–F35, U-CFG-10/11/12/14). Remaining 41 are correctly
deferred to Phases 2.b/2.c/2.e per spec §"Sub-phases".

**AC status:** AC9 ✅, AC12 ✅, AC13 ✅, AC14 🟡 (WOR-321 closed Duplicate,
Linear desc updated with epic comment but not bulk-rewritten yet — addressed
by this very commit). All other ACs correctly deferred.

### Phase 2.b — `lock` integration (NEXT)

**Status:** ⏳ Not started. TDD pattern from Phase 2.a will continue:
write failing tests for the new `apply_lock()` integration first, then
extend `cli/commands/lock.py` to call it.

### Phase 2.c — `unlock` integration

⏳ Pending Phase 2.b.

### Phase 2.d — `doctor` extension

⏳ Pending Phase 2.b.

### Phase 2.e — concurrency + injection harness

⏳ Pending Phases 2.b/2.c.

### Phase 2.f — docs + Linear close-out

⏳ Pending. Spec lives in this file (durably in git as of this commit).
