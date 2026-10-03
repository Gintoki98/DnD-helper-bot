"""Who is asking: tools act for a Telegram user id, never for a chat.

Telegram resolves identity from the sender of a message; the MCP server has no
message, so a tool either receives ``user_id`` or falls back to ``MCP_USER_ID``
from .env. There is deliberately no third guess.
"""

from __future__ import annotations

from mcp.server.mcpserver.exceptions import ToolError

from .. import config
from ..storage import db


def actor(user_id: int | None = None) -> int:
    """The Telegram id a tool should act for.

    An explicit ``user_id`` wins, then ``MCP_USER_ID`` from .env. Without
    either there is no way to know who is asking, and the caller is told so
    rather than being silently attributed to nobody.
    """
    if user_id is not None:
        if user_id <= 0:
            raise ToolError(f"user_id must be a Telegram id, got {user_id}")
        return user_id
    if config.MCP_USER_ID is not None:
        return config.MCP_USER_ID
    raise ToolError(
        "This tool needs a Telegram user id and none was given. "
        "Pass user_id=<id>, or set MCP_USER_ID in .env. "
        "The whoami tool explains how to find an id."
    )


async def profile(user_id: int) -> str:
    """How to refer to an id: ``Ada Lovelace (@ada)`` when the bot knows it."""
    row = await db.get_user(user_id)
    if row is None:
        return f"unknown user (id {user_id})"
    name = " ".join(
        part
        for part in ((row["first_name"] or ""), (row["last_name"] or ""))
        if part
    ).strip()
    handle = f" (@{row['username']})" if row["username"] else ""
    if not name:
        return f"id {user_id}{handle}"
    return f"{name}{handle}"
