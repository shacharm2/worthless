# WOR-207: Live test flight recorder — observability for E2E tests

> "Pass/fail tells you what. The flight recorder tells you why."

Every live E2E test should produce a self-contained report folder with the full data cycle — what was sent, what the proxy decided, what came back, what changed. Auditable without re-running.

## Architecture

Conftest fixture approach. `FlightRecorder` dataclass activated only for `live`/`e2e` marked tests (zero cost on normal runs).

## Report structure

```
.reports/<timestamp>/<test-name>/
  state-before.json     # DB, .env, shard_a_dir snapshot
  flow.json             # state machine transitions with timestamps
  request.json          # HTTP request to proxy
  proxy-log.txt         # proxy internal decisions (logging capture)
  upstream-request.json # what proxy sent to OpenAI/Anthropic
  upstream-response.json
  response.json         # what proxy returned to client
  state-after.json      # post-test state
  verdict.md            # human-readable summary
```

## Implementation

1. `.reports/` in `.gitignore`
2. `tests/conftest_reports.py` — `FlightRecorder` class with `capture_state()`, `capture_request()`, `capture_proxy_log()`, `capture_response()`, `write_verdict()`
3. Function-scoped `flight_recorder` fixture, auto-used on `live`/`e2e` markers
4. Update `test_e2e_live.py` to call recorder at each stage
5. Add `logger.debug()` at 3 proxy decision points in `app.py` (alias extraction, gate, reconstruction) — no-ops in production

## Key decisions

- Proxy subprocess logs captured via `proc.stdout` pipe
- Upstream request/response captured via respx (mocked) or httpx event hooks (live)
- Both JSON (machine) and markdown (human) output
- Fixture, not plugin — keeps it simple and local

## AC

- `uv run pytest tests/test_e2e_live.py -m live` produces `.reports/` with all artifacts
- Reports are self-contained and readable without running the code
- Normal test runs (`uv run pytest`) produce no reports and pay no overhead
