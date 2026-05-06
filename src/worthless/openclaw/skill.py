"""Embedded ``SKILL.md`` install/uninstall for OpenClaw.

We own ``~/.openclaw/workspace/skills/worthless/`` (locked decision L3)
and overwrite stale content. Installs are stage-then-rename so a crash
mid-copy leaves the user with either the old folder or no folder — never
half-written state.

Symlinks are refused on both install and uninstall (F34): an attacker
who can plant a link could redirect a privileged write or sweep an
unrelated directory on unlock.

Spec: ``.claude/plans/graceful-dreaming-reef.md`` §"Public API contracts
for Phase 2.a" / ``worthless.openclaw.skill`` and failure-mode rows
F30, F31, F33, F34, F35.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from importlib import (
    resources,
)  # nosemgrep: python.lang.compatibility.python37.python37-compatibility-importlib2  # noqa: E501 -- worthless requires Python 3.10+; importlib.resources is stdlib
from pathlib import Path

from worthless.openclaw.errors import (
    OpenclawErrorCode,
    OpenclawIntegrationError,
)

_SKILL_PACKAGE = "worthless.openclaw.skill_assets"
_SKILL_FILE = "SKILL.md"
_VERSION_LINE = re.compile(r"^Version:\s*(\S+)\s*$", re.MULTILINE)


# ---------------------------------------------------------------------------
# Asset access
# ---------------------------------------------------------------------------


def _read_skill_asset() -> str:
    """Return the embedded ``SKILL.md`` body as a UTF-8 string.

    ``importlib.resources.files()`` is Python 3.9+ and avoids the legacy
    pkg_resources caching issues called out in risk register R5.
    """
    return resources.files(_SKILL_PACKAGE).joinpath(_SKILL_FILE).read_text(encoding="utf-8")


def current_version() -> str:
    """Return the version string declared in the embedded ``SKILL.md``.

    Parses the first ``Version: <token>`` line. Phase 3 will replace the
    body with the real skill content; the parsing contract stays.
    """
    body = _read_skill_asset()
    match = _VERSION_LINE.search(body)
    if not match:
        # Defensive: a future SKILL.md without a Version line is a bug we
        # want to catch loudly rather than silently fall back.
        raise OpenclawIntegrationError(
            OpenclawErrorCode.SKILL_INSTALL_FAILED,
            "embedded SKILL.md is missing a Version: line",
        )
    return match.group(1)


# ---------------------------------------------------------------------------
# Install / uninstall
# ---------------------------------------------------------------------------


def _refuse_if_symlink(path: Path) -> None:
    """Raise SYMLINK_REFUSED if ``path`` is a symlink.

    Uses ``lstat`` semantics via ``Path.is_symlink`` so a dangling link
    is still caught. Canonicalize via ``Path.resolve`` (F35) for the
    final on-disk identity check elsewhere.
    """
    if path.is_symlink():
        raise OpenclawIntegrationError(
            OpenclawErrorCode.SYMLINK_REFUSED,
            f"refusing to follow symlink at {path}",
        )


def install(target_dir: Path) -> Path:
    """Install the embedded skill folder at ``target_dir/worthless/``.

    Stage into ``target_dir/.worthless.tmp.<pid>/``, then atomic-rename
    over any existing folder. Cleans up the staging dir on any failure.

    Returns the resolved final path. Raises :class:`OpenclawIntegrationError`
    on hard refusals (e.g. symlink at the destination, missing version).
    """
    target_dir.mkdir(parents=True, exist_ok=True)

    final = target_dir / "worthless"
    _refuse_if_symlink(final)

    body = _read_skill_asset()

    # Stage into a sibling tempdir so the rename is same-filesystem and
    # atomic. mkdtemp guarantees a fresh unique dir; we own it for the
    # life of this call.
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".worthless.tmp.{os.getpid()}.",
            dir=str(target_dir),
        )
    )
    try:
        # Copy every embedded asset file into the staging dir. Using the
        # importlib.resources contents traversal keeps this content-agnostic
        # (R10) — Phase 3 can add files without us touching this code.
        package_root = resources.files(_SKILL_PACKAGE)
        for entry in package_root.iterdir():
            name = entry.name
            if name == "__init__.py" or name.startswith("__pycache__"):
                continue
            if not entry.is_file():
                continue
            (staging / name).write_text(entry.read_text(encoding="utf-8"), encoding="utf-8")

        # Belt-and-braces: ensure SKILL.md actually landed (covers a future
        # asset reorg that drops the file from the package).
        if not (staging / _SKILL_FILE).is_file():
            (staging / _SKILL_FILE).write_text(body, encoding="utf-8")

        # If a previous install exists at ``final``, remove it first so the
        # rename can succeed. We've already refused symlinks above; a real
        # directory is owned by us per L3 and is overwritable.
        if final.exists():
            shutil.rmtree(final)

        # ``os.replace`` (not ``Path.replace``) is patchable at the module
        # level so failure-injection tests can simulate disk-full / EACCES.
        os.replace(staging, final)  # noqa: PTH105
    except OpenclawIntegrationError:
        # Wrapped errors (e.g. SYMLINK_REFUSED) keep their code; just
        # ensure no staging tempdir leaks (F33).
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise
    except Exception as exc:
        # Any other failure (EACCES, ENOSPC, simulated rename failures
        # in tests) is wrapped as SKILL_INSTALL_FAILED so callers in
        # apply_lock() can surface it as a structured event without
        # leaking raw OSError types into --json output. Always clean up
        # the staging dir so we never leave ``.worthless.tmp.<pid>/``
        # artifacts behind (F33).
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise OpenclawIntegrationError(
            OpenclawErrorCode.SKILL_INSTALL_FAILED,
            f"failed to install skill into {final}: {exc}",
        ) from exc

    return final.resolve()


def uninstall(target_dir: Path) -> bool:
    """Remove ``target_dir/worthless/`` if present. Returns True on removal.

    Tolerant: a missing target_dir or worthless/ subfolder returns False
    without error — supports doctor/unlock retries on partially-installed
    hosts (F-XS-44).

    Refuses to follow a symlink at the destination (F34).
    """
    if not target_dir.exists():
        return False

    final = target_dir / "worthless"
    if final.is_symlink():
        raise OpenclawIntegrationError(
            OpenclawErrorCode.SYMLINK_REFUSED,
            f"refusing to follow symlink at {final}",
        )
    if not final.exists():
        return False

    shutil.rmtree(final)
    return True
