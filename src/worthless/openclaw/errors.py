"""Stable error codes + event objects for the OpenClaw integration.

The string values of :class:`OpenclawErrorCode` are wire-format: they
appear in ``worthless lock --json`` / ``worthless doctor --json`` output
and are consumed by Pi (the JSON parser referenced in spec AC6). Renaming
a value is a breaking change for downstream agents — extend, don't rename.

The :class:`OpenclawIntegrationEvent` dataclass is frozen so events
appended to a sink in lock-core can't be mutated by later stages before
``--json`` renders them.

Spec: ``.claude/plans/graceful-dreaming-reef.md`` §"Public API contracts
for Phase 2.a" and the failure-mode tables F01–F47.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class OpenclawErrorCode(str, Enum):
    """Wire-stable identifiers for OpenClaw integration events.

    Subclasses :class:`str` so JSON serialization yields the dotted string
    directly without needing a custom encoder.
    """

    CONFIG_UNREADABLE = "openclaw.config_unreadable"
    CONFIG_RECREATED = "openclaw.config_recreated"
    CONFIG_UPDATED = "openclaw.config_updated"
    CONFIG_MISSING = "openclaw.config_missing"
    PROVIDER_CONFLICT = "openclaw.provider_conflict"
    SYMLINK_REFUSED = "openclaw.symlink_refused"
    WRITE_FAILED = "openclaw.write_failed"
    LOCK_TIMEOUT = "openclaw.lock_timeout"
    SKILL_FOREIGN_OWNER = "openclaw.skill_foreign_owner"
    SKILL_INSTALL_FAILED = "openclaw.skill_install_failed"
    HOME_MISMATCH = "openclaw.home_mismatch"


@dataclass(frozen=True)
class OpenclawIntegrationEvent:
    """One structured event emitted by the OpenClaw integration layer.

    Frozen so a downstream stage can't flip ``level`` before the JSON
    renderer reads it. Use :func:`dataclasses.asdict` to serialize.
    """

    code: OpenclawErrorCode
    level: str  # "info" | "warn" | "error"
    detail: str
    extra: dict[str, str] | None = field(default=None)


class OpenclawIntegrationError(Exception):
    """Hard refusal raised by :mod:`worthless.openclaw.skill` operations.

    Caught by ``integration.apply_lock()`` (Phase 2.b) and converted to an
    :class:`OpenclawIntegrationEvent` — never propagates into lock-core
    per locked decision L1.
    """

    def __init__(self, code: OpenclawErrorCode, detail: str) -> None:
        self.code = code
        super().__init__(detail)
