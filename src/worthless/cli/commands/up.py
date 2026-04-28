"""up command -- standalone proxy daemon/foreground.

``worthless up`` starts the proxy in foreground on port 8787.
``worthless up -d`` starts it in daemon mode (background).
"""

from __future__ import annotations

import os
import signal
import subprocess  # nosec B404 — required for daemon process management
import sys
import time
from contextlib import contextmanager
from pathlib import Path

if sys.platform != "win32":
    import fcntl
else:  # pragma: no cover — fail_if_windows gates before flock is reached
    fcntl = None  # type: ignore[assignment]

import typer

from worthless.cli.bootstrap import WorthlessHome, get_home
from worthless.cli.console import get_console
from worthless.cli.errors import ErrorCode, WorthlessError, error_boundary, sanitize_exception
from worthless.cli.platform import (
    IS_WINDOWS,
    fail_if_windows,
    pid_in_tree,
    popen_platform_kwargs,
)
from worthless.cli.process import (
    build_proxy_env,
    check_pid,
    cleanup_stale_pid,
    disable_core_dumps,
    fernet_transport,
    finalize_fernet_transport,
    pid_path,
    poll_health_pid,
    prepare_proxy_env,
    proxy_cmd,
    read_pid,
    spawn_proxy,
    write_pid,
)
from worthless.cli.sidecar_lifecycle import (
    SidecarHandle,
    shutdown_sidecar,
    spawn_sidecar,
    split_to_tmpfs,
)
from worthless.crypto.types import zero_buf

# Phase D: poll cadence for the foreground supervisor. ``time.sleep`` is
# interrupted by signals, so ``_shutdown`` flips within one tick of Ctrl+C.
_FOREGROUND_POLL_INTERVAL_S = 0.5

# QA #4: filename of the per-home flock that serializes ``worthless up``
# foreground sessions. Living-but-empty file at ``~/.worthless/.up.lock``;
# the OS-level flock (held for the foreground session lifetime) is the
# actual serialization primitive.
_UP_LOCK_FILENAME = ".up.lock"


@contextmanager
def _foreground_lock(home_dir: Path):
    """Serialize concurrent ``worthless up`` invocations via flock.

    Without this, two concurrent invocations both pass the initial
    pidfile check and race: invocation B's later ``write_pid`` overwrites
    A's, then B's port-bind failure runs cleanup that ``unlink``s the
    pidfile A just wrote — leaving A running with no pidfile (and
    ``worthless down`` unable to find it).

    Implementation: ``LOCK_EX | LOCK_NB`` on ``~/.worthless/.up.lock``
    held for the entire foreground session. Concurrent acquirer fails
    fast with WRTLS-105 LOCK_IN_PROGRESS — clear, actionable, and BEFORE
    any subprocess work.

    Windows is gated upstream by ``fail_if_windows()`` so flock is never
    reached there; this CM is a no-op when ``fcntl`` is None.
    """
    if fcntl is None:  # pragma: no cover — Windows-only branch, gated upstream
        yield
        return

    lock_path = home_dir / _UP_LOCK_FILENAME
    # Append-mode opens (or creates if missing) without truncating. Note:
    # ``rm ~/.worthless/.up.lock`` mid-session would let a concurrent
    # acquirer create a fresh inode and bypass our lock. This is out of
    # threat model — an attacker with write access to ``~/.worthless/``
    # already has far worse capabilities (read shards, kill the daemon).
    fp = lock_path.open("a")  # noqa: SIM115 — explicit close in finally below
    try:
        try:
            fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise WorthlessError(
                ErrorCode.LOCK_IN_PROGRESS,
                f"another `worthless up` is already in progress (lock held at {lock_path})",
            ) from exc
        try:
            yield
        finally:
            # Closing the fd auto-releases the lock on POSIX, but
            # explicit unlock is clearer and survives any caller that
            # might keep the fd alive past the CM (defense-in-depth).
            try:
                fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        fp.close()


def _resolve_port(port_arg: int | None) -> int:
    """Resolve port from argument, env var, or default.

    Priority: explicit arg > WORTHLESS_PORT env > 8787 default.
    """
    if port_arg is not None:
        return port_arg
    env_port = os.environ.get("WORTHLESS_PORT")
    if env_port:
        return int(env_port)
    return 8787


def start_daemon(
    proxy_env: dict[str, str],
    port: int,
    pid_file: Path,
    log_file: Path,
    console,
) -> int:
    """Start proxy in daemon mode (setsid, write PID, detach).

    Returns the daemon PID on success.  Importable by other modules
    (e.g. the default command pipeline) that need to start the proxy
    programmatically.
    """
    cmd = proxy_cmd(port)

    log_fd: int = -1
    try:
        with fernet_transport(proxy_env) as (fernet_key, fernet_fd, fernet_fds):
            full_env = prepare_proxy_env(proxy_env, fernet_fd)
            platform_kwargs = popen_platform_kwargs(detach=True, pass_fds=tuple(fernet_fds))

            log_fd = os.open(str(log_file), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)

            proc = subprocess.Popen(  # nosec B603 — cmd is internally constructed, not user input
                cmd,
                env=full_env,
                stdout=subprocess.DEVNULL,
                stderr=log_fd,
                stdin=subprocess.PIPE if fernet_key else None,
                **platform_kwargs,
            )

            finalize_fernet_transport(proc, fernet_key, fernet_fd)
    except Exception as exc:
        if not isinstance(exc, typer.Exit):
            console.print_error(
                WorthlessError(
                    ErrorCode.PROXY_UNREACHABLE,
                    sanitize_exception(exc, generic="failed to start daemon"),
                )
            )
        raise typer.Exit(code=1) from exc
    finally:
        if log_fd >= 0:
            os.close(log_fd)

    # Record the spawn PID up front so a racing second invocation has
    # something live to detect even before health comes up.
    try:
        write_pid(pid_file, proc.pid, port)
    except OSError:
        proc.kill()
        raise

    resolved_pid = poll_health_pid(port, timeout=10.0)
    if resolved_pid is None:
        console.print_warning(
            f"Proxy started (PID {proc.pid}) but health check timed out. Check logs."
        )
        return proc.pid

    canonical_pid = _upgrade_pidfile_if_trusted(
        spawn_pid=proc.pid,
        resolved_pid=resolved_pid,
        port=port,
        pid_file=pid_file,
        console=console,
    )
    console.print_success(f"Proxy running on 127.0.0.1:{port} (PID {canonical_pid})")
    return canonical_pid


def _upgrade_pidfile_if_trusted(
    *,
    spawn_pid: int,
    resolved_pid: int,
    port: int,
    pid_file: Path,
    console,
) -> int:
    """Rewrite *pid_file* with *resolved_pid* only if it belongs to our tree.

    Returns the PID the caller should treat as canonical. A foreign daemon
    already bound to the port would also answer ``/healthz`` — we must not
    record its PID as ours. If the rewrite fails we keep the stale-but-
    writable *spawn_pid* rather than leaving an un-stoppable daemon.
    """
    if resolved_pid == spawn_pid:
        return spawn_pid
    if not pid_in_tree(spawn_pid, resolved_pid):
        console.print_warning(
            f"Proxy started (PID {spawn_pid}) but /healthz reports PID {resolved_pid}, "
            "which is not a descendant of our spawn — recording the spawn PID instead."
        )
        return spawn_pid
    try:
        write_pid(pid_file, resolved_pid, port)
    except OSError:
        return spawn_pid
    return resolved_pid


def _start_foreground(
    *,
    home: WorthlessHome,
    proxy_env: dict[str, str],
    port: int,
    pid_file: Path,
    console,
) -> None:
    """Start sidecar + proxy in foreground (blocks until SIGINT/SIGTERM).

    Phase D: spawns the sidecar BEFORE the proxy, wires the socket path
    into ``proxy_env``, and detects mid-session sidecar crash, surfacing
    it as :attr:`ErrorCode.SIDECAR_CRASHED` (WRTLS-112). On any exit path
    — clean, error, or signal — the proxy is terminated first, then the
    sidecar (cleanup ordering matters).

    Concurrent ``worthless up`` invocations are serialized via flock on
    ``~/.worthless/.up.lock`` (QA #4). The second invocation fails fast
    with WRTLS-105 LOCK_IN_PROGRESS rather than racing on the pidfile.
    """
    # Acquire the foreground lock for the entire session lifetime. flock
    # auto-releases when the file is closed (or on process exit if we
    # crash). A concurrent invocation that finds the lock held raises
    # WRTLS-105 immediately — BEFORE any subprocess work — saving spawn
    # cycles and preventing the "B unlinks A's pidfile" failure mode.
    with _foreground_lock(home.base_dir):
        _start_foreground_locked(
            home=home,
            proxy_env=proxy_env,
            port=port,
            pid_file=pid_file,
            console=console,
        )


def _start_foreground_locked(
    *,
    home: WorthlessHome,
    proxy_env: dict[str, str],
    port: int,
    pid_file: Path,
    console,
) -> None:
    """Foreground body — runs only while ``_foreground_lock`` is held.

    Extracted so the lock acquisition stays a thin envelope around an
    otherwise-unchanged Phase D body.
    """

    # Pre-spawn signal handlers (QA #7): SIGTERM during ``spawn_sidecar``
    # or ``spawn_proxy`` would otherwise hit Python's default ``SIG_DFL``
    # action and terminate the parent before we hold a Popen handle —
    # leaving the freshly-spawned sidecar/proxy as orphans. Install a
    # KeyboardInterrupt-raising handler now so a signal during the spawn
    # window propagates through the existing ``except BaseException``
    # cleanup below. ``_supervise_proxy_with_sidecar`` later replaces these
    # with its flag-based handler (clean in-loop shutdown). Originals are
    # restored in the outer ``finally`` so we don't leak handlers past
    # this function's lifetime.
    def _spawn_window_signal_handler(_signum=None, _frame=None) -> None:
        raise KeyboardInterrupt

    prev_sigint = signal.signal(signal.SIGINT, _spawn_window_signal_handler)
    prev_sigterm = (
        signal.signal(signal.SIGTERM, _spawn_window_signal_handler) if not IS_WINDOWS else None
    )

    try:
        # Step 1: split the Fernet key to tmpfs. ``home.fernet_key`` returns
        # a ``bytearray`` (SR-01); pass it through directly — never cast to
        # ``bytes``, the reconstruct buffer must remain zeroable.
        fernet_key = home.fernet_key
        try:
            shares = split_to_tmpfs(fernet_key, home.base_dir)
        finally:
            # SR-02: zero the plaintext Fernet key. The shares are now on
            # disk and held in ``shares.shard_a/b`` bytearrays (zeroed by
            # ``shutdown_sidecar`` later). The original key is no longer
            # needed in this process — wipe it now so it can't be lifted
            # from a memory dump for the rest of the session. Runs on
            # success AND on split_to_tmpfs failure.
            fernet_key[:] = bytearray(len(fernet_key))

        # Step 2: spawn the sidecar. Same UID = no sudo dance (Phase 0 decision).
        handle: SidecarHandle | None = None
        try:
            socket_path = shares.run_dir / "sidecar.sock"
            handle = spawn_sidecar(socket_path, shares, allowed_uid=os.getuid())

            # Step 3: wire the socket path into the proxy env. We mutate
            # ``proxy_env`` in place rather than extending ``build_proxy_env``
            # (per /plan decision 7) so the sidecar location lives next to the
            # spawn rather than being computed twice.
            proxy_env["WORTHLESS_SIDECAR_SOCKET"] = str(handle.socket_path)

            # Step 4: spawn the proxy.
            try:
                proxy, actual_port = spawn_proxy(env=proxy_env, port=port)
            except Exception as exc:
                console.print_error(
                    WorthlessError(
                        ErrorCode.PROXY_UNREACHABLE,
                        sanitize_exception(exc, generic="failed to start proxy"),
                    )
                )
                raise typer.Exit(code=1) from exc

            _supervise_proxy_with_sidecar(
                proxy=proxy,
                handle=handle,
                actual_port=actual_port,
                pid_file=pid_file,
                console=console,
            )
        except BaseException:
            # Step 5: on any failure after sidecar is up, tear down so we
            # leave no orphan PID, no orphan shares, no orphan socket.
            # ``handle is None`` means ``spawn_sidecar`` itself raised — the
            # share files remain on disk, so unlink them (and the run dir)
            # directly. SR-02: in BOTH branches the in-memory shard bytearrays
            # must be zeroed before the exception propagates.
            if handle is not None:
                shutdown_sidecar(handle)
            else:
                for path in (shares.share_a_path, shares.share_b_path):
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
                try:
                    shares.run_dir.rmdir()
                except OSError:
                    pass
                zero_buf(shares.shard_a)
                zero_buf(shares.shard_b)
            raise
    finally:
        # Restore signal handlers we replaced at function entry.
        # ``_supervise_proxy_with_sidecar`` may have overwritten them; this
        # restores whatever the caller had installed before us so we don't
        # leak our handlers past ``_start_foreground``'s lifetime.
        signal.signal(signal.SIGINT, prev_sigint)
        if prev_sigterm is not None:
            signal.signal(signal.SIGTERM, prev_sigterm)


def _supervise_proxy_with_sidecar(
    *,
    proxy: subprocess.Popen,
    handle: SidecarHandle,
    actual_port: int,
    pid_file: Path,
    console,
) -> None:
    """Run the foreground supervisor loop once both processes are up.

    Wired by :func:`_start_foreground`. Split out so the spawn-and-cleanup
    layer stays readable. On a clean exit the proxy is terminated first,
    then ``shutdown_sidecar`` (Phase C) is called; on a sidecar crash the
    proxy is still terminated first, then ``shutdown_sidecar`` runs, then
    we raise WRTLS-112.
    """
    # Write PID file immediately so a racing second invocation has
    # something to detect. Rewrite below once /healthz reports the
    # authoritative PID.
    write_pid(pid_file, proxy.pid, actual_port)

    # Signal handler ONLY sets a flag — all cleanup in main thread.
    # This avoids reentrant wait() calls and blocking I/O in handlers.
    _shutdown = False

    def _on_signal(_signum=None, _frame=None):
        nonlocal _shutdown
        _shutdown = True

    signal.signal(signal.SIGINT, _on_signal)
    if not IS_WINDOWS:
        signal.signal(signal.SIGTERM, _on_signal)

    resolved_pid = poll_health_pid(actual_port, timeout=15.0)
    if resolved_pid is None:
        proxy.terminate()
        try:
            proxy.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proxy.kill()
            proxy.wait(timeout=2)  # reap after kill to prevent zombies
        pid_file.unlink(missing_ok=True)
        # Sidecar teardown happens via the outer try/except in _start_foreground.
        console.print_error(
            WorthlessError(ErrorCode.PROXY_UNREACHABLE, "Proxy failed to become healthy")
        )
        raise typer.Exit(code=1)

    _upgrade_pidfile_if_trusted(
        spawn_pid=proxy.pid,
        resolved_pid=resolved_pid,
        port=actual_port,
        pid_file=pid_file,
        console=console,
    )

    console.print_success(f"Proxy running on 127.0.0.1:{actual_port} (Ctrl+C to stop)")

    # Phase D: also watch the sidecar. If it dies first, flag the crash
    # and break — the cleanup block below will still tear down the proxy
    # before we surface WRTLS-112.
    sidecar_crashed = False
    while proxy.poll() is None and not _shutdown:
        if handle.proc.poll() is not None:
            sidecar_crashed = True
            break
        time.sleep(_FOREGROUND_POLL_INTERVAL_S)

    # Cleanup — proxy first, then sidecar. Always in main thread.
    proxy.terminate()
    try:
        proxy.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proxy.kill()
        try:
            proxy.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
    pid_file.unlink(missing_ok=True)

    # Tear down the sidecar AFTER the proxy is reaped so any in-flight
    # reconstruct request the proxy was holding has already failed.
    shutdown_sidecar(handle)

    if sidecar_crashed:
        raise WorthlessError(
            ErrorCode.SIDECAR_CRASHED,
            "sidecar terminated unexpectedly during session",
        )

    console.print_warning("Proxy stopped.")


def register_up_commands(app: typer.Typer) -> None:
    """Register the ``up`` command on the Typer app."""

    @app.command()
    @error_boundary
    def up(
        port: int | None = typer.Option(
            None, "--port", "-p", help="Port to bind (default: 8787 or WORTHLESS_PORT)"
        ),
        daemon: bool = typer.Option(
            False, "--daemon", "-d", help="Run in background (daemon mode)"
        ),
    ) -> None:
        """Start the proxy server (foreground or daemon)."""
        fail_if_windows()
        console = get_console()
        home = get_home()

        actual_port = _resolve_port(port)

        # Phase D: daemon mode is foreground-only until WOR-387 wires the
        # sidecar into the daemon path. Reject early with a clear hint
        # rather than silently spawning a proxy without a sidecar.
        if daemon:
            raise WorthlessError(
                ErrorCode.PLATFORM_UNSUPPORTED,
                "daemon mode not yet supported with sidecar — use foreground "
                "(`worthless up` without `-d`). Daemon support is tracked by WOR-387.",
            )

        # Check PID file for existing proxy
        pid_file = pid_path(home)
        if pid_file.exists():
            info = read_pid(pid_file)
            if info is not None:
                existing_pid, existing_port = info
                if check_pid(existing_pid):
                    raise WorthlessError(
                        ErrorCode.PORT_IN_USE,
                        f"Proxy already running "
                        f"(PID {existing_pid} "
                        f"on port {existing_port}). "
                        f"Stop it first or use "
                        f"a different port.",
                    )
                else:
                    # Stale PID file -- reclaim
                    cleanup_stale_pid(pid_file)
                    console.print_warning(f"Reclaimed stale PID file (was PID {existing_pid})")

        # Disable core dumps
        disable_core_dumps()

        # Build proxy env
        proxy_env = build_proxy_env(home)

        _start_foreground(
            home=home,
            proxy_env=proxy_env,
            port=actual_port,
            pid_file=pid_file,
            console=console,
        )
