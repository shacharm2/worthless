"""Worthless integration with OpenClaw.

Canonical home for the openclaw.json reader/writer used by both WOR-431
(``worthless openclaw enable/disable/status``) and WOR-321 (sidecar
auto-configuration of the OpenClaw container).
"""

from worthless.openclaw.config import (
    OpenclawConfigError,
    get_provider,
    locate_config_path,
    read_config,
    set_provider,
    unset_provider,
)

__all__ = [
    "OpenclawConfigError",
    "get_provider",
    "locate_config_path",
    "read_config",
    "set_provider",
    "unset_provider",
]
