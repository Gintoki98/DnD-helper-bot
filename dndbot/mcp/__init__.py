"""The MCP server: one module per domain, each exposing ``register(mcp)``.

Same shape as ``dndbot.handlers`` - whoever reads the bot reads this at a
glance. ``build_server()`` is the only wiring; every other decision lives in
the domain modules.

Domains land here as they are implemented: ``dice``, ``srd``, ``meta``,
``character`` and ``campaign`` - the full v1 inventory of §2 in the plan.
"""

from __future__ import annotations

from mcp.server import MCPServer

from . import campaign, character, dice, meta, srd
from .runtime import lifespan

INSTRUCTIONS = (
    "Tools for the D&D 5e helper bot: dice, SRD reference, characters, "
    "campaigns and sessions. Every tool is stateless - one call, one answer - "
    "and everything shares the Telegram bot's database, so a campaign created "
    "here shows up in Telegram and the other way round. Tools act for a "
    "Telegram user id: pass user_id where one is asked for, or set "
    "MCP_USER_ID in .env (whoami reports it). Campaigns are addressed by "
    "name or invite code."
)


def build_server() -> MCPServer:
    """Assemble the server: lifecycle first, then one call per domain."""
    server = MCPServer(
        "dndbot", version="0.1.0", instructions=INSTRUCTIONS, lifespan=lifespan
    )
    dice.register(server)
    srd.register(server)
    meta.register(server)
    character.register(server)
    campaign.register(server)
    return server
