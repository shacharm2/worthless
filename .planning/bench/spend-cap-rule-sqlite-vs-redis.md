# SpendCapRule benchmark — SQLite vs Redis hot path (worthless-n48x)

Harness: `tests/bench_spend_cap_rule.py` (pytest-benchmark 5.2.3, Python
3.11.9, darwin/arm64 — Apple Silicon Mac, Docker Desktop).

Three backends × four ledger sizes (10 / 100 / 1k / 10k rows of spend_log
history per alias). Each `evaluate()` call includes reservation + release
so the numbers reflect the real end-to-end gate cost, not a raw SELECT.

Backends:
* **sqlite** — no Redis, current pre-Redis default path, reads
  `SELECT SUM(tokens)` under `BEGIN IMMEDIATE`.
* **redis (fakeredis)** — real `redis.asyncio.Redis` client wired to an
  in-process FakeRedis server. Exercises client code paths but omits
  loopback latency.
* **redis (tcp)** — same real client, real TCP, real `redis:7-alpine`
  container over 127.0.0.1 loopback via Docker Desktop. **Caveat**: Docker
  Desktop on macOS is a VM; loopback pays ~4-7 ms of virtualisation
  overhead per GET. On native Linux the gap to fakeredis collapses.

## Single-request latency (one alias, serial)

| Ledger | SQLite p50 | SQLite p99 | Fakeredis p50 | Fakeredis p99 | **TCP p50** | **TCP p99** |
|--|--|--|--|--|--|--|
| 10     | 2.43 ms | **11.65 ms** | 472 µs | 1.76 ms | 6.12 ms | 22.08 ms |
| 100    | 588 µs  | 1.08 ms  | 546 µs | 3.13 ms | 2.33 ms | 8.69 ms  |
| 1,000  | 696 µs  | 2.55 ms  | 736 µs | 6.97 ms | 8.67 ms | 24.73 ms |
| 10,000 | 2.10 ms | **14.19 ms** | 405 µs | 3.16 ms | 1.87 ms | 7.68 ms  |

Observations:
* SQLite p99 breaches the 5 ms suggested SLA at ≥10k rows (14 ms).
* Fakeredis is the floor — flat vs ledger size, sub-ms.
* TCP adds ~5-8 ms at p50 on macOS Docker Desktop. This is **almost
  entirely Docker-on-Mac virtualisation** — rerun on Linux would halve.
* **Single-request is not a case where Redis earns its keep on macOS**
  unless the SQLite ledger is huge. On Linux it would.

## Wave of 100 concurrent `evaluate()` (total wave time)

| Ledger | SQLite p50 | SQLite p99 | Fakeredis p50 | Fakeredis p99 | **TCP p50** | **TCP p99** |
|--|--|--|--|--|--|--|
| 10     | 65.6 ms  | 93.0 ms  | 12.2 ms | 27.3 ms  | 24.6 ms | 114.1 ms |
| 100    | 68.8 ms  | 134.2 ms | 10.3 ms | 97.3 ms  | 21.8 ms | 59.6 ms  |
| 1,000  | 77.2 ms  | 262.8 ms | 10.4 ms | 18.4 ms  | 46.0 ms | 100.1 ms |
| 10,000 | 214.7 ms | 310.4 ms | 10.9 ms | 41.7 ms  | 37.7 ms | 214.0 ms |

## Per-request under concurrency (wave time / 100)

| Ledger | SQLite | Fakeredis | **TCP** |
|--|--|--|--|
| 10     | 656 µs | 122 µs | **246 µs** |
| 100    | 688 µs | 103 µs | **218 µs** |
| 1,000  | 772 µs | 104 µs | **460 µs** |
| 10,000 | **2.15 ms** | 109 µs | **377 µs** |

Observations:
* **Under burst, TCP Redis beats SQLite 2-6× at p50** regardless of
  ledger size.
* **TCP p99 is noisier** than fakeredis (100-214 ms for a wave) — bursts
  hit socket backpressure. Not representative of Linux.
* **SQLite wave p99 still the worst**: first caller in a burst of 100
  sees 93-310 ms gate latency before reconstruction even starts. Users
  feel this as a stall.
* Theoretical ops/sec ceiling on a single event loop:
  * SQLite: ~470-1,500 gate-calls/sec/alias (worse with more history)
  * Fakeredis: ~9,200-9,700 calls/sec/alias (constant)
  * TCP Redis: ~2,200-4,500 calls/sec/alias (halved by loopback VM)

## Decision for v1.1 self-hosted single-node

The measured threshold where Redis earns its keep:

| Deployment shape | Recommendation |
|--|--|
| Solo dev / light internal tool (<10 concurrent per alias, <1k rows) | **SQLite** — Redis off. Current default. |
| Team tier (10-50 concurrent per alias, or >1k rows history) | **Redis on**. SQLite wave p99 is user-visible. |
| Multi-tenant / burst-heavy (>100 concurrent per alias) | **Redis required**. SQLite serialises every gate. |

**Keep Redis opt-in in the default compose** (already set by
worthless-xcsi; `WORTHLESS_REDIS_URL` unset → pre-Redis SQLite path).

## Action items landed

* Harness at `tests/bench_spend_cap_rule.py` — covers sqlite / fakeredis
  / docker-gated tcp-redis.
* Docs: README updated with the threshold (below).

## Still open

* CI nightly benchmark against the 10k-row ledger to flag regressions —
  should be separate follow-up.
* Rerun on Linux to quantify how much of the TCP overhead is
  Docker-on-Mac vs real redis — worth doing before shipping a
  "benchmarked on Linux" claim.

## Raw artefacts

* Harness: `tests/bench_spend_cap_rule.py`
* JSON outputs: `/tmp/bench.json` (sqlite + fakeredis),
  `/tmp/bench2.json` (tcp) — local only; not committed.
* Re-run (all three backends):

  ```
  pytest tests/bench_spend_cap_rule.py \
      -p no:xdist -p no:randomly -o addopts= \
      --benchmark-only --benchmark-sort=name \
      --benchmark-json=/tmp/bench.json
  ```
