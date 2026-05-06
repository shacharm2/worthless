"""OpenClaw integration entry points.

Phase 2.a ships only the ``detect()`` predicate and the
:class:`IntegrationState` snapshot it returns. ``apply_lock``,
``apply_unlock``, and ``health_check`` arrive in Phases 2.b–2.d.

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
from dataclasses import dataclass
from pathlib import Path

from worthless.openclaw.config import locate_config_path

_SKILL_SUBPATH = ("skills", "worthless")


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

    try:
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
