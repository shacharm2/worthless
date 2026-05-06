"""Phase 2.a — ``integration.detect()`` unit + functional tests.

Spec: graceful-dreaming-reef.md §"OpenClaw Detection Predicate" and
§"Failure modes" rows F01–F04, F36. Detection is pure: no writes, no
network, no daemon probes. Each test docstring cites the F-ID it covers.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pin HOME (and Path('~').expanduser()) at a tmp_path-rooted directory.

    detect() must consult $HOME — not the real user's. We patch HOME plus
    USERPROFILE (cross-platform safety) so Path.home() resolves under the
    sandbox.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_detect_with_broken_home_returns_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F01 (U-DET-01): broken HOME → present=False, home_dir=None, note set.

    detect() must not raise when HOME is unresolvable; it must downgrade
    cleanly to "absent" so lock-core proceeds untouched (per L1).
    """
    from worthless.openclaw import integration

    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.delenv("USERPROFILE", raising=False)

    # Force Path.home() to raise — the documented mechanism.
    def _raise() -> Path:
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(Path, "home", staticmethod(_raise))

    state = integration.detect()
    assert state.present is False
    assert state.home_dir is None
    assert state.config_path is None
    assert state.workspace_path is None
    assert state.skill_path is None
    assert any("home" in n.lower() for n in state.notes), state.notes


def test_detect_with_openclaw_as_regular_file_returns_absent(
    fake_home: Path,
) -> None:
    """F02 (U-DET-02): ``~/.openclaw/`` exists but is a file → absent.

    detect() must not crash on opendir of a regular file; it must report
    absent with a debug note explaining why.
    """
    from worthless.openclaw import integration

    (fake_home / ".openclaw").write_text("not a dir")

    state = integration.detect()
    assert state.present is False
    assert state.workspace_path is None
    assert any("file" in n.lower() or "not a dir" in n.lower() for n in state.notes), state.notes


def test_detect_with_dangling_workspace_symlink_returns_absent(
    fake_home: Path,
) -> None:
    """F03 (F-DET-03): workspace is a dangling symlink → absent.

    A broken symlink resolves to a non-existent target. detect() must
    not follow it into a misleading "present" verdict.
    """
    from worthless.openclaw import integration

    openclaw_dir = fake_home / ".openclaw"
    openclaw_dir.mkdir()
    workspace = openclaw_dir / "workspace"
    workspace.symlink_to(fake_home / "does_not_exist")

    state = integration.detect()
    assert state.present is False
    assert state.workspace_path is None


def test_detect_with_unreadable_workspace_returns_absent(
    fake_home: Path,
) -> None:
    """F04 (F-DET-04): workspace dir exists but ``os.access(R_OK)`` False.

    Some sandboxes give us a workspace dir we can't read. Treat as absent
    so we don't promise a working install we can't deliver.
    """
    from worthless.openclaw import integration

    workspace = fake_home / ".openclaw" / "workspace"
    workspace.mkdir(parents=True)

    try:
        workspace.chmod(0o000)
        state = integration.detect()
    finally:
        # Restore so tmp_path teardown can rm -r without EACCES.
        workspace.chmod(0o700)

    assert state.present is False
    assert state.workspace_path is None
    assert any("readable" in n.lower() or "access" in n.lower() for n in state.notes), state.notes


def test_detect_with_read_only_home_returns_absent(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F36 (F-DET-36): read-only $HOME → present=False with note.

    Some CI runners freeze HOME; we can't safely promise an install will
    succeed there, so we report absent up front.
    """
    from worthless.openclaw import integration

    real_access = os.access

    def fake_access(path: object, mode: int) -> bool:
        if Path(str(path)) == fake_home and mode == os.W_OK:
            return False
        return real_access(path, mode)

    monkeypatch.setattr("worthless.openclaw.integration.os.access", fake_access)

    state = integration.detect()
    assert state.present is False
    assert any(
        "home" in n.lower() and ("read" in n.lower() or "writable" in n.lower())
        for n in state.notes
    ), state.notes


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_detect_workspace_only_is_present(fake_home: Path) -> None:
    """Happy path A: workspace exists, no config → present=True.

    Per spec predicate (openclaw_present = config OR workspace_dir): a
    workspace alone is enough to say OpenClaw is installed.
    """
    from worthless.openclaw import integration

    workspace = fake_home / ".openclaw" / "workspace"
    workspace.mkdir(parents=True)

    state = integration.detect()
    assert state.present is True
    assert state.config_path is None
    assert state.workspace_path == workspace.resolve()
    assert state.skill_path == (workspace / "skills" / "worthless").resolve()
    assert state.home_dir == fake_home.resolve()


def test_detect_config_only_is_present(fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path B: ``~/.openclaw/openclaw.json`` exists, no workspace.

    Phase 1's locate_config_path() probes ``~/.openclaw/openclaw.json``
    first. detect() must echo that path back even when the workspace dir
    is absent.
    """
    from worthless.openclaw import integration

    monkeypatch.chdir(fake_home)  # avoid stray ./openclaw.json from CWD

    openclaw_dir = fake_home / ".openclaw"
    openclaw_dir.mkdir()
    config = openclaw_dir / "openclaw.json"
    config.write_text("{}")

    state = integration.detect()
    assert state.present is True
    assert state.config_path == config.resolve()
    assert state.workspace_path is None
    assert state.skill_path is None
    assert state.home_dir == fake_home.resolve()


def test_detect_both_present_returns_both_paths(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path C: config + workspace both exist → both fields populated."""
    from worthless.openclaw import integration

    monkeypatch.chdir(fake_home)

    openclaw_dir = fake_home / ".openclaw"
    openclaw_dir.mkdir()
    workspace = openclaw_dir / "workspace"
    workspace.mkdir()
    config = openclaw_dir / "openclaw.json"
    config.write_text("{}")

    state = integration.detect()
    assert state.present is True
    assert state.config_path == config.resolve()
    assert state.workspace_path == workspace.resolve()
    assert state.skill_path == (workspace / "skills" / "worthless").resolve()


def test_detect_returns_frozen_dataclass(fake_home: Path) -> None:
    """IntegrationState is frozen — callers can't mutate the snapshot.

    A mutable detect() result invites bugs where one consumer flips a
    flag and the next consumer sees stale state.
    """
    from worthless.openclaw import integration

    state = integration.detect()
    with pytest.raises((AttributeError, Exception)):
        state.present = True  # type: ignore[misc]


def test_detect_with_home_isdir_oserror_returns_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """F01 cousin: ``Path.home().is_dir()`` raises OSError.

    Some FUSE mounts and broken loop devices can stat-fail unexpectedly.
    We must treat as absent rather than crash.
    """
    from worthless.openclaw import integration

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    real_is_dir = Path.is_dir

    def boom_is_dir(self: Path, *a: object, **kw: object) -> bool:
        if self == home:
            raise OSError("simulated stat failure")
        return real_is_dir(self, *a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "is_dir", boom_is_dir)

    state = integration.detect()
    assert state.present is False
    assert state.home_dir is None
    assert any("unresolvable" in n.lower() for n in state.notes)


def test_detect_with_home_not_a_directory_returns_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Edge case: Path.home() points at a regular file (corrupt env).

    Treat as absent; no install can possibly succeed there.
    """
    from worthless.openclaw import integration

    fake = tmp_path / "home_file"
    fake.write_text("not a dir")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake))

    state = integration.detect()
    assert state.present is False
    assert state.home_dir is None
    assert any("not a directory" in n.lower() for n in state.notes)


def test_detect_with_workspace_not_dir_returns_absent(fake_home: Path) -> None:
    """Edge case: ``~/.openclaw/workspace`` exists as a regular file.

    The workspace path resolves but is_dir() is False; detect must
    report absent with a note.
    """
    from worthless.openclaw import integration

    openclaw_dir = fake_home / ".openclaw"
    openclaw_dir.mkdir()
    (openclaw_dir / "workspace").write_text("not a dir")

    state = integration.detect()
    assert state.present is False
    assert state.workspace_path is None
    assert any("workspace is not a directory" in n.lower() for n in state.notes), state.notes


def test_detect_with_locate_config_oserror_swallowed(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defensive: if locate_config_path() raises OSError, detect() must
    not propagate — config probe failure can't break the predicate.

    Workspace is also absent, so result is "absent" with a config-probe
    note.
    """
    from worthless.openclaw import integration

    def boom() -> Path | None:
        raise OSError("simulated stat failure")

    monkeypatch.setattr("worthless.openclaw.integration.locate_config_path", boom)

    state = integration.detect()
    assert state.present is False
    assert state.config_path is None
    assert any("config probe failed" in n.lower() for n in state.notes)


def test_detect_with_workspace_resolve_oserror(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defensive: workspace exists + is_dir but resolve() raises OSError."""
    from worthless.openclaw import integration

    workspace = fake_home / ".openclaw" / "workspace"
    workspace.mkdir(parents=True)

    real_resolve = Path.resolve

    def selective_resolve(self: Path, *a: object, **kw: object) -> Path:
        if self == workspace:
            raise OSError("simulated workspace resolve failure")
        return real_resolve(self, *a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "resolve", selective_resolve)

    state = integration.detect()
    assert state.present is False
    assert state.workspace_path is None
    assert any("unresolvable" in n.lower() for n in state.notes)


def test_detect_does_not_create_files(fake_home: Path) -> None:
    """detect() is pure: no files or directories created as a side effect.

    Anything mutating belongs in apply_lock(); detect() runs unconditionally
    on every CLI invocation and must stay free of I/O writes.
    """
    from worthless.openclaw import integration

    before = sorted(p.relative_to(fake_home) for p in fake_home.rglob("*"))
    integration.detect()
    after = sorted(p.relative_to(fake_home) for p in fake_home.rglob("*"))
    assert before == after
