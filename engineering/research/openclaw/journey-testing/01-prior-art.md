# Prior Art: Automatically Testing an AI-Agent Tool's Integration with an LLM Backend

> Research dimension for the WOR-728 journey-test design. Question: how do others auto-test the
> real user journey (config → agent resolves model → request shaped → hits gateway → upstream →
> content returns), and has anyone done it for OpenClaw specifically?

**Provenance.** Web research conducted 2026-06-12. Seam map read:
`engineering/openclaw-integration-reference.md` (OpenClaw pinned `2026.5.3-1` @ `2eae30e779cb694b776ba1f52bd24c644cbdd919`). OpenClaw source clone at `~/Projects/worthless/openclaw`
is the same build. WebFetch is blocked in this environment; page bodies were extracted via WebSearch
result summaries, so deep quotes below are paraphrased from those summaries and should be spot-checked
against the live docs before being treated as load-bearing.

---

## TL;DR for the worthless decision

1. **OpenClaw already ships the harness we were about to invent.** It has three Vitest suites
   (unit/integration, e2e, **live**) plus Docker runners, and the e2e lane boots a **deterministic
   mock OpenAI endpoint** and runs a *real local agent turn* against it
   (`mock-openai/gpt-5.5`, `mock-openai/gpt-5.5-alt`). That is precisely the "config → resolve model
   → shape request → hit endpoint → content returns" journey we want — minus worthless in the path.
   The cheapest journey test is: **point OpenClaw's existing mock-endpoint e2e flow at the worthless
   proxy instead of directly at the mock**, so the request transits the real gate.

2. **The whole "only caught by hand" class is a known anti-pattern with a known fix.** Every mature
   LLM gateway (LiteLLM, Helicone, Portkey docs, OpenClaw's own release checks) converges on the same
   answer: a **real HTTP mock upstream on a real port**, pointed at via `OPENAI_BASE_URL` /
   `ANTHROPIC_BASE_URL`, exercised by a **real client** (not an in-process monkeypatch). The trap you
   hit — bugs only a human GUI driver catches — is exactly what these mock-server-on-a-port designs
   exist to close, because they let the *actual client request-shaping code* run end to end.

3. **The live-vs-hermetic split everyone uses maps cleanly onto worthless's client-agnostic /
   provider-aware invariant.** Hermetic (mock upstream, deterministic) covers the provider-protocol
   surface (request shape, SSE framing, error JSON) — that is most of it. A tiny **live, nightly,
   key-gated, cost-capped** lane covers only what a real provider does that a mock cannot fake
   (real "Unknown model" rejection by OpenRouter, real 404-no-tool-endpoint, real 400-array-content).

---

## 1. OpenClaw itself — does it have a headless / CI / e2e story?

**Yes, a substantial one. This is the most important finding.**

### 1a. Three-tier Vitest suite + Docker runners (the e2e lane is the prize)

OpenClaw's testing docs describe **three Vitest suites of increasing realism and cost** plus a small
set of Docker runners:

- **unit/integration** — in-process.
- **e2e** — boots a Gateway and runs an agent turn against a **mocked OpenAI endpoint**.
- **live** — hits real providers (gated; see below).

The e2e lane is the directly reusable one. Per the docs summaries:

- The Docker/Bash e2e lanes source `scripts/lib/openclaw-e2e-instance.sh` *inside the container* for
  "entrypoint resolution, **mock OpenAI startup**, Gateway foreground/background launch, and readiness
  probes."
- "npm tarball onboarding tests install the packed OpenClaw tarball globally in Docker, configure
  OpenAI via env-ref onboarding, run `doctor`, and **run one mocked OpenAI agent turn**."
- "**Release checks use the deterministic mock provider** with mock-qualified models
  (`mock-openai/gpt-5.5` and `mock-openai/gpt-5.5-alt`) so the channel contract is isolated from live
  model latency and normal provider-plugin startup."
- Helpers pass base64-encoded state (`docker_e2e_test_state_shell_b64`,
  `docker_e2e_test_state_function_b64`) into containers, decoded by the same lib script.

Sources:
[Testing · OpenClaw](https://docs.openclaw.ai/help/testing) ·
[Tests reference](https://docs.openclaw.ai/reference/test) ·
[CI pipeline · OpenClaw](https://docs.openclaw.ai/ci) ·
[QA / E2E automation](https://open-claw.bot/docs/concepts/qa-e2e-automation/) ·
[package.json](https://github.com/openclaw/openclaw/blob/main/package.json)

**Why this matters for worthless:** OpenClaw's e2e already runs the *full client request-shaping path*
(onboarding → config → model resolve → agent turn) and asserts on returned content, against a
deterministic mock. worthless's proxy is OpenAI-protocol-compatible. So the smallest viable journey
test is to **insert the worthless proxy between OpenClaw and that mock** (set the provider `baseUrl`
to `http://127.0.0.1:8787/<alias>/v1`, mock upstream behind the proxy). This exercises the real gate
without a human and without a real key. It also naturally catches the *worthless-side* concerns the
seam map flags (metering parse `proxy/app.py:471-474`, `max_completion_tokens` reservation
`proxy/rules.py:44` / WOR-730, error-swallowing `proxy/app.py:130` / WOR-729).

### 1b. Live suite — exactly the gating model worthless should copy

The **live** suite is gated and cost-aware:

- Run with `pnpm test:live`; requires **API keys and `LIVE=1`** (or provider-specific
  `*_LIVE_TEST=1`) to unskip — otherwise it skips.
- A second knob, **`OPENCLAW_LIVE_MODELS=modern`** (or `all`), is required to *actually* run the model
  matrix; without it `pnpm test:live` stays a "gateway smoke" test only.
- Live tests are **serial (1 worker) by default to avoid rate-limit conflicts**.
- They **prefer live/env API keys over stored auth profiles**, so stale `auth-profiles.json` keys
  don't mask real shell credentials.
- Credential discovery mirrors the CLI: "If the CLI works, live tests should find the same keys."
  Config at `~/.openclaw/openclaw.json` (or `OPENCLAW_CONFIG_PATH`); per-agent auth at
  `~/.openclaw/agents/<agentId>/agent/auth-profiles.json`.

Sources:
[Testing: live suites · OpenClaw](https://docs.openclaw.ai/help/testing-live) ·
[Run OpenClaw tests & benchmarks](https://open-claw.bot/docs/cli/reference/test/)

**Lesson:** OpenClaw's two-knob gate (`LIVE=1` to unskip + `OPENCLAW_LIVE_MODELS` to widen the matrix,
serial, env-keys-first) is a ready-made template for worthless's live lane — and aligns with the repo
rule "never put keys in CI; real-key tests are local-only" (`feedback_no_keys_in_ci`).

### 1c. Headless / non-interactive agent run (drive it without a GUI)

The pain was "only caught by hand in the GUI." OpenClaw can be driven non-interactively:

- **`openclaw onboard --non-interactive --mode local --auth-choice <provider>`** scripts the entire
  setup (the human-only step). Relevant flags: `--secret-input-mode ref` (env-backed key refs instead
  of plaintext — matches worthless's inert-shard-A model), `--gateway-port`, `--gateway-bind loopback`,
  `--install-daemon`, `--skip-bootstrap`. Inline keys without the matching env var fail fast in ref
  mode.
- **`acpx`** — a separate headless CLI client for stateful Agent Client Protocol (ACP) sessions,
  built for agent-to-agent / CI use, with profiles (`--profile ci`) and meaningful exit codes
  (e.g. exit 4 = no active session). This is a candidate for *driving an agent turn from a script*
  in place of GUI typing.

Sources:
[CLI automation · OpenClaw](https://docs.openclaw.ai/start/wizard-cli-automation) ·
[onboard · OpenClaw](https://docs.openclaw.ai/cli/onboard) ·
[Onboarding reference](https://docs.openclaw.ai/reference/wizard) ·
[openclaw/acpx](https://github.com/openclaw/acpx) ·
[acpx CLI.md](https://github.com/openclaw/acpx/blob/main/docs/CLI.md)

**No published worthless-specific OpenClaw journey test was found** — this appears to be greenfield.
But OpenClaw's own e2e/mock harness is the scaffold to build it on, not from scratch.

---

## 2. How comparable gateways / brokers test their *client* integrations

The recurring pattern: **a real fake-OpenAI server + a real client + cost-zero deterministic
fixtures**, plus a thin live smoke lane.

### LiteLLM (closest analogue — it *is* an OpenAI-compatible proxy)

- Tests **monkeypatch external calls** for unit speed, but for proxy/integration realism LiteLLM
  ships a **`mock_response`** mechanism: set `mock_response` in `litellm_params` and the proxy
  validates request/response handling, error flows, and **client integrations at $0**.
- Provides a **hosted fake-OpenAI endpoint** to load-test against, *and* docs to **self-host your own
  fake OpenAI proxy server**.
- A **`network_mock` mode** intercepts outbound requests **at the httpx transport layer** and returns
  canned responses — used to benchmark proxy overhead deterministically.
- Auth tests prove unauthenticated requests are rejected and valid admin keys work — a direct analogue
  to worthless's "gate before reconstruct" assertion.

Sources:
[LiteLLM testing (DeepWiki)](https://deepwiki.com/BerriAI/liteLLM-proxy/5.2-testing) ·
[LiteLLM load test](https://docs.litellm.ai/docs/load_test) ·
[LiteLLM repo](https://github.com/BerriAI/litellm/) ·
[mock_response issue #20969](https://github.com/BerriAI/litellm/issues/20969)

### Helicone / Portkey / OpenRouter / Cloudflare AI Gateway / Langfuse

Less public test-internals detail, but the consistent *guidance* is the lesson:

- **Prefer OpenAI-compatible interfaces so one smoke test runs across multiple gateways**, and
  **test failure behavior, not just happy paths** — "a router only proves its value when a provider
  times out, pricing changes, a model degrades, or a team hits a quota."
  ([LLM Gateway guide](https://klymentiev.com/blog/llm-gateway-guide))
- Promptfoo treats **Cloudflare AI Gateway as just another provider behind a `baseUrl`**, i.e. the
  gateway is tested by swapping the endpoint a normal client points at.
  ([Promptfoo Cloudflare provider](https://www.promptfoo.dev/docs/providers/cloudflare-gateway/))
- Helicone integrates by **swapping the OpenRouter URL for the gateway URL + headers** — same
  "change the base URL, run the real client" testability principle worthless relies on.
  ([Helicone OpenRouter integration](https://docs.helicone.ai/getting-started/integration-method/openrouter))

**How they avoid the "only caught by hand" trap:** by making the gateway a drop-in `baseUrl` and
running an **actual SDK/client request** through it against a fake upstream — never asserting only on
internal function calls. The bug class worthless hit (model-resolve, tool-gate, content-shape) lives
*in the client*, so the test must run the client.

---

## 3. General techniques for testing the real user journey

| Technique | Tooling | Fit for worthless journey test |
|---|---|---|
| **Real fake-OpenAI/Anthropic server on a real port** | `@copilotkit/llmock` → `aimock` (Node, real HTTP, SSE in OpenAI/Anthropic/Gemini/Bedrock formats, fixture-driven, zero-dep); `mock-llm` (dwmkerr); `mock-openai-server`; `MockLLM` (StacklokLabs, YAML fixtures, OpenAI+Anthropic) | **Strong.** A cross-process server is mandatory because OpenClaw and the worthless proxy are separate processes — an in-process interceptor (respx) can't sit between them. Point worthless's upstream at this. |
| **In-process HTTP mock (httpx transport)** | `respx`, VCR/`vcrpy` cassettes, `pytest-recording` | **For worthless-side unit tests only** (e.g. proxy→upstream). Per worthless's TESTING.md provider lane already uses RESPX + Syrupy snapshots. Cannot cover the OpenClaw→proxy hop. |
| **Record/replay golden transcripts** | VCR cassettes (`.yaml` record-then-replay); Syrupy snapshots (already in worthless) | **Strong for the provider-protocol surface.** Record one real OpenRouter free-model exchange (incl. the 404/400 error bodies), replay deterministically in CI. Captures the *exact* failure shapes that bit the WOR-621 debug. |
| **GUI automation of the chat UI** | Playwright (incl. Playwright MCP / Test Agents 1.56 planner-generator-healer); Electron-targeted Playwright | **Use sparingly.** Highest fidelity to "the human driving the GUI," but slow and flaky. OpenClaw's own **non-interactive `onboard` + `acpx` + e2e agent-turn** already drive the journey headlessly, which is cheaper and less brittle than pixel-driving the Electron chat. Reserve Playwright for a single smoke if a GUI-only seam can't be reached headlessly. |
| **Contract tests vs provider OpenAPI** | Prism (mock from OpenAPI), WireMock, Schemathesis (already in worthless's proxy lane) | **Medium.** Good for asserting worthless's *output* request matches OpenAI/Anthropic schemas. Note WireMock's weakness: limited true SSE/streaming — `ai-mocks` (Kotlin) and `aimock` exist specifically because WireMock can't do real SSE. |
| **Mock-server agent-turn (OpenClaw's own pattern)** | OpenClaw `scripts/lib/openclaw-e2e-instance.sh` + deterministic `mock-openai/*` models | **Best fit.** Reuse it; insert worthless between OpenClaw and the mock. |

Sources:
[Mocking OpenAI (laszlo)](https://laszlo.substack.com/p/mocking-openai-unit-testing-in-the) ·
[CopilotKit/llmock](https://github.com/CopilotKit/llmock) ·
[CopilotKit/aimock](https://github.com/CopilotKit/aimock) ·
[dwmkerr/mock-llm](https://github.com/dwmkerr/mock-llm) ·
[StacklokLabs/mockllm](https://github.com/StacklokLabs/mockllm) ·
[mokksy/ai-mocks](https://github.com/mokksy/ai-mocks) ·
[Playwright Test Agents](https://playwright.dev/docs/test-agents) ·
[SSE testing pattern (LangWatch Scenario)](https://langwatch.ai/scenario/examples/testing-remote-agents/sse/)

### The streaming/SSE trap (directly relevant — worthless forwards SSE)

Real bugs in this exact space, several filed against OpenClaw itself:
- OpenClaw issue #32179: **two SSE events concatenated without the `\n\n` delimiter** → LineDecoder
  splits wrong. ([#32179](https://github.com/openclaw/openclaw/issues/32179))
- Anthropic streaming **partial-JSON chunk** failures requiring buffer accumulation
  ([LiteLLM PR #17493](https://github.com/BerriAI/litellm/pull/17493)).
- Anthropic **message/tool ordering** violations under streaming
  ([openai-agents-python #1863](https://github.com/openai/openai-agents-python/issues/1863)).

Implication: the worthless journey test's mock upstream must be able to emit **real SSE with
chunk-boundary control** (split a JSON event across two flushes, concatenate two events) so worthless's
stream-forwarding is proven robust. `aimock`/`ai-mocks` advertise exactly this; a respx-only test
cannot.

---

## 4. Live-vs-hermetic, as the field frames it

| Axis | Hermetic (mock upstream) | Live (real provider) |
|---|---|---|
| **Determinism** | Full. Same input → same output; assert exact request shape, status, body. The consensus: "JSON validity, required-field presence, length constraints are fully deterministic at near-zero CI cost." | Non-deterministic content (temperature/sampling). Must assert *properties* (status code, error shape, "content non-empty"), never exact text. |
| **Cost** | $0. The whole reason fake-OpenAI servers exist. | Real $. Catch token-consumption regressions before they become "unexpected API bills." Gate behind keys + nightly. |
| **Flakiness** | Low. | Higher — rate limits, provider outages, model deprecation. OpenClaw runs live **serial, 1 worker** to dodge rate limits; everyone makes live **skip-by-default**. |
| **What only it can catch** | Provider-protocol shape, metering parse, gate-before-reconstruct, SSE framing. | The WOR-621 failure ladder's *real* triggers: OpenRouter's actual `Unknown model` rejection, real `404 no tool endpoint`, real `400 array content`, real `402 insufficient credits`. A mock only catches these if you *hand-author the fixture* — which means you must have seen the real one once (record/replay). |

General guidance found:
- Non-determinism is *the* core LLM-testing challenge; "testing for exact output equality fails on
  valid responses and passes when a wrong output happens to match." Evaluate output *properties*.
  ([LLM testing tools 2026](https://contextqa.com/blog/llm-testing-tools-frameworks-2026/),
  [contract testing guide](https://testrigor.com/blog/api-contract-testing/))
- Deterministic generation removes flakiness/guesswork → maintainable suites, easier debugging.
  ([Diffblue](https://www.diffblue.com/resources/deterministic-test-generation/))
- Push deterministic assertions (JSON validity, required fields, length, token budget) into cheap CI;
  keep live as a thin, gated smoke.

**Synthesis for worthless's client-agnostic / provider-aware invariant:**
- **Provider-protocol-generic surface → hermetic.** Because the proxy is client-agnostic, a request
  that is *shape-correct* from a hand-built client and from OpenClaw take the identical proxy path.
  So most coverage is hermetic: mock upstream + assert worthless's forwarded request/SSE/metering per
  provider (OpenAI vs Anthropic — the only legitimate branch).
- **OpenClaw-specific surface → needs the real OpenClaw client, but can stay hermetic-upstream.** The
  bugs that bit (model-ref parse on first `/`, `supportsTools` gate, `requiresStringContent` flatten,
  the `agents/.../models.json` baseUrl merge gotcha) are produced *inside OpenClaw's request-shaping*.
  They are invisible to a hand-built request. So you need the **real OpenClaw process** in the loop —
  but the *upstream* can still be a deterministic mock. OpenClaw's own e2e mock-agent-turn proves this
  combination is viable.
- **Truly live → tiny, nightly, key-gated, cost-capped, property-asserting.** Only for the failure
  ladder's real provider behaviors that no mock can authentically reproduce until you've recorded one.

---

## Concrete recommendations seeded by prior art (for the design doc, not decided here)

1. **Reuse, don't rebuild.** Stand up worthless's journey test on OpenClaw's existing e2e scaffold:
   non-interactive `onboard --mode local --secret-input-mode ref` → set provider `baseUrl` to the
   worthless alias → run the mock-endpoint agent turn with worthless in the path. Mirror the
   `openclaw.json` + `agents/main/agent/models.json` baseUrl write to defeat the merge gotcha.
2. **Mock upstream = real HTTP server with SSE chunk control** (`aimock`/`ai-mocks` class), not respx,
   because two real processes must talk to it and SSE framing is a real failure mode.
3. **Copy OpenClaw's live-gate model** (`LIVE=1` + model-matrix knob, serial, env-keys-first,
   skip-by-default) for the worthless live lane — satisfies `feedback_no_keys_in_ci`.
4. **Record/replay the failure ladder.** Capture one real OpenRouter free-model session (success +
   each 404/400/402 body) as golden fixtures so CI can assert the *exact* error shapes that took the
   WOR-621 debug multiple hours — and so WOR-729 (error-passthrough) gets a regression test.

---

## Sources

- [Testing · OpenClaw](https://docs.openclaw.ai/help/testing)
- [Testing: live suites · OpenClaw](https://docs.openclaw.ai/help/testing-live)
- [Tests reference · OpenClaw](https://docs.openclaw.ai/reference/test)
- [CI pipeline · OpenClaw](https://docs.openclaw.ai/ci)
- [QA / E2E automation · OpenClaw](https://open-claw.bot/docs/concepts/qa-e2e-automation/)
- [CLI automation · OpenClaw](https://docs.openclaw.ai/start/wizard-cli-automation)
- [onboard · OpenClaw](https://docs.openclaw.ai/cli/onboard)
- [Onboarding reference · OpenClaw](https://docs.openclaw.ai/reference/wizard)
- [openclaw/openclaw package.json](https://github.com/openclaw/openclaw/blob/main/package.json)
- [openclaw/acpx](https://github.com/openclaw/acpx) · [acpx CLI.md](https://github.com/openclaw/acpx/blob/main/docs/CLI.md)
- [OpenClaw issue #32179 (SSE delimiter)](https://github.com/openclaw/openclaw/issues/32179)
- [LiteLLM testing (DeepWiki)](https://deepwiki.com/BerriAI/liteLLM-proxy/5.2-testing)
- [LiteLLM load test docs](https://docs.litellm.ai/docs/load_test) · [LiteLLM repo](https://github.com/BerriAI/litellm/) · [mock_response #20969](https://github.com/BerriAI/litellm/issues/20969)
- [LLM Gateway guide (Klymentiev)](https://klymentiev.com/blog/llm-gateway-guide)
- [Helicone OpenRouter integration](https://docs.helicone.ai/getting-started/integration-method/openrouter)
- [Promptfoo Cloudflare AI Gateway provider](https://www.promptfoo.dev/docs/providers/cloudflare-gateway/)
- [CopilotKit/llmock](https://github.com/CopilotKit/llmock) · [CopilotKit/aimock](https://github.com/CopilotKit/aimock)
- [dwmkerr/mock-llm](https://github.com/dwmkerr/mock-llm) · [StacklokLabs/mockllm](https://github.com/StacklokLabs/mockllm) · [mokksy/ai-mocks](https://github.com/mokksy/ai-mocks) · [mock-openai-server](https://github.com/freakynit/mock-openai-server)
- [Mocking OpenAI — unit testing in the age of LLMs (laszlo)](https://laszlo.substack.com/p/mocking-openai-unit-testing-in-the)
- [Playwright Test Agents](https://playwright.dev/docs/test-agents) · [Playwright](https://playwright.dev/)
- [SSE testing pattern (LangWatch Scenario)](https://langwatch.ai/scenario/examples/testing-remote-agents/sse/)
- [LiteLLM Anthropic partial-JSON PR #17493](https://github.com/BerriAI/litellm/pull/17493) · [Anthropic streaming ordering #1863](https://github.com/openai/openai-agents-python/issues/1863)
- [LLM testing tools & frameworks 2026 (contextqa)](https://contextqa.com/blog/llm-testing-tools-frameworks-2026/)
- [API contract testing guide (testRigor)](https://testrigor.com/blog/api-contract-testing/)
- [Deterministic test generation (Diffblue)](https://www.diffblue.com/resources/deterministic-test-generation/)
