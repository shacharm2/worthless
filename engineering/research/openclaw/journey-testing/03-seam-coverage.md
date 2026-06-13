# 03 — The Worthless Seam: Live-vs-Hermetic Coverage Matrix

> **Dimension:** what must be tested LIVE through OpenClaw vs what can be tested
> hermetically against the provider protocol, given worthless is **client-agnostic**.

## Sources read (git HEADs pinned)

- `engineering/openclaw-integration-reference.md` (the seam map).
- worthless: `src/worthless/proxy/app.py`, `proxy/rules.py`, `proxy/metering.py`,
  `openclaw/integration.py`, `adapters/{openai,anthropic}.py` — branch
  `feature/wor-715-lock-captures-mode` (this worktree).
- OpenClaw clone `~/Projects/worthless/openclaw` HEAD
  **`2eae30e779cb694b776ba1f52bd24c644cbdd919`** = tag **`v2026.5.3-1`** (matches the
  reference doc's pin exactly — every `file:line` in the seam map is live-accurate).

## The load-bearing distinction

The proxy (`app.py`) has **zero** branching on the calling client. A request from
OpenClaw, a curl script, and the Anthropic SDK take the *identical* code path
(`proxy_request`). The only branches are **provider**-shaped:

- metering parse — `app.py:471-474` (`provider == "anthropic"` → `extract_usage_anthropic`).
- error-shape / sanitize — `app.py:127-164`, `errors.py`.
- upstream URL — per-enrollment `base_url` column (registry `lookup_by_name`).

**Corollary that drives this whole matrix:** every behavior worthless *owns* is a
function of the **request bytes + provider**, never of "who sent it." Therefore
anything provable by *constructing the request bytes* is hermetic. The only thing
that genuinely needs a live OpenClaw is the production of those bytes — i.e. the
behaviors that live **inside OpenClaw's own source** (model resolution, the
`compat.*` gates, the catalog-merge baseUrl precedence). Those are *OpenClaw*
behaviors worthless depends on but does not implement, so a unit test of worthless
code can never catch them moving.

Three tiers used below:

- **H = Hermetic** — pure unit, or against the in-repo mock upstream
  (`tests/openclaw/mock-upstream/app.py`). No OpenClaw process. Fast, runs every commit.
- **C = Container** — needs the real **OpenClaw container** (config + agent loop)
  but a **mock upstream**. Proves OpenClaw shapes/routes the request as the seam map claims.
- **L = Live upstream** — needs a **real provider** (OpenAI / OpenRouter / Anthropic).
  Reserved for things only the real provider's wire contract can confirm.

---

## Coverage matrix

| # | Seam behavior | Owner | Tier | Why this tier | Existing worthless coverage |
|---|---|---|---|---|---|
| 1 | **Model-ref parse** (split on first `/`; `openai` + `liquid/lfm-…` → model under openai) | OpenClaw `validation.ts:1165` | **C** | Pure OpenClaw string logic; worthless never parses model refs. A unit test would only re-implement OpenClaw, not catch it drifting. Needs the agent to actually resolve the ref. | None direct. `test_routing_contract.py` exercises *routing* after the ref resolves, not the parse itself. **GAP.** |
| 2 | **"Unknown model" rejection** (model absent from a configured provider's catalog → rejected *before* the proxy) | OpenClaw `validation.ts:1220` | **C** | The rejection happens entirely inside OpenClaw; the request never reaches worthless. Cannot be observed hermetically — there is no worthless code in the path. The *signal* is "proxy sees zero requests." | None. **GAP** (this is exactly the human-GUI-only class of breakage). |
| 3 | **`supportsTools:false` / 404-no-tool-endpoint** (agent always sends `tools` unless compat clears it) | OpenClaw `model-tool-support.ts:6` | **C** (404 surface) + **H** (passthrough) | Two halves: (a) *that the agent emits/omits `tools`* is OpenClaw behavior → C. (b) *that worthless forwards a `tools` body byte-identical and passes a 404 through* is worthless → H against mock (`bad-model`→404). | **Partial.** `test_adapters.py` proves tools body passes through byte-identical + 404 error sanitize in `test_proxy.py`. The *agent emits tools* half is uncovered. |
| 4 | **`requiresStringContent:true` / 400-array-content** (compat flattens `[{type:text}]` → string) | OpenClaw `openai-transport-stream.ts:1856` | **C** (flatten) + **H** (400 passthrough) | The *flatten* is OpenClaw's; worthless forwards the body verbatim. Only the agent applying the compat flag can be tested live; worthless's job (forward + relay the 400) is hermetic. | **Partial.** Body-verbatim forward + upstream-error sanitize are H-covered. The compat flatten is **GAP**. |
| 5 | **Catalog-merge baseUrl gotcha** (editing `openclaw.json` baseUrl alone is overridden by `agents/main/agent/models.json`) | OpenClaw `schema.base.generated.ts:1520/1529` | **C** | This is a *config-precedence* fact: only a running OpenClaw resolving its merged config reveals which baseUrl wins. No worthless code models the merge. | **Covered (C).** `test_routing_contract.py` cases `openai_stale_models_json`, `openai_replace_mode`, `openai_per_model_baseurl` directly assert the proxy (B) is hit, not the stale A. Strongest existing live test. |
| 6 | **Provider routing → upstream URL** (per-enrollment `base_url` → registry host) | worthless `lock.py:_resolve_upstream_base_url`, `app.py` | **H** | Pure worthless: alias→row→base_url→forward. Provable with mock upstream + a hand-built request. | **Covered (H).** `test_openclaw_e2e.py` (mock stack) asserts the request reaches the registered mock URL; adapter/registry unit tests. |
| 7 | **Usage metering — OpenAI vs Anthropic** (per-provider parse; streaming + non-streaming; cache tokens) | worthless `metering.py`, `app.py:471` | **H** | Parsing a known response body is a pure function of (bytes, provider). The mock can emit every shape (incl. `cache-hit`, no-usage-in-stream). OpenClaw irrelevant. | **Covered (H), strong.** `test_metering.py` (17 cases: JSON/SSE/multi-delta/missing/malformed), `test_streaming_metering.py`. Mock emits `cache-hit`/`include_usage` variants. **Caveat:** see WOR-730 below — reservation precision is a *different* axis from post-hoc metering and is thinner. |
| 8 | **Upstream error pass-through / sanitize** (allowlist type/code/param; generic message; Anthropic `type:"error"` sentinel) | worthless `app.py:127-164` | **H** | Sanitization is a pure transform of the upstream body + provider. Mock returns OpenAI 404 / Anthropic 400 shapes on `does-not-exist`. | **Covered (H).** `test_proxy.py` / `test_error_metering_and_hardening.py`. *Note:* WOR-729 (collapses actionable upstream message) is a known **product** gap, not a test gap — and it's exactly what made the free-model debug multi-hour. |
| 9 | **Spend-cap gate-before-reconstruct** (rules engine denies BEFORE any IPC decrypt / key material) | worthless `app.py:373`, `rules.py` SpendCapRule | **H** | The single most security-critical invariant, and entirely worthless-internal: denial path places no IPC call. Provable with a unit harness asserting the sidecar mock is never touched. Client identity is irrelevant by construction. | **Covered (H).** Rules unit tests + proxy gate tests. SR-03 is the project's most-tested invariant. Does **not** need OpenClaw. |
| 10 | **pee0 first-use cached-credential bypass** (key resolved from `agents/<id>/agent/models.json` / `auth-profiles.json`, not the locked config) | OpenClaw `model-auth.ts:173`, `models-config.ts:184`, `auth-profiles/store.ts` | **C** | This is the one bypass that *defeats* worthless: OpenClaw reads a plaintext key from a cache the lock never rewrote, sending the REAL key straight to the provider — the proxy sees nothing. Only a running OpenClaw with a primed cache can demonstrate the request skipping the proxy. F1 does not neutralize it. | **None. Highest-value GAP.** No test asserts "after lock, a primed `models.json` cache still routes through the proxy (or fails closed)." This is a security regression detector, not just an integration one. |

---

## The irreducible live/container surface

Pulling out the rows that **cannot** be proven hermetically because the behavior
lives in OpenClaw's source, not worthless's:

1. **Model-ref parse + Unknown-model rejection (#1, #2)** — the request is killed
   *inside* OpenClaw before any worthless code runs. The only observable is
   "proxy received zero requests" → must drive the agent.
2. **`compat.supportsTools` / `requiresStringContent` request shaping (#3, #4, agent half)**
   — whether the agent emits `tools` / array-content is OpenClaw logic gated on
   `compat.*`. The *worthless* half (forward verbatim, relay 404/400) is hermetic;
   the *emission* is not.
3. **Catalog-merge baseUrl precedence (#5)** — pure OpenClaw config resolution.
4. **pee0 cached-credential bypass (#10)** — the security-critical one. Requires a
   running OpenClaw whose credential cache is primed, to prove the locked path is
   (or isn't) actually load-bearing.

Everything else (rows 6–9, and the worthless half of 3/4) pulls down to fast
hermetic tests, because by the client-agnostic invariant worthless's behavior is a
pure function of **(request bytes, provider)** — and a test can synthesize those
bytes without OpenClaw.

**Minimal container suite (the WOR-732 lane):** four agent-driven cases against the
**mock upstream** suffice to cover the irreducible surface — one per failure-ladder
rung (Unknown-model, 404-no-tools, 400-array-content, dead-baseUrl-via-merge) plus
the pee0 bypass as a fifth, security-flavored case. A real **live upstream is not
required** for any of them: the mock already simulates 404/400/5xx/cache by model-name
convention, and the failure ladder's rungs are about *request shaping*, which the
mock observes by capturing headers/body. Live upstream (tier L) is only needed to
re-validate the mock's fidelity (a periodic "is the mock still shaped like the real
provider?" guard), not for the journey itself.

## Where existing tests already cover the seam

- **Strong hermetic:** metering (#7), error sanitize (#8), gate-before-reconstruct
  (#9), provider routing to upstream (#6) — `test_metering.py`,
  `test_streaming_metering.py`, `test_proxy.py`, `test_error_metering_and_hardening.py`,
  `test_openclaw_e2e.py` (mock stack), `tests/test_adapters.py`.
- **Strong container:** catalog-merge baseUrl authority (#5) — `test_routing_contract.py`
  pins it to image `2026.5.3-1` with stale-models.json / replace-mode / per-model cases.
- **Partial:** request-shaping passthrough (#3/#4 worthless half) covered; the
  **agent-emits** half not.

## Recommended additions (gaps, ranked)

1. **pee0 cached-credential bypass guard (#10)** — container test: prime
   `agents/main/agent/models.json` with a plaintext key, run lock, drive a chat,
   assert the proxy saw the request (or the call fails closed). *Security regression
   detector.* Highest value.
2. **Failure-ladder agent suite (#1–#4)** — four container cases against the mock,
   each asserting the documented symptom appears/disappears as the matching
   `compat`/catalog piece is toggled. This is the WOR-732 "version-bump fails a test,
   not production" lane, extended from routing-only to full request-shaping.
3. **Reservation-estimate precision (WOR-730, hermetic)** — `_estimate_tokens`
   reads only `max_tokens`; OpenClaw sends `max_completion_tokens` /
   `max_output_tokens`. Add unit cases so the hard-cap reservation doesn't silently
   under-reserve. Pure unit, no OpenClaw.
