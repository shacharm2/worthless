"""WorthlessConsole — TTY/plain/json output routing singleton."""

from __future__ import annotations

import json
import os
import sys
from contextlib import nullcontext
from typing import Any

from rich.console import Console

from worthless.cli.errors import WorthlessError

# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_console: WorthlessConsole | None = None


def get_console() -> WorthlessConsole:
    """Return the current console (creates a default if none set)."""
    global _console  # noqa: PLW0603
    if _console is None:
        _console = WorthlessConsole()
    return _console


def set_console(console: WorthlessConsole) -> None:
    """Install *console* as the module-level singleton."""
    global _console  # noqa: PLW0603
    _console = console


# ---------------------------------------------------------------------------
# Console class
# ---------------------------------------------------------------------------


class WorthlessConsole:
    """Routes output to stderr (spinners/status) vs stdout (data).

    Respects ``--quiet``, ``--json``, and ``NO_COLOR``.
    """

    def __init__(self, quiet: bool = False, json_mode: bool = False) -> None:
        self.quiet = quiet
        self.json_mode = json_mode
        no_color = self._no_color
        self._err = Console(stderr=True, no_color=no_color)
        self._out = Console(no_color=no_color)

    # ------------------------------------------------------------------
    # Colour helpers
    # ------------------------------------------------------------------

    @property
    def _no_color(self) -> bool:
        if os.environ.get("FORCE_COLOR"):
            return False
        return bool(os.environ.get("NO_COLOR"))

    # ------------------------------------------------------------------
    # Output methods
    # ------------------------------------------------------------------

    def status(self, message: str) -> Any:
        """Return a Rich spinner context manager on stderr, or a no-op."""
        if self.quiet:
            return nullcontext()
        return self._err.status(message)

    def print_result(self, data: dict[str, Any]) -> None:
        """Print structured data — JSON to stdout in json_mode, Rich otherwise."""
        if self.json_mode:
            sys.stdout.write(json.dumps(data, default=str) + "\n")
            sys.stdout.flush()
        else:
            self._out.print(data)

    def print_success(self, message: str) -> None:
        """Green text to stderr (suppressed in quiet mode)."""
        if not self.quiet:
            self._err.print(f"[green]{message}[/green]")

    def print_error(self, error: WorthlessError) -> None:
        """Red WRTLS-NNN to stderr (always shown)."""
        self._err.print(f"[bold red]{error}[/bold red]")

    def print_warning(self, message: str) -> None:
        """Yellow text to stderr (suppressed in quiet mode)."""
        if not self.quiet:
            self._err.print(f"[yellow]{message}[/yellow]")
