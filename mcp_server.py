#!/usr/bin/env python3
"""Start the D&D helper MCP server.

    python mcp_server.py

The client launches this process and talks to it over stdin/stdout, so
nothing may ever be written to stdout: logging goes to logs/ and stderr, and
that is the whole rule. No Telegram credentials are needed - the server
shares the database and the SRD cache with the bot. Configure it in the
client with:

    "mcp": {"dndbot": {"type": "local",
                       "command": ["…/DnD-helper-bot/.venv/bin/python",
                                   "…/DnD-helper-bot/mcp_server.py"]}}

``MCP_USER_ID`` in .env gives the tools a Telegram id to act for when a call
does not pass one (see .env.example).
"""

from __future__ import annotations

import sys

from dndbot import config
from dndbot.config import ConfigError
from dndbot.logs import setup_logging
from dndbot.mcp import build_server


def run() -> int:
    try:
        setup_logging()
        config.check(telegram=False)
        build_server().run(transport="stdio")
    except ConfigError as exc:
        # stderr, never stdout: stdout belongs to the protocol.
        print(f"\nConfiguration problem: {exc}\n", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(run())
