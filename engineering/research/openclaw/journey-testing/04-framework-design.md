# Journey Testing — Framework Design (live vs not-live)

> **Dimension:** the actual CI test framework that would have caught the GUI-only breakage automatically.
> **Sources of truth:** `engineering/openclaw-integration-reference.md` (the seam map),
> `tests/test_openclaw_e2e.py` + `tests/openclaw/docker-compose.yml` (the rig that already exists),
> `pyproject.toml` markers, `scripts/verify-live-rig.sh` (the pre-flight).

## Pinned coordinates (record what was read)

| Component | Version | HEAD read |
|---|---|---|
| OpenClaw source clone | `2026.5.3-1` | `2eae30e779cb694b776ba1f52bd24c644cbdd919` (detached, `~/Projects/worthless/openclaw`) |
| worthless worktree | `0.3.7` | branch `feature/wor-715-lock-captures-mode` (this repo) |
| Existing OC rig | mock-upstream + worthless-proxy compose, OC gateway behind `--profile openclaw` | `tests/openclaw/docker-compose.yml` |

The clone, the seam-map doc, and the running container are the same OpenClaw build, so the
`file:line` surfaces in the seam map are live-accurate for this design.

---

## 1. THE USER JOURNEY as a testable sequence

The breakage that only a human-in-the-GUI caught is the *chain* — each individual worthless unit
test passed, but no test crossed all the seams in one run. Define the journey as 8 ordered assertion
points (AP). Each AP is independently assertable; a lane "covers" an AP if it can fail when that AP
breaks.

| AP | Step | What is asserted | Failure mode this catches (from the ladder) |
|----|------|------------------|----------------------------------------------|
| **AP1** | `worthless lock --env` rewrites config | OpenClaw `openclaw.json` **and** `agents/main/agent/models.json` both get the proxy `baseUrl` (merge gotcha) | Dead `baseUrl` — editing one file only → silent override → "network connection error" |
| **AP2** | Provider/model registered in OC config | Provider entry has `required:["baseUrl","models"]`; model item `required:["id","name"]` present | Schema-invalid config → OC won't load provider at all |
| **AP3** | Agent resolves model ref | `parseProviderModelRef` (split on first `/`) maps `openrouter/liquid/lfm…:free` → provider `openrouter`, model `liquid/lfm…:free`; model is in the configured provider's catalog | **"Unknown model"** rejection (`validation.ts:1220`) — *before* any request leaves OC |
| **AP4** | Request shaped | `tools` omitted when `compat.supportsTools:false`; array content flattened to string when `compat.requiresStringContent:true` | **404 no-tool-endpoint**; **400 array-content** |
| **AP5** | Request hits proxy gate | Proxy receives body, runs rules engine **before** reconstruct (gate-before-reconstruct invariant) | Spend-cap not enforced / gate bypassed |
| **AP6** | Proxy → upstream | Real key reconstructed into auth header, body forwarded **verbatim**, correct upstream URL by **registry name** (not wire protocol) | OpenRouter key sent to `api.openai.com` → 401 (PR #276 regression) |
| **AP7** | Content returns | A non-empty assistant message round-trips back through proxy → OC → surface | "Everything wired but chat returns nothing" — the human-only signal |
| **AP8** | Spend metered | Per-provider usage parsed (`extract_usage_anthropic`/`_openai`), reservation + post-hoc count recorded | Metering miss (WOR-730/731 class) |

**The central split (client-agnostic / provider-aware):**
- AP1–AP4 are **OpenClaw-specific** (config rewrite + request shaping). worthless does not own these;
  a version bump moves them. These need **live or container-of-OpenClaw** coverage.
- AP5, AP6, AP8 are **provider-protocol-generic** (client-agnostic). A hand-built request reproduces
  them exactly → **hermetic** coverage is sufficient and authoritative.
- AP7 is the integration of both — only fully proven when a real agent drives a real request end to end.

> Corollary from the seam map: if AP1–AP4 pass (request well-formed leaving OC) and AP5–AP8 fail,
> the bug is worthless-side; otherwise it is OC-side. The lane design encodes this so a red test
> *tells you which side*.

---

## 2. TEST LANES (explicit tradeoffs)

### Lane A — HERMETIC contract lane  (per-PR, blocking)
**Covers:** AP1, AP2, AP3, AP4 (OC-side, *as worthless models them*) + AP5, AP6, AP8 (worthless-side).
**No Docker, no OpenClaw, no network.** Pure pytest. This is the lane that turns the failure ladder
into fast assertions.

Three test groups:

1. **Failure-ladder fixtures** — golden config + golden request bodies encoding each rung:
   - *Unknown model*: assert worthless writes the model id into the provider catalog so OC's
     `validation.ts` check would pass. Test the **worthless writer**, not OC: given a lock for
     `openrouter`, the resulting `models[]` contains the exact id, and the agent default ref is
     `openrouter/<id>`.
   - *404 no-tools* / *400 array-content*: assert the compat-flag matrix — worthless's config writer
     emits `compat.supportsTools` / `compat.requiresStringContent` for the recipes that need them.
     Drive it as a table: `(provider, model, expect_supportsTools, expect_requiresStringContent)`.
   - *Dead baseUrl (merge gotcha)*: assert the writer touches **both** `openclaw.json` and
     `agents/main/agent/models.json` with the same proxy `baseUrl`. This is the single highest-value
     hermetic test — it is the one failure no unit test currently isolates.
2. **OC config-schema contract tests** — pin the OC `schema.base.generated.ts` constraints worthless
   depends on as JSON-schema fixtures checked into `tests/openclaw/contracts/`
   (`provider.required`, `model.required`, the `compat` field names, the `const:"merge"` +
   "preserve non-empty agent models.json baseUrl" rules). Validate worthless's *emitted* config
   against these schemas with `jsonschema`. When OC bumps and a constraint moves, the contract test
   fails with a diff — exactly the seam-map "re-verify" loop, automated.
3. **Worthless-side request lifecycle** — RESPX-backed (already in `test` deps): feed the golden
   request bodies through the proxy app in-process, assert gate-before-reconstruct, verbatim
   forward, registry-name URL resolution (the 401 regression), and usage extraction. These are
   `contract`-marked and reuse the existing proxy test harness.

| Tradeoff | Value |
|---|---|
| Speed | < 5 s, in the default `pytest` run |
| Flakiness | ~0 (no I/O) |
| Cost | $0 |
| Determinism | Total |
| **Gap** | Tests worthless's *model* of OC, not OC itself. A logic error in OC's parser that worthless's fixtures also encode wrongly is invisible here — that is what Lane B catches. |

### Lane B — CONTAINER + MOCK-UPSTREAM lane  (per-PR if affordable, else nightly; blocking on `main`)
**Covers:** AP1–AP8 end to end, deterministic & free. The real OpenClaw container + real worthless
proxy + a **fake** OpenAI/Anthropic server (the existing `mock-upstream`).

Build on the rig that already exists (`tests/openclaw/docker-compose.yml`): it already runs
`mock-upstream` + `worthless-proxy` and has the OpenClaw gateway gated behind `--profile openclaw`.
This lane *activates that profile* and drives the agent.

Driving the agent (depends on Dimension 2's headless findings):
- **Preferred:** headless agent invocation if OC exposes a non-GUI chat entrypoint (CLI/HTTP). One
  request → assert content returns (AP7) and mock captured the real key + well-formed body (AP4, AP6).
- **Fallback:** the Lit-web-component GUI technique — Playwright drives the dev-gui:
  `preview_fill` the chat input → `dispatchEvent('input')` (Lit needs the event, not just value set)
  → click send → poll for the assistant message node. This is the literal automation of the
  human-only path that found the bug.

The mock asserts the seam from the **upstream** side: it captures every auth header (proves the real
key, not shard-A, arrived → AP6) and can return the failure-ladder bodies on demand
(`does-not-exist` → 404/400 convention already in `mock-upstream/app.py`; extend with a
`no-tools`/`array-rejected` trigger model to exercise AP4 negatively).

**Non-flaky rules (mandatory):**
- Health-gate every dependency (`depends_on: condition: service_healthy`) — already done for proxy.
  Add an explicit "OC loaded provider" probe before sending the first message (poll OC's
  models/health endpoint until the locked provider appears — closes the AP2/AP3 race).
- Dynamic host ports (already done) — no fixed-port collisions across parallel CI jobs.
- Pin the OC image by **digest**, not `latest`. The compose file currently uses
  `ghcr.io/openclaw/openclaw:latest`; change to `:2026.5.3-1@sha256:…` so a silent OC release
  cannot flip CI red overnight. (This is also what makes the seam map's line numbers trustworthy.)
- Bounded polling with explicit timeouts + log capture on failure (the fixture already dumps
  `docker logs` on non-healthy — extend to dump OC logs + the mock's captured bodies as artifacts).
- One-request-per-test, fresh project name per run (`openclaw-e2e-{uuid}`) — already the pattern.

| Tradeoff | Value |
|---|---|
| Speed | 60–180 s (image pull cached, build cached) |
| Flakiness | Low *if* health-gated + digest-pinned; medium otherwise |
| Cost | $0 (no real provider) |
| Determinism | High (mock is deterministic) |
| **Gap** | Mock ≠ real provider. Real-provider quirks (OpenRouter's `:free` throttling/guardrails, real 402) are invisible — Lane C covers those. |

### Lane C — LIVE SMOKE lane  (opt-in, gated, scheduled — NEVER per-PR)
**Covers:** AP7 against reality — "real chat returns real content through the proxy" — plus the
real-provider-only failure rungs (402 insufficient credits, free-model throttle/guardrail).

Real provider, one free/cheap model (the seam-map recipe:
`liquid/lfm-2.5-1.2b-instruct:free` via OpenRouter). Manual (`uv run test-live`) or scheduled
(nightly cron), **never on every PR**.

**Key-in-CI prohibition (hard rule, from memory `feedback_no_keys_in_ci`):** real API keys NEVER
go into GitHub Actions / any CI secret store — financial + exposure risk. This lane runs
**local-only** (developer machine, key from local keychain) or on a dedicated self-hosted runner
the team controls, gated behind `if: github.event_name == 'workflow_dispatch'` and a manual
approval environment. CI's hosted runners only ever run Lanes A and B (hermetic mocks).

**Pre-flight is mandatory:** `scripts/verify-live-rig.sh` runs first. It greps the F1 signature
(`provider_name = provider`) in both the worktree source and the installed package inside the
container, failing with a rebuild recipe on divergence — this catches the "I tested the wrong code"
trap that made F1 look un-shipped mid-debug. No live assertion is trusted until the pre-flight
passes.

| Tradeoff | Value |
|---|---|
| Speed | 5–30 s per chat, plus rig spin-up |
| Flakiness | Medium-high (provider availability, throttling, balance) — never block PRs on it |
| Cost | Real $ (use free model; cap spend via worthless's own rule) |
| Determinism | Low — assert *liveness* ("non-empty content returned"), not exact text |
| **Gap** | Not reproducible in CI by design; its value is the periodic "reality still matches our mocks" signal. |

### Lane D — GUI e2e (Playwright/preview)  (warranted ONLY as Lane B's fallback driver)
Not a separate lane — it is the *driver* for Lane B when no headless OC entrypoint exists, and the
*driver* for Lane C's live smoke. Justification: the breakage was found by hand-driving the GUI, so
the GUI path must be automatable. But a standalone GUI lane against real providers is the most
expensive + flakiest possible test; do not build one. Reuse the same `preview_fill → dispatch input →
click send → poll for message` routine inside Lanes B and C.

---

## 3. CI WIRING

### pytest marks (extend existing, in `pyproject.toml`)
Already present: `openclaw`, `docker`, `live`, `contract`, `e2e`, `user_flow`. The default `addopts`
already excludes `live` and `docker`. Add **no new marks** — map the lanes onto existing ones:

| Lane | Marks | Default run? |
|---|---|---|
| A (hermetic) | `contract` (+ unmarked unit) | **Yes** — runs in `uv run pytest` |
| B (container+mock) | `openclaw` + `docker` | No — excluded by default `-m 'not docker'` |
| C (live smoke) | `live` (+ `openclaw`) | No — excluded by default `-m 'not live'` |

### GitHub Actions jobs

```
job: lane-a-hermetic     # every PR, blocking
  runs-on: ubuntu-latest
  steps: uv run pytest            # contract + unit, < 1 min
         uv run ruff check .

job: lane-b-container    # every PR if runner budget allows, else nightly + on main
  runs-on: ubuntu-latest          # Docker available on hosted runners
  steps: uv run pytest -m "openclaw and docker" -o addopts="--timeout=300"
  needs: lane-a-hermetic
  # digest-pinned OC image; health-gated; artifacts: docker logs + mock captured bodies

job: lane-c-live         # workflow_dispatch ONLY + nightly schedule; NEVER on pull_request
  runs-on: self-hosted            # team-controlled; key from local keychain, NOT a CI secret
  if: github.event_name == 'workflow_dispatch' || github.event_name == 'schedule'
  environment: live-smoke         # requires manual approval
  steps: ./scripts/verify-live-rig.sh     # pre-flight FIRST
         uv run pytest -m live -o addopts="--timeout=120"
```

Per-PR = Lane A always + Lane B (gate on cost). Nightly = A + B + C. Manual = any, incl. live.

### Artifact capture — the logging-passthrough technique
The proxy **swallows** the upstream's actionable error (`proxy/app.py:130-155`, WOR-729): a billing
402 and a schema 400 both surface as generic `"upstream provider error"`. So a failed Lane B/C test
must capture the *real* upstream body, or it gives a multi-hour-hunt-shaped red.

Insert a **logging passthrough** between OpenClaw and the worthless proxy (the WOR-621 capture
technique): a thin recording proxy that tees the exact request body OC sent + the upstream status/body
to a file, **upstream of** worthless's sanitizer. On test failure, upload as artifacts:
- OC → passthrough: the request body (proves AP4 shaping)
- passthrough → upstream: the real status + body (proves the *actual* failure rung, un-swallowed)
- proxy + OC `docker logs`
- mock's captured auth headers

This converts every red into "which AP, which side, what did upstream actually say" without a human.

### Making Lane B non-flaky (checklist)
- Digest-pin OC image (not `latest`).
- Health-gate proxy + mock (done) + add an "OC loaded the locked provider" readiness probe.
- Dynamic ports + per-run project name (done).
- Bounded polling, no `sleep`-and-hope; explicit timeouts.
- Single request per test; isolate state.
- Always dump artifacts on failure (above).

---

## 4. PHASED ROLLOUT → WOR-732

WOR-732 (*"OpenClaw signature test suite"*, P2, `tests/openclaw/`) is exactly this work: "the
`supportsTools` / `requiresStringContent` / merge-baseUrl knowledge becomes a 3-lane test suite so a
version bump fails a test, not production." Map the phases onto it.

**Phase 1 — Hermetic Lane A (highest value / lowest cost) — do first.**
Encode the failure ladder + the merge-baseUrl gotcha + the compat-flag matrix as fast `contract`
tests, plus the OC schema-contract fixtures in `tests/openclaw/contracts/`. This alone would have
caught the dead-baseUrl and Unknown-model classes per-PR with zero Docker. Closes the bulk of WOR-732.
Dependency: none. Ship as the first PR under WOR-732.

**Phase 2 — Container Lane B with mock-upstream + headless/Playwright driver.**
Activate the `--profile openclaw` path, digest-pin the image, add the OC-readiness probe and the
logging-passthrough artifact capture. Drive headless if Dimension 2 found an entrypoint, else
Playwright GUI fallback. This proves AP7 (content returns) deterministically and free. This is the
piece that actually exercises the real OpenClaw parser, closing the Lane-A "we tested our model of
OC" gap.

**Phase 3 — Live smoke Lane C, opt-in.**
Wire `verify-live-rig.sh` pre-flight + `test-live` runner against the free OpenRouter recipe on a
self-hosted/local runner. Scheduled nightly + manual dispatch. **Key-in-CI prohibition enforced:**
no real key in any hosted-runner secret; live runs only where the team controls the runner and the
key comes from local keychain. Value: the periodic "reality still matches our mocks" canary.

**Sequencing rationale:** Lane A is days of work and catches most of the ladder per-PR — ship it
first for immediate ROI. Lane B is the load-bearing "did the real journey complete" test but needs
the driver decision (Dimension 2) and digest pinning. Lane C is a canary, not a gate — last, opt-in,
and never allowed near CI secrets.

### Follow-up dependencies surfaced
- **WOR-729** (un-swallow upstream error) materially improves Lane B/C debuggability — until then the
  logging-passthrough is *required* for usable failures. Note this as a soft dependency.
- **WOR-730/731** (metering parse: `max_completion_tokens` / `max_output_tokens`) is AP8 — add a
  hermetic Lane A assertion that worthless's reservation reads the field OC actually sends, so a
  metering regression fails per-PR.
