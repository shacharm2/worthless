# SpendCapRule benchmark — SQLite vs Redis hot path (worthless-n48x)

Harness: `tests/bench_spend_cap_rule.py` (pytest-benchmark 5.2.3, Python
3.11.9, darwin/arm64, fakeredis for in-process redis-py).

Two workloads × four ledger sizes (10 / 100 / 1k / 10k rows of spend_log
history per alias). Each `evaluate()` call includes reservation +
release so the numbers reflect the real end-to-end gate cost, not a
raw SELECT.

## Single-request latency (one alias, serial)

| Backend | Ledger | p50 | p95 | p99 | Mean |
|--|--|--|--|--|--|
| SQLite | 10 | 2.43 ms | 6.91 ms | 11.65 ms | 3.03 ms |
| SQLite | 100 | 0.59 ms | 0.91 ms | 1.08 ms | 0.62 ms |
| SQLite | 1,000 | 0.70 ms | 1.36 ms | 2.55 ms | 0.80 ms |
| SQLite | 10,000 | 2.10 ms | 6.33 ms | **14.19 ms** | 2.74 ms |
| Redis  | 10 | 0.47 ms | 1.12 ms | 1.76 ms | 0.57 ms |
| Redis  | 100 | 0.55 ms | 1.57 ms | 3.13 ms | 0.71 ms |
| Redis  | 1,000 | 0.74 ms | 3.31 ms | 6.97 ms | 1.07 ms |
| Redis  | 10,000 | 0.40 ms | 1.04 ms | 3.16 ms | 0.54 ms |

Observations:
* SQLite p99 **breaches the suggested 5 ms SLA at 10k rows** (14 ms).
* SQLite cost is dominated by the `SELECT COALESCE(SUM(tokens)...)` scan.
* Redis cost is flat vs ledger size (GET is O(1)).
* fakeredis has no network hop — a real redis over loopback adds
  ~0.1-0.5 ms, pushing the Redis column to roughly 0.6-1.5 ms at p50.

## Concurrent load (100 parallel `evaluate()` via `asyncio.gather`)

Total wave time (all 100 evaluates complete):

| Backend | Ledger | Wave p50 | Wave p99 | **Per-req p50** | **Per-req p99** |
|--|--|--|--|--|--|
| SQLite | 10 | 65.6 ms | 93.0 ms | **656 µs** | 930 µs |
| SQLite | 100 | 68.8 ms | 134.2 ms | **688 µs** | 1,342 µs |
| SQLite | 1,000 | 77.2 ms | 262.8 ms | **772 µs** | 2,628 µs |
| SQLite | 10,000 | 214.7 ms | 310.4 ms | **2,147 µs** | 3,104 µs |
| Redis  | 10 | 12.2 ms | 27.3 ms | **122 µs** | 273 µs |
| Redis  | 100 | 10.3 ms | 97.3 ms | **103 µs** | 973 µs |
| Redis  | 1,000 | 10.4 ms | 18.4 ms | **104 µs** | 184 µs |
| Redis  | 10,000 | 10.9 ms | 41.7 ms | **109 µs** | 417 µs |

Observations:
* **Redis is 5-10× faster per-request under concurrency.** The gate is
  the reservation lock plus one GET; SQLite has the same lock plus
  `BEGIN IMMEDIATE` on WAL which serialises actual I/O.
* **SQLite wave p99 is 93-310 ms.** The first caller in a burst waits
  for the whole queue to drain before the lock releases — **any
  request in a burst of 100 sees up to ~310 ms gate latency** before
  it even reaches reconstruction. That's a user-visible stall.
* **SQLite scales badly with ledger size under contention**: per-req
  p50 triples (656 µs → 2,147 µs) going from 10 to 10k rows. Redis
  stays flat.
* Per-call ops/sec ceiling on a single event loop:
  * SQLite: ~470-1,500 gate-calls/sec/alias (worse with more history)
  * Redis: ~9,200-9,700 gate-calls/sec/alias (constant)

## Decision (for v1.1 self-hosted single-node)

Three realistic deployment shapes, and what this data says:

1. **Solo developer or light internal tool** — 1-5 concurrent requests
   per alias, spend_log under a few hundred rows between the 90-day
   prune. SQLite p99 is 1-3 ms. **Ship Redis off by default.** ← we're
   here after `fix(redis): CI docker-e2e` (Redis is opt-in via
   `WORTHLESS_REDIS_URL`).
2. **Team tier** — 10-50 concurrent per alias, thousands of rows. SQLite
   wave p99 runs into **60-260 ms**, Redis ~10-40 ms. **Operators
   benefit from flipping Redis on.** Compose already supports this
   with a single env line.
3. **Multi-tenant / burst-heavy** — >100 concurrent per alias. SQLite
   wave p99 hits **310 ms+** and gets worse with history. **Redis is
   table stakes.** This is v2.0 territory anyway.

## Concrete follow-ups

* **Keep Redis shipping opt-in** (no change needed).
* **Document the threshold in the README / docs.** When operators
  should enable `WORTHLESS_REDIS_URL`: "if you see gate latency spikes
  during bursts, or if spend_log grows >1k rows between prunes."
* **Run the benchmark on CI nightly** against the 10k-row ledger — if
  SQLite p99 exceeds a regression threshold (say 20 ms), it signals
  reservation-lock contention has regressed.
* **Re-run with real TCP Redis** (docker-gated) before shipping Redis
  to prod — fakeredis omits loopback latency that will cost another
  ~0.3 ms at p50.

## Raw artefacts

* Harness: `tests/bench_spend_cap_rule.py`
* JSON output: `/tmp/bench.json` (local only; not committed — kick the
  harness to regenerate).
* Re-run: `pytest tests/bench_spend_cap_rule.py -p no:xdist -p no:randomly
  -o addopts= --benchmark-only --benchmark-columns=min,median,mean,max,ops
  --benchmark-sort=name --benchmark-json=/tmp/bench.json`
