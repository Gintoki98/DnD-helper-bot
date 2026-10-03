"""Turning a tool call's arguments into the rows the tools work on.

The bot works out a campaign from the message that arrived - its sender, the
title of the group it landed in; here the same rules run off plain arguments,
and "which campaign?" comes back as a ToolError carrying the words the player
would have seen. Every tool that needs a campaign goes through this one place.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from ..common import (
    NoCampaign,
    NotAMember,
    NotTheDM,
    ensure_dm,
    ensure_member,
    resolve_campaign_for,
)
from .identity import actor
from .render import strip


async def campaign(user_id: int | None, argument: str | None = None) -> Any:
    """The campaign this call is about.

    Resolution order matches the bot exactly: an explicit name or invite
    code, then the campaign the user last selected, then their only one.
    """
    uid = actor(user_id)
    try:
        return await resolve_campaign_for(uid, argument)
    except NoCampaign as exc:
        raise ToolError(strip(exc)) from exc


async def member(user_id: int | None, argument: str | None = None) -> tuple[int, Any]:
    """The actor **and** the campaign they belong to.

    For tools that would otherwise ask twice: resolve the identity once, the
    campaign the same way the bot does, and prove membership - so a caller
    outside the campaign gets the player's own words as a ToolError instead
    of silently editing somebody else's campaign.
    """
    uid = actor(user_id)
    row = await campaign(uid, argument)
    try:
        await ensure_member(row, uid)
    except NotAMember as exc:
        raise ToolError(strip(exc)) from exc
    return uid, row


async def dm(user_id: int | None, argument: str | None = None) -> tuple[int, Any]:
    """The actor and the campaign they **run** - only the DM passes.

    Same shape as :func:`member` for the DM-only half of the panel: opening
    and closing sessions, and reading the join queue.
    """
    uid = actor(user_id)
    row = await campaign(uid, argument)
    try:
        await ensure_dm(row, uid)
    except NotTheDM as exc:
        raise ToolError(strip(exc)) from exc
    return uid, row
