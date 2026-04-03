# Worthless: Performance & Optimization Strategy

## Executive Summary
This conceptual optimization plan outlines the strategy for minimizing response latency—specifically focusing on Time-To-First-Token (TTFT)—and maximizing concurrent throughput for the Worthless proxy.

The optimizations are strictly architectural and break down into three vectors:
1. **Static / Structural**: Removing Python baseline execution overhead.
2. **Dynamic / Runtime**: Managing memory allocations, caching, and DB I/O.
3. **Latency / Network**: Shrinking the network envelope and protocol overhead to upstream LLMs.

---

### 1. Static Optimization (Structural & Ahead-of-Time)
Static optimizations are applied before runtime to lower the baseline cost of executing Python code.

*   `[Static / Compilation]` **AOT Compilation for Cryptography (`mypyc`)**
    *   **Context:** `reconstruct_key` runs synchronously on every request to manipulate bytes. Pure Python bitwise operations are inherently slow.
    *   **Action:** Compile `worthless.crypto.splitter` using Mypyc.
    *   **Static Analysis Findings:** Currently, `pyproject.toml` uses a pure-Python `setuptools` build backend. While `splitter.py` is perfectly type-hinted and uses standard modules, introducing `mypyc` forces the project to distribute OS-specific C-extensions via wheels. We defer this optimization to preserve the project's frictionless installation across platforms unless TTFT absolutely demands more speed.

*   `[Static / Dependency]` **High-Performance JSON Engine (`orjson`)**
    *   **Context:** Inspecting the `"model"` key or parsing streaming schemas relies on string manipulation, which is memory intensive in standard `json`.
    *   **Action:** Substitute standard `json` with `orjson`.
    *   **Static Analysis Findings:** Static grep analysis reveals standard `import json` usage deeply embedded across the proxy engine (`app.py`, `errors.py`, `metering.py`) and CLI commands. Implementing this requires swapping the imports project-wide (`import orjson as json`) and explicitly adjusting `json.dumps()` calls, as `orjson.dumps()` natively outputs `bytes`.

*   `[Static / Loop]` **Event Loop Swapping (`uvloop`)**
    *   **Context:** Native asyncio works well but is historically slower than high-performance I/O loops.
    *   **Action:** Point ASGI (uvicorn/hypercorn) at `uvloop`.
    *   **Static Analysis Findings:** Code inspection of `src/worthless/cli/process.py` line ~131 shows the daemon dynamically spawning Uvicorn using `subprocess.Popen(["uvicorn", ...])`. Upgrading this is trivial: append `["--loop", "uvloop"]` to the command array and add `uvloop` to `pyproject.toml` dependencies.

---

### 2. Dynamic Optimization (Runtime & State)
Dynamic analysis optimizations target bottlenecks that scale linearly as request concurrency grows.

*   `[Dynamic / I-O]` **SQLite Concurrency Tuning**
    *   **Context:** `ShardRepository` hits disk for `shard_b` and `nonce` sequentially. Default SQLite locks cause severe contention under heavy read loads.
    *   **Action:** Set `PRAGMA journal_mode=WAL;` (Write-Ahead Logging). This allows multiple requests to read keys concurrently without being blocked by writers.
    
*   `[Dynamic / Caching]` **Secure Zeroing LRU Cache**
    *   **Context:** Hitting the disk for `shard_b` still costs ~1-3ms per request.
    *   **Action:** Build an in-memory `lru_cache` for `StoredShard` structures. **Critical Security Caveat:** The cache eviction policy *must* securely execute `.zero()` on the memory when a shard is pushed out of the cache to preserve the local-memory invariants.
    
*   `[Dynamic / Memory]` **Zero-Copy Streaming Pipelines**
    *   **Context:** Intercepting stream chunks (SSE) just to decode from bytes to strings, then encode back to bytes, triggers massive garbage collection pauses.
    *   **Action:** Maintain a zero-copy byte pipe. Map `httpx`'s `.aiter_bytes()` directly into FastAPI's `StreamingResponse` so Python never allocates intermediate string representations.

---

### 3. Network & Latency Optimization (TTFT)
Time-To-First-Token (TTFT) is the dominant factor in LLM responsiveness. TCP and TLS overhead are the primary silent killers here.

*   `[Latency / Protocol]` **Global Connection Pooling**
    *   **Context:** A fresh `httpx.AsyncClient` per request triggers a fresh TCP handshake and TLS negotiation, adding ~100-300ms of latency instantly.
    *   **Action:** Initialize a global `httpx.AsyncClient` tied to FastAPI's lifespan events. Maximize the pool size (e.g., `Limits(max_keepalive_connections=100)`).
    
*   `[Latency / Network]` **HTTP/2 Multiplexing**
    *   **Context:** HTTP/1.1 requires head-of-line blocking (one active request per connection).
    *   **Action:** Spin up the global client with `http2=True`. Both Anthropic and OpenAI support HTTP/2, letting dozens of requests interleave over a single hot TCP tunnel.
    
*   `[Latency / Algorithm]` **Rules Engine Short-Circuiting**
    *   **Context:** Reconstructing keys only to drop the request because they hit a spend limit wastes CPU time for downstream users.
    *   **Action:** Ensure Limits and Gate checks are fired off *before* invoking the `reconstruct_key` overhead.

---

### 4. Dynamic Verification & Load Testing
Validating dynamic optimizations requires subjecting the proxy to simulated production pressures, specifically focusing on memory stability and head-of-line blocking measurements.

*   `[Test / Micro-Benchmarks]` **`pytest-benchmark` Execution**
    *   Your `pyproject.toml` already inherently supports `pytest-benchmark`. We isolate the `ShardRepository.retrieve_enrolled()` call with and without the `lru_cache`, and the XOR `reconstruct_key` logic with and without `mypyc` compiled extensions, guaranteeing an objective ms-per-call drop.
*   `[Test / Concurrency Sink]` **`locust` or `bombardier` Spike Tests**
    *   We spawn a local dummy fast-API server mocking Anthropic APIs with a simulated ~150ms TTFT response. Then, blast Worthless with 500 concurrent requests.
    *   If **SQLite WAL** is working, throughput shouldn't stutter on disk reads.
    *   If **Global Connection Pooling** with **HTTP/2** is working, the mock server shouldn't log 500 new TCP handshakes.
*   `[Test / Memory Leaks]` **`memray` Profiling for Zero-Copy Streaming**
    *   We profile the proxy process using Python's `memray` profiler while pushing 100MB of synthetic chunked SSE data through it.
    *   A successful zero-copy pipeline execution will display a flatlined memory profile graph. If intermediate `str` decoding hasn't been eradicated, `memray` will show violent spiky sawtooth allocation patterns where the garbage collector works overtime.
*   `[Test / Security Isolation]` **LRU Cache Exhaustion Property Test**
    *   Using Hypothesis, we rapidly simulate 10,000 requests using 10,000 distinctly random alias keys to force the LRU cache to repeatedly evict stale items. We map Python `sys.getrefcount()` checks on the `StoredShard` bytearrays to prove `.zero()` is deterministically fired on every unmounting without leaking sensitive bytes.
