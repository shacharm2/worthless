"""OpenClaw integration entry points.

Phase 2.a ships ``detect()`` + ``IntegrationState`` (pure detection).
Phase 2.b adds ``apply_lock()`` + ``OpenclawApplyResult`` (Stage-3 hook
called from ``worthless lock`` after .env + DB are committed).
``apply_unlock()`` and ``health_check()`` arrive in Phases 2.c–2.d.

``detect()`` is **pure**: no file writes, no network, no daemon probes.
It runs unconditionally on every CLI invocation, so any I/O cost here
becomes startup latency. The detection predicate is::

    openclaw_present = config_present OR workspace_dir_present

where ``config_present`` is delegated to Phase 1's
:func:`worthless.openclaw.config.locate_config_path` (which already
covers the project-local + ``~/.openclaw/`` + XDG fallback chain) and
``workspace_dir_present`` requires
``~/.openclaw/workspace/`` to exist, be a directory, and be readable
(per failure-mode rows F02–F04 and F36).

Spec: ``.claude/plans/graceful-dreaming-reef.md`` §"OpenClaw Detection
Predicate" and §"Failure modes" rows F01–F04, F36.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

from worthless.openclaw import config as _config_mod
from worthless.openclaw import skill as _skill_mod
from worthless.openclaw.config import OpenclawConfigError, locate_config_path
from worthless.openclaw.errors import (
    OpenclawErrorCode,
    OpenclawIntegrationError,
    OpenclawIntegrationEvent,
)

_SKILL_SUBPATH = ("skills", "worthless")
_DEFAULT_PROXY_BASE_URL = "http://127.0.0.1:8787"

# OpenClaw daemon requires the `api` field to identify the wire protocol
# of each provider. Without it, skills may be visible but tool calls go
# nowhere. Maps worthless provider IDs (the keys in the splitter's
# provider registry) to the OpenClaw `api` schema string. Live-verified
# against ghcr.io/openclaw/openclaw:latest.
_PROVIDER_API: dict[str, str] = {
    "openai": "openai-completions",
    "anthropic": "anthropic-messages",
}


@dataclass(frozen=True)
class IntegrationState:
    """Read-only snapshot of OpenClaw presence on this host.

    ``present`` is the OR of config-present and workspace-present (per
    spec predicate). ``notes`` collects human-readable reasons we
    arrived at the verdict — useful for ``--json`` debug output and for
    ``doctor``'s diagnostic surface in Phase 2.d.
    """

    present: bool
    config_path: Path | None
    workspace_path: Path | None
    skill_path: Path | None
    home_dir: Path | None
    notes: tuple[str, ...]


def _resolve_home() -> tuple[Path | None, list[str]]:
    """Return ``(home, notes)`` where ``home`` is the resolved home dir
    or ``None`` if it can't be determined / isn't writable.

    F01: broken HOME → return None with a debug note.
    F36: read-only HOME → return None with a debug note (we can't promise
    an install we can't deliver).
    """
    notes: list[str] = []
    try:
        home = Path.home()
    except (RuntimeError, KeyError, OSError) as exc:
        notes.append(f"home unresolvable: {exc}")
        return None, notes

    # Path.home() is already absolute on POSIX; resolve() would walk every
    # path component (slow on /mnt/c WSL targets). One stat is enough.
    try:
        if not home.is_dir():
            notes.append(f"home is not a directory: {home}")
            return None, notes
    except OSError as exc:
        notes.append(f"home unresolvable: {exc}")
        return None, notes

    if not os.access(home, os.W_OK):
        notes.append(f"home is not writable (read-only): {home}")
        return None, notes

    return home, notes


def _classify(path: Path) -> tuple[str, str | None]:
    """Classify a path with one ``lstat`` syscall (+1 follow if symlink).

    Returns ``(kind, detail)`` where ``kind`` is one of:
    ``missing`` / ``dangling`` (broken symlink) / ``file`` / ``dir`` /
    ``symlink-to-file`` / ``symlink-to-dir``. ``detail`` carries an
    OS-level error string when the probe was inconclusive (treated as
    ``missing`` for safety).

    Collapses 3-4 separate ``is_symlink``/``exists``/``is_dir`` stat
    syscalls per path into one — material on slow filesystems
    (``/mnt/c`` WSL targets per project_target_users.md).
    """
    import stat as _stat

    try:
        st = path.lstat()
    except FileNotFoundError:
        return "missing", None
    except OSError as exc:
        return "missing", str(exc)

    if _stat.S_ISLNK(st.st_mode):
        try:
            target = path.stat()
        except FileNotFoundError:
            return "dangling", None
        except OSError as exc:
            return "missing", str(exc)
        return (
            ("symlink-to-dir", None)
            if _stat.S_ISDIR(target.st_mode)
            else (
                "symlink-to-file",
                None,
            )
        )

    return ("dir", None) if _stat.S_ISDIR(st.st_mode) else ("file", None)


def _probe_workspace(home: Path) -> tuple[Path | None, list[str]]:
    """Return ``(workspace_path or None, notes)`` for the workspace probe.

    F02: ``~/.openclaw`` is a regular file → absent.
    F03: ``~/.openclaw/workspace`` is a dangling symlink → absent.
    F04: workspace dir not readable → absent + warn note.
    """
    notes: list[str] = []
    openclaw_dir = home / ".openclaw"
    kind, detail = _classify(openclaw_dir)
    if kind == "dangling":
        notes.append(f"~/.openclaw is a dangling symlink: {openclaw_dir}")
        return None, notes
    if kind == "file" or kind == "symlink-to-file":
        notes.append(f"~/.openclaw is a file, not a dir: {openclaw_dir}")
        return None, notes
    if kind == "missing":
        if detail:
            notes.append(f"~/.openclaw probe error: {detail}")
        return None, notes

    workspace = openclaw_dir / "workspace"
    kind, detail = _classify(workspace)
    if kind == "dangling":
        notes.append(f"workspace is a dangling symlink: {workspace}")
        return None, notes
    if kind == "file" or kind == "symlink-to-file":
        notes.append(f"workspace is not a directory: {workspace}")
        return None, notes
    if kind == "missing":
        if detail:
            notes.append(f"workspace probe error: {detail}")
        return None, notes

    if not os.access(workspace, os.R_OK):
        notes.append(f"workspace not readable (no R_OK access): {workspace}")
        return None, notes

    try:
        return workspace.resolve(), notes
    except OSError as exc:
        notes.append(f"workspace unresolvable: {exc}")
        return None, notes


def _probe_config() -> tuple[Path | None, list[str]]:
    """Return ``(openclaw.json path or None, notes)`` — symmetric with siblings.

    Delegates to Phase 1's ``locate_config_path`` (project-local + global +
    XDG fallback). Resolves the result so case-insensitive FS paths (F35)
    compare equal downstream.
    """
    notes: list[str] = []
    try:
        candidate = locate_config_path()
    except OSError as exc:
        notes.append(f"config probe failed: {exc}")
        return None, notes

    if candidate is None:
        return None, notes

    # F-CFG-15: do NOT call .resolve() on a symlink — that dereferences
    # the link and hides the attack vector. Return the un-resolved path
    # so apply_lock's symlink check fires correctly. For non-symlinks
    # we still resolve for F35 (case-insensitive FS canonical compare).
    try:
        if candidate.is_symlink():
            notes.append(f"config is a symlink (refused for safety): {candidate}")
            return candidate, notes
        return candidate.resolve(), notes
    except OSError as exc:
        notes.append(f"config unresolvable: {exc}")
        return None, notes


def detect() -> IntegrationState:
    """Determine OpenClaw presence on this host. Pure: no writes, no network.

    Returns a frozen :class:`IntegrationState` snapshot. Callers must not
    cache the result across CLI invocations — the user could install or
    uninstall OpenClaw between runs.
    """
    home, notes = _resolve_home()
    if home is None:
        return IntegrationState(
            present=False,
            config_path=None,
            workspace_path=None,
            skill_path=None,
            home_dir=None,
            notes=tuple(notes),
        )

    workspace, ws_notes = _probe_workspace(home)
    notes.extend(ws_notes)

    config, cfg_notes = _probe_config()
    notes.extend(cfg_notes)

    skill_path = workspace.joinpath(*_SKILL_SUBPATH).resolve() if workspace is not None else None

    # Spec: openclaw_present = config_present OR workspace_dir_present.
    present = config is not None or workspace is not None

    return IntegrationState(
        present=present,
        config_path=config,
        workspace_path=workspace,
        skill_path=skill_path,
        home_dir=home,
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# Phase 2.b — apply_lock()
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OpenclawApplyResult:
    """Result of an :func:`apply_lock` invocation.

    Surfaces in ``worthless lock --json`` so downstream agents (Pi) can
    confirm what we did and what we skipped without parsing logs.

    Per locked decisions L1/L2: a partial-success state with non-empty
    ``providers_skipped`` or non-empty failure ``events`` is still a
    "lock succeeded" outcome from the CLI's perspective — the .env and
    DB are already committed by the time this struct is built.
    """

    detected: bool
    config_path: Path | None
    workspace_path: Path | None
    skill_path: Path | None
    providers_set: tuple[str, ...] = ()
    providers_skipped: tuple[tuple[str, str], ...] = ()
    skill_installed: bool = False
    events: tuple[OpenclawIntegrationEvent, ...] = field(default_factory=tuple)


def _resolve_active_config_path(state: IntegrationState, home: Path | None) -> Path:
    """Pick the config file path to write to.

    If ``detect()`` already located one we use it; otherwise default to
    ``~/.openclaw/openclaw.json`` so set_provider can recreate it
    (covers F-CFG-23: the file vanished between detect and write).
    """
    if state.config_path is not None:
        return state.config_path
    base = home if home is not None else Path.home()
    return base / ".openclaw" / "openclaw.json"


def _check_world_writable(config_path: Path, events: list[OpenclawIntegrationEvent]) -> None:
    """Append a WRITE_FAILED warning event when ``config_path`` is mode 0o**6.

    F-CFG-16: don't chmod, but surface the risk so doctor can flag it.
    Reuses the WRITE_FAILED code at level=warn — adding a dedicated code
    is over-engineering for a single warning channel.
    """
    try:
        mode = config_path.stat().st_mode
    except OSError:
        return
    if mode & (stat.S_IWGRP | stat.S_IWOTH):
        events.append(
            OpenclawIntegrationEvent(
                code=OpenclawErrorCode.WRITE_FAILED,
                level="warn",
                detail=(
                    f"openclaw.json is world/group-writable: {config_path} (mode {mode & 0o777:o})"
                ),
            )
        )


def _is_proxy_url(url: str, proxy_base_url: str) -> bool:
    """Return True if ``url`` is rooted at our proxy.

    Used by F-CFG-13 to distinguish a previous worthless-managed entry
    (safe to overwrite) from a user's manual override (must not stomp).
    """
    return isinstance(url, str) and url.startswith(proxy_base_url.rstrip("/") + "/")


def apply_lock(
    planned_updates: list[tuple[str, str, str]],
    *,
    proxy_base_url: str = _DEFAULT_PROXY_BASE_URL,
) -> OpenclawApplyResult:
    """Wire OpenClaw to route through worthless. Idempotent. Best-effort.

    Stage 3 of ``worthless lock``. Runs AFTER .env + DB are committed.
    Per L1/L2: failures here NEVER roll back lock-core. They surface as
    structured events in the returned :class:`OpenclawApplyResult`.

    Args:
        planned_updates: list of ``(provider, alias, shard_a)`` triples
            for keys that were just locked. ``shard_a`` is a UTF-8 string
            (lock.py decodes the bytearray before calling us).
        proxy_base_url: override for the proxy host. Defaults to
            ``http://127.0.0.1:8787`` (the canonical worthless port).

    Returns:
        :class:`OpenclawApplyResult` describing what we did.

    Spec: ``engineering/research/openclaw-WOR-431-phase-2-spec.md``
    §"Phase 2.b" / §"`apply_lock()` contract".
    """
    state = detect()
    if not state.present:
        return OpenclawApplyResult(
            detected=False,
            config_path=None,
            workspace_path=None,
            skill_path=None,
        )

    events: list[OpenclawIntegrationEvent] = []
    providers_set: list[str] = []
    providers_skipped: list[tuple[str, str]] = []

    config_path = _resolve_active_config_path(state, state.home_dir)
    _check_world_writable(config_path, events)

    # F-CFG-15: refuse symlinked openclaw.json BEFORE any read or write.
    # ``os.replace`` would clobber the link target, so even an invalid
    # symlink is unsafe — and a valid one is exactly the attack vector
    # (point ~/.openclaw/openclaw.json at ~/.bashrc, run worthless lock,
    # watch ~/.bashrc get destroyed). Defensive depth: set_provider /
    # unset_provider also have _refuse_if_symlink inside the flock, but
    # we short-circuit here so the event is correctly tagged
    # SYMLINK_REFUSED instead of CONFIG_UNREADABLE (which would fire
    # when get_provider tried to JSON-parse the link target).
    if config_path is not None:
        try:
            is_link = config_path.is_symlink()
        except OSError:
            is_link = False
        if is_link:
            events.append(
                OpenclawIntegrationEvent(
                    code=OpenclawErrorCode.SYMLINK_REFUSED,
                    level="error",
                    detail=(
                        f"refusing to follow symlink at {config_path} (F-CFG-15) — "
                        "symlinked openclaw.json is a known attack vector"
                    ),
                    extra={"path": str(config_path)},
                )
            )
            # Continue to Stage B (skill install) but skip Stage A.
            return OpenclawApplyResult(
                detected=True,
                config_path=config_path,
                workspace_path=state.workspace_path,
                skill_path=None,
                providers_set=(),
                providers_skipped=tuple(
                    (f"worthless-{provider}", "symlink_refused")
                    for provider, _alias, _shard in planned_updates
                ),
                skill_installed=False,
                events=tuple(events),
            )

    # ---- Stage A: write providers ----------------------------------------
    for provider, alias, shard_a in planned_updates:
        provider_name = f"worthless-{provider}"
        base_url = f"{proxy_base_url.rstrip('/')}/{alias}/v1"

        # F-CFG-13: pre-existing entry pointing somewhere that isn't our
        # proxy is a manual override. Skip + emit conflict event.
        try:
            existing = _config_mod.get_provider(config_path, provider_name)
        except OpenclawConfigError as exc:
            events.append(
                OpenclawIntegrationEvent(
                    code=OpenclawErrorCode.CONFIG_UNREADABLE,
                    level="error",
                    detail=f"could not read {config_path}: {exc}",
                )
            )
            providers_skipped.append((provider_name, "config_unreadable"))
            continue
        except OSError as exc:
            events.append(
                OpenclawIntegrationEvent(
                    code=OpenclawErrorCode.CONFIG_UNREADABLE,
                    level="error",
                    detail=f"could not read {config_path}: {exc}",
                )
            )
            providers_skipped.append((provider_name, "config_unreadable"))
            continue

        if existing is not None:
            existing_url = existing.get("baseUrl", "")
            if existing_url and not _is_proxy_url(existing_url, proxy_base_url):
                events.append(
                    OpenclawIntegrationEvent(
                        code=OpenclawErrorCode.PROVIDER_CONFLICT,
                        level="warn",
                        detail=(
                            f"refusing to overwrite {provider_name}: "
                            f"existing baseUrl {existing_url!r} is not a worthless proxy"
                        ),
                        extra={"provider": provider_name, "baseUrl": existing_url},
                    )
                )
                providers_skipped.append((provider_name, "provider_conflict"))
                continue

        try:
            _config_mod.set_provider(
                config_path,
                provider_name,
                base_url,
                api_key=shard_a,
                # Required by OpenClaw daemon — without these the daemon
                # rejects the config with "Invalid input: expected array,
                # received undefined". Verified live (WOR-431 evidence).
                api=_PROVIDER_API.get(provider),
                # `models=[]` default is applied inside set_provider when
                # the entry is new. Existing arrays are preserved.
            )
        except OpenclawConfigError as exc:
            events.append(
                OpenclawIntegrationEvent(
                    code=OpenclawErrorCode.CONFIG_UNREADABLE,
                    level="error",
                    detail=f"set_provider refused: {exc}",
                    extra={"provider": provider_name},
                )
            )
            providers_skipped.append((provider_name, "config_unreadable"))
            continue
        except OSError as exc:
            events.append(
                OpenclawIntegrationEvent(
                    code=OpenclawErrorCode.WRITE_FAILED,
                    level="error",
                    detail=f"failed to write {config_path}: {exc}",
                    extra={"provider": provider_name},
                )
            )
            providers_skipped.append((provider_name, "write_failed"))
            continue

        providers_set.append(provider_name)
        events.append(
            OpenclawIntegrationEvent(
                code=OpenclawErrorCode.CONFIG_UPDATED,
                level="info",
                detail=f"wrote {provider_name} to {config_path}",
                extra={"provider": provider_name, "baseUrl": base_url},
            )
        )

    # ---- Stage B: install skill ------------------------------------------
    skill_installed = False
    skill_path: Path | None = None
    workspace = state.workspace_path
    if workspace is not None:
        try:
            skill_path = _skill_mod.install(workspace / "skills")
            skill_installed = True
        except OpenclawIntegrationError as exc:
            events.append(
                OpenclawIntegrationEvent(
                    code=getattr(exc, "code", OpenclawErrorCode.SKILL_INSTALL_FAILED),
                    level="error",
                    detail=str(exc),
                )
            )
        except OSError as exc:
            events.append(
                OpenclawIntegrationEvent(
                    code=OpenclawErrorCode.SKILL_INSTALL_FAILED,
                    level="error",
                    detail=f"skill install failed: {exc}",
                )
            )

    return OpenclawApplyResult(
        detected=True,
        config_path=config_path,
        workspace_path=workspace,
        skill_path=skill_path,
        providers_set=tuple(providers_set),
        providers_skipped=tuple(providers_skipped),
        skill_installed=skill_installed,
        events=tuple(events),
    )


# ---------------------------------------------------------------------------
# Phase 2.c — apply_unlock()
# ---------------------------------------------------------------------------


def apply_unlock(
    aliases: list[tuple[str, str]],
    *,
    remove_skill: bool = True,
) -> OpenclawApplyResult:
    """Reverse Phase 2.b's :func:`apply_lock`. Idempotent. Best-effort.

    Stage 3 of ``worthless unlock``. Per L1/L2 in
    ``engineering/research/openclaw-WOR-431-phase-2-spec.md``: failures
    here NEVER cause unlock-core to fail. If the user runs ``unlock`` and
    we can't clean up OpenClaw's config, they still get their ``.env``
    restored — surfaced as structured events instead of exceptions.

    Args:
        aliases: list of ``(provider, alias)`` pairs whose ``worthless-*``
            entries should be removed from ``openclaw.json``. Same shape as
            :func:`apply_lock` minus ``shard_a`` (we don't need it for undo).
        remove_skill: when True (default) sweep
            ``~/.openclaw/workspace/skills/worthless/`` too. Pass False to
            tear down only provider entries — useful for ``doctor --fix``
            paths that want to refresh providers without reinstalling.

    Returns:
        :class:`OpenclawApplyResult`. ``providers_set`` lists the
        ``worthless-*`` entries we actually removed (we reuse the field
        with "providers we changed" semantics — symmetric with
        ``apply_lock``); ``providers_skipped`` lists ones we couldn't
        remove and the reason.

    Spec: ``engineering/research/openclaw-WOR-431-phase-2-spec.md``
    §"Phase 2.c — ``unlock`` integration", failure-mode rows RT-01/02/03,
    F-XS-40 / F-XS-41, locked decisions L1, L2, L3.
    """
    state = detect()
    if not state.present:
        return OpenclawApplyResult(
            detected=False,
            config_path=None,
            workspace_path=None,
            skill_path=None,
        )

    events: list[OpenclawIntegrationEvent] = []
    providers_removed: list[str] = []
    providers_skipped: list[tuple[str, str]] = []

    config_path = _resolve_active_config_path(state, state.home_dir)

    # ---- Stage A: remove worthless-* provider entries --------------------
    for provider, _alias in aliases:
        provider_name = f"worthless-{provider}"
        try:
            removed = _config_mod.unset_provider(config_path, provider_name)
        except OpenclawConfigError as exc:
            events.append(
                OpenclawIntegrationEvent(
                    code=OpenclawErrorCode.CONFIG_UNREADABLE,
                    level="error",
                    detail=f"could not read {config_path}: {exc}",
                    extra={"provider": provider_name},
                )
            )
            providers_skipped.append((provider_name, "config_unreadable"))
            continue
        except OSError as exc:
            events.append(
                OpenclawIntegrationEvent(
                    code=OpenclawErrorCode.WRITE_FAILED,
                    level="error",
                    detail=f"failed to write {config_path}: {exc}",
                    extra={"provider": provider_name},
                )
            )
            providers_skipped.append((provider_name, "write_failed"))
            continue

        # ``unset_provider`` returns ``{}`` when the entry was already
        # absent — that's the idempotent case (IDEM). We still emit a
        # CONFIG_UPDATED event so doctor / --json can confirm the no-op.
        providers_removed.append(provider_name)
        events.append(
            OpenclawIntegrationEvent(
                code=OpenclawErrorCode.CONFIG_UPDATED,
                level="info",
                detail=(
                    f"removed {provider_name} from {config_path}"
                    if removed
                    else f"{provider_name} already absent in {config_path}"
                ),
                extra={"provider": provider_name},
            )
        )

    # ---- Stage B: uninstall skill folder ---------------------------------
    skill_uninstalled = False
    workspace = state.workspace_path
    skill_path = state.skill_path
    if remove_skill and workspace is not None:
        try:
            skill_uninstalled = _skill_mod.uninstall(workspace / "skills")
        except OpenclawIntegrationError as exc:
            events.append(
                OpenclawIntegrationEvent(
                    code=getattr(exc, "code", OpenclawErrorCode.SKILL_INSTALL_FAILED),
                    level="error",
                    detail=str(exc),
                )
            )
        except OSError as exc:
            events.append(
                OpenclawIntegrationEvent(
                    code=OpenclawErrorCode.SKILL_INSTALL_FAILED,
                    level="error",
                    detail=f"skill uninstall failed: {exc}",
                )
            )

    return OpenclawApplyResult(
        detected=True,
        config_path=config_path,
        workspace_path=workspace,
        skill_path=skill_path,
        providers_set=tuple(providers_removed),
        providers_skipped=tuple(providers_skipped),
        skill_installed=skill_uninstalled,
        events=tuple(events),
    )
