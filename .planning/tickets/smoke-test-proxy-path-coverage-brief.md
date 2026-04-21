# New ticket brief — Smoke coverage for proxy path + marker-based selection

**Proposed project**: v1.1 (post-launch hardening)
**Proposed priority**: P1 (critical coverage gap — could ship broken proxy unnoticed)
**Proposed labels**: `v1.1`, `testing`, `DevOps`
**Proposed parent epic**: none

## Story (ELI5)

Our release smoke job runs 3 tests against the published Docker image, selected via pytest `-k` filter. QA-expert reviewed the selection during WOR-236 and found: **zero of the 3 tests actually exercise the proxy itself**. They all verify the container starts and `/healthz` responds. A container where `POST /v1/chat/completions` 500s on first request would pass all three. Healthz green ≠ product works.

Separately, the `-k` string-match selection is silent-failure-prone: if a test gets renamed, the filter silently excludes it, and we only notice when a user reports the regression.

## Why this issue exists

WOR-236 reused the existing `tests/test_docker_e2e.py` suite via `-k` to avoid inventing a new smoke selection mechanism. That was correct scope discipline for landing the publish pipeline, but left the smoke coverage thin on the one thing the container is FOR: proxying API calls.

## What needs to be done

### Part 1: Add proxy-path smoke coverage

Add 1-2 tests that exercise the proxy handler end-to-end inside the running container:

- **Minimum**: `test_proxy_returns_non_5xx_on_enrolled_key` — enroll a fake key, POST to the proxy endpoint, assert the response is a non-5xx (upstream-reachability error is acceptable; a 500 from our handler is not).
- **Better**: `test_proxy_reconstructs_against_mock_upstream` — point `OPENAI_BASE_URL` at a mock, enroll, POST, assert the mock received a request with the correct reconstructed-key header format.

`tests/openclaw/mock-upstream/` already has a mock upstream container that can be reused (it's referenced by `tests/test_openclaw_e2e.py`).

### Part 2: Switch from `-k` to marker-based selection

- Add `@pytest.mark.smoke` to the smoke-selected tests in `tests/test_docker_e2e.py`.
- Update the workflow: `-m "docker and smoke"` instead of `-k "container_starts_healthy or ..."`.
- Add `--strict-markers` in `pyproject.toml` pytest config (or a `pytest.ini`) — unknown markers fail fast.
- Add a collection-count guard in the workflow step: if fewer than N tests are collected, fail the job (protects against someone removing `@pytest.mark.smoke` and CI silently going green).

### Part 3: Consider adding these (QA-expert ranked by ROI)

- `test_fernet_key_generated` + `test_db_initialized` — near-zero marginal cost (container fixture already up). Catches silent bootstrap failures.
- `test_data_persists_across_restart` — ~15s. Catches non-idempotent bootstrap (key regeneration on restart).

## Acceptance criteria

- [ ] Workflow uses `@pytest.mark.smoke` markers, not `-k` string match.
- [ ] At least one test exercises the proxy path (not just `/healthz`).
- [ ] Collection-count guard in workflow prevents silent empty selection.
- [ ] Total smoke job runtime stays under 6 min (current ~2-3 min + ~1-2 min additions).

## Research context for the implementer

- Current smoke selection: `publish-docker.yml` smoke step, `-k "container_starts_healthy or enroll_and_healthz or runs_as_non_root"`.
- Test file to modify: `tests/test_docker_e2e.py` (1061 lines; fixtures at 130-301).
- Mock upstream available: `tests/openclaw/mock-upstream/` — reusable for proxy-path tests without hitting real APIs.
- `pytest --collect-only -q -m "docker and smoke" | wc -l` gives a collectable test count for the guard assertion.

## Dependencies

- WOR-236 must ship (this ticket modifies the workflow it creates).

## Scope boundary

Does NOT include:
- Full regression test coverage in smoke (that's what the PR test suite is for).
- Performance/load testing in smoke.
- Multi-provider smoke (OpenAI + Anthropic + others) — single provider is fine for a smoke.

## Effort estimate

~3 hours: 1h for the marker refactor + count guard, 2h for the proxy-path test + mock-upstream wiring.
