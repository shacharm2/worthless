"""Typer application — CLI entry point for ``worthless``."""

from __future__ import annotations

import typer

from worthless.cli.console import WorthlessConsole, set_console
from worthless.cli.errors import set_debug

app = typer.Typer(
    name="worthless",
    help="Protect your API keys in 90 seconds.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)


@app.callback()
def _main(
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress non-error output"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
    debug: bool = typer.Option(False, "--debug", help="Show full tracebacks on error"),
) -> None:
    """Worthless — make stolen API keys architecturally worthless."""
    set_debug(debug)
    set_console(WorthlessConsole(quiet=quiet, json_mode=json_output))


# -- Register command modules --------------------------------------------------
from worthless.cli.commands.lock import register_lock_commands  # noqa: E402

register_lock_commands(app)

from worthless.cli.commands.unlock import register_unlock_commands  # noqa: E402

register_unlock_commands(app)

from worthless.cli.commands.scan import register_scan_commands  # noqa: E402

register_scan_commands(app)

from worthless.cli.commands.status import register_status_commands  # noqa: E402

register_status_commands(app)

from worthless.cli.commands.wrap import register_wrap_commands  # noqa: E402

register_wrap_commands(app)

from worthless.cli.commands.up import register_up_commands  # noqa: E402

register_up_commands(app)
