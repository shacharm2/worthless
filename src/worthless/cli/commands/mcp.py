"""mcp command — start the Worthless MCP server over stdio."""

from __future__ import annotations

import typer

from worthless.cli.errors import error_boundary


def register_mcp_commands(app: typer.Typer) -> None:
    """Register the ``mcp`` command on the Typer app."""

    @app.command()
    @error_boundary
    def mcp() -> None:
        """Start the MCP server (stdio transport)."""
        from worthless.mcp.server import main

        main()
