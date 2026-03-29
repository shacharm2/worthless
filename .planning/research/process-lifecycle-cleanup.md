# Process Lifecycle & Cleanup Research

> How production tools handle parent-child process lifecycle and cleanup when a supervisor/wrapper process dies.
> Researched 2026-03-25 for Phase 4 CLI (wrap/up mode design).

## Recommendation for Worthless

### Wrap Mode: Chrome's pipe-based detection + process groups

1. Spawn children in new process group (`process_group=0`, Python 3.11+)
2. Create "liveness pipe" — parent holds write end, children inherit read end
3. Children monitor pipe in background thread. EOF = parent died = self-terminate
4. Graceful shutdown: `os.killpg(pgid, SIGTERM)` → wait → `os.killpg(pgid, SIGKILL)`
5. Linux: additionally use `prctl(PR_SET_PDEATHSIG, SIGKILL)` for defense-in-depth
6. macOS: no PR_SET_PDEATHSIG equivalent — pipe is the primary mechanism

### Up Mode: Tailscale's state reconciliation + nginx's graceful shutdown

1. Run as system service (systemd/launchd) — v1.x, not PoC
2. Write PID file + Unix socket on startup
3. On startup, check for stale PID file and clean up
4. Support fast shutdown (SIGTERM) and graceful drain (SIGQUIT)
5. Clients detect broken connection via TCP RST / socket EOF

## Summary Table

| Tool | Mechanism | SIGKILL-safe? | Cross-platform? |
|------|-----------|---------------|-----------------|
| Docker | Daemon + cgroups | Containers survive CLI death | Linux native, VM elsewhere |
| Foreman | Signal forwarding | No | Yes (Ruby) |
| supervisord | SIGCHLD + process groups | No | macOS + Linux |
| systemd | cgroups (kernel) | Yes | Linux only |
| Chrome | IPC pipe break | Yes (pipe EOF) | Yes |
| Nginx | PID file + signals | No | macOS + Linux |
| Tailscale | System service + reconciliation | Stale state, reconcile on restart | Yes |

## Process Groups (setpgrp/killpg)

- Reliable on macOS + Linux for trappable signals (SIGTERM, SIGINT)
- Does NOT help with SIGKILL — children orphaned
- Python 3.11+ `process_group=0` in Popen (thread-safe replacement for `preexec_fn=os.setpgrp`)
- `start_new_session=True` (Python 3.2+) creates new session AND process group

## prctl(PR_SET_PDEATHSIG) — Linux Only

- Child calls after fork, receives signal when creating thread exits
- Caveat: triggered by thread death, not process death (multi-threaded parents)
- Race condition: parent dies between fork() and prctl() — check getppid() after
- No macOS equivalent. Workarounds: kqueue EVFILT_PROC, pipe EOF, getppid() polling

## dumb-init (Docker PID 1)

Solves three problems when running as PID 1 in a container:
1. Signal black hole — kernel ignores default handlers for PID 1
2. Zombie accumulation — PID 1 must reap orphans
3. No process group forwarding — shell entrypoints don't forward signals

Relevant if Worthless proxy runs as PID 1 in a Docker container.

## Key Material Crash Exposure (from security reviewer)

| Signal | finally runs? | bytearray zeroed? | str copy zeroed? | Core dump? |
|--------|--------------|-------------------|------------------|------------|
| SIGTERM | Yes (usually) | Yes | No | Yes (default) |
| SIGINT | Yes | Yes | No | No |
| SIGKILL | No | No | No | No |
| SIGABRT | No | No | No | Yes |
| Segfault | No | No | No | Yes |

**Critical:** Reconstructed key as Python `str` (immutable) in HTTP headers can never be zeroed. This is the architectural reason for the Rust reconstruction service in Harden milestone.

**PoC mitigations:**
1. `resource.setrlimit(resource.RLIMIT_CORE, (0, 0))` — disable core dumps
2. `mlock()` key buffer via ctypes — prevent swap-out
3. Null out header dicts in finally block — reduce str copy lifetime
4. Accept SIGKILL/OOM-kill unmitigable in Python

## Sources

- Docker: docs.docker.com/reference/cli/docker/compose/kill/
- Foreman: github.com/ddollar/foreman/issues/357
- Overmind: github.com/DarthSim/overmind
- supervisord: supervisord.org/subprocess.html
- systemd: freedesktop.org/software/systemd/man/latest/systemd.kill.html
- Chrome: chromium.org/developers/design-documents/multi-process-architecture/
- Nginx: nginx.org/en/docs/control.html
- Tailscale: tailscale.com/docs/reference/tailscaled
- dumb-init: engineeringblog.yelp.com/2016/01/dumb-init-an-init-for-docker.html
- Python subprocess: docs.python.org/3/library/subprocess.html
- PR_SET_PDEATHSIG: man7.org/linux/man-pages/man2/pr_set_pdeathsig.2const.html
