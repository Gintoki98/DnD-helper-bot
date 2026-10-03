"""Small helpers shared by every handler module."""

from __future__ import annotations

import html
import time
from typing import Any, Sequence

from telethon import events

from . import config
from .storage import db

# Telegram rejects a message above 4096 characters.
MESSAGE_LIMIT = 4096


def safe(text: Any) -> str:
    """Escape user-supplied text for Telegram HTML.

    Campaign names, character names, notes and announcements all come from
    players and are interpolated into HTML messages, so they must be escaped
    or Telegram rejects the whole message.
    """
    return html.escape(str(text if text is not None else ""), quote=False)


def display_name(user: Any) -> str:
    """A stable, human-readable name for a user or a stored roster row."""
    if user is None:
        return "Someone"
    if isinstance(user, str):
        return user
    first = (getattr(user, "first_name", "") or "").strip()
    last = (getattr(user, "last_name", "") or "").strip()
    name = " ".join(safe(part) for part in (first, last) if part)
    if not name and isinstance(user, dict):
        name = " ".join(
            safe(part) for part in ((user.get("first_name") or ""), (user.get("last_name") or ""))
            if part
        ).strip()
    if not name:
        username = getattr(user, "username", None)
        if not username and isinstance(user, dict):
            username = user.get("username")
        if username:
            return f"<code>@{safe(username)}</code>"
        return "Someone"
    username = getattr(user, "username", None)
    if not username and isinstance(user, dict):
        username = user.get("username")
    return f"{name} <code>@{safe(username)}</code>" if username else name


def plain_name(user: Any) -> str:
    return _strip_tags(display_name(user))


def _strip_tags(text: str) -> str:
    out, depth = [], 0
    i = 0
    while i < len(text):
        if text[i] == "<":
            depth += 1
        elif text[i] == ">":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(text[i])
        i += 1
    return "".join(out).strip()


def relative(timestamp: float | None) -> str:
    """"3 minutes ago" style stamp."""
    if not timestamp:
        return "never"
    delta = max(0.0, time.time() - float(timestamp))
    for limit, divisor, unit in (
        (60, 1, "second"),
        (3600, 60, "minute"),
        (86400, 3600, "hour"),
        (2592000, 86400, "day"),
    ):
        if delta < limit:
            value = int(delta / divisor)
            return f"{value} {unit}{'s' if value != 1 else ''} ago"
    return f"{int(delta / 2592000)} months ago"


def duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def is_callback(event) -> bool:
    """True for a CallbackQuery event (a button press).

    Only ``NewMessage.Event`` carries ``.out``; a CallbackQuery event has no
    such attribute, so probing ``event.out`` there raises AttributeError.
    """
    return isinstance(event, events.CallbackQuery.Event) or hasattr(event, "data")


def command_argument(event) -> str:
    """The text after the command word, stripped; ``""`` when there is none.

    ``/switch Sylra`` -> ``"Sylra"``, ``/switch`` -> ``""``.
    """
    parts = event.raw_text.split(None, 1)
    return parts[1].strip() if len(parts) > 1 else ""


async def send_view(
    event,
    text: str,
    buttons: Sequence[Any] | None = None,
    replace: bool | None = None,
) -> Any:
    """Send a view, editing the triggering message when that makes sense.

    ``replace`` decides explicitly. When it is ``None`` the sensible default
    applies: a button press edits the message it came from, a typed command
    sends a new reply.
    """
    if replace is None:
        replace = is_callback(event)
    if replace:
        return await event.edit(
            text, buttons=buttons, parse_mode="html", link_preview=False
        )
    return await event.reply(
        text, buttons=buttons, parse_mode="html", link_preview=False
    )


class NoCampaign(Exception):
    """Raised when a command needs a campaign but none is selected."""


class NotAMember(Exception):
    """Raised when the user is not in the campaign they are addressing."""


class NotTheDM(Exception):
    """Raised when a player-only command is used by a non-DM."""


async def resolve_campaign_for(
    user_id: int, argument: str | None = None, chat_title: str | None = None
) -> Any:
    """Work out which campaign a user's command refers to.

    Free of Telegram: ``chat_title`` is the title of the group the message
    came from - None in a private chat, and always None for the MCP server.

    Order: explicit argument -> the campaign named in the chat title -> the
    user's last selected campaign -> their only campaign.
    """
    if argument:
        term = argument.strip()
        row = await db.lookup_campaign(term)
        if row is None:
            raise NoCampaign(f"I do not know a campaign called <code>{term}</code>.")
        await db.set_active_campaign(user_id, row["id"])
        return row

    # In a named group, prefer a campaign whose name appears in the title.
    if chat_title:
        lowered = chat_title.lower()
        for row in await db.campaigns_for_user(user_id):
            if row["name"].lower() in lowered:
                return row

    selected = await db.active_campaign_id(user_id)
    if selected:
        row = await db.get_campaign(selected)
        if row is not None:
            return row

    mine = await db.campaigns_for_user(user_id)
    if len(mine) == 1:
        return mine[0]
    if not mine:
        raise NoCampaign(
            "You are not in a campaign yet.\n\n"
            "Start one with <code>/newcampaign The Amber Court</code> "
            "or join a friend's with <code>/join ABC123</code>."
        )
    names = "\n".join(f"• <code>{row['name']}</code> <i>({row['invite_code']})</i>" for row in mine)
    raise NoCampaign(
        "Which campaign? Send it explicitly, e.g. <code>/campaign Amber Court</code>.\n\n"
        f"{names}\n\nOr switch with <code>/select</code>."
    )


async def resolve_campaign(event, argument: str | None = None) -> Any:
    """resolve_campaign_for() for a Telegram event.

    The chat title is read only when the argument did not already settle it:
    ``event.chat`` can cost an entity lookup on Telegram's side.
    """
    title = None
    if not argument:
        chat = getattr(event, "chat", None)
        title = getattr(chat, "title", None) if chat is not None else None
    return await resolve_campaign_for(event.sender_id, argument, title)


async def ensure_member(campaign, user_id: int) -> Any:
    membership = await db.membership(campaign["id"], user_id)
    if membership is None:
        raise NotAMember(
            f"You are not a member of <b>{campaign['name']}</b>.\n"
            f"Join with <code>/join {campaign['invite_code']}</code> and wait for the DM to approve."
        )
    return membership


async def ensure_dm(campaign, user_id: int) -> None:
    membership = await db.membership(campaign["id"], user_id)
    if membership is None or membership["role"] != "dm":
        raise NotTheDM("Only the DM can do that.")


async def member_campaign(event, argument: str | None = None) -> Any:
    """The campaign a member-only command acts on, or ``None`` after replying.

    Resolves the campaign, enforces membership and answers the player itself,
    so each command can start with one call and a guard clause.
    """
    try:
        campaign = await resolve_campaign(event, argument)
        await ensure_member(campaign, event.sender_id)
    except (NoCampaign, NotAMember) as exc:
        await event.reply(str(exc), parse_mode="html")
        return None
    return campaign


async def dm_campaign(event, argument: str | None = None) -> Any:
    """The campaign a DM-only command acts on, or ``None`` after replying.

    Same contract as :func:`member_campaign`, but only the DM passes.
    """
    try:
        campaign = await resolve_campaign(event, argument)
        await ensure_dm(campaign, event.sender_id)
    except NoCampaign as exc:
        await event.reply(str(exc), parse_mode="html")
        return None
    except (NotAMember, NotTheDM) as exc:
        await event.reply(str(exc))
        return None
    return campaign


def is_admin(user_id: int | None) -> bool:
    return bool(config.ADMIN_ID and user_id == config.ADMIN_ID)
