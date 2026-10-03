"""Identity and self-description: who is calling, what they are part of,
and what the bot itself says about how to use it."""

from __future__ import annotations

from inspect import cleandoc

from mcp.server.mcpserver.exceptions import ToolError

from .. import config
from ..common import is_admin
from ..help import (
    ALIASES,
    HELP_PAGES,
    HELP_TOPICS,
    PAGE_KEYS,
    TUTORIAL_PAGES,
    WELCOME,
)
from ..logs import errors_path, recent_errors as error_log
from ..storage import db
from . import identity
from .render import esc_md, to_markdown


async def whoami(user_id: int | None = None, name: str | None = None) -> str:
    """Who this server acts for, or who a Telegram name belongs to. Telegram: ``/whoami``.

    With no arguments it reports the identity in use (``MCP_USER_ID`` from
    .env): the id every tool that changes something acts on, plus its
    campaigns. Pass ``name`` to search Telegram users by username or name -
    that is how you discover the ``user_id`` other tools expect. Pass
    ``user_id`` to inspect a specific id instead of the configured one.
    """
    if name is not None:
        term = name.strip()
        if not term:
            raise ToolError('Give a name or @username to search for, e.g. name="ada".')
        matches = await db.find_user(term)
        if not matches:
            return (
                f"No Telegram user matches {term!r}.\n\n"
                "Only people who have already spoken to the bot are known here."
            )
        lines = [f"**{len(matches)} match(es) for {esc_md(term)}**", ""]
        for row in matches:
            full = " ".join(
                part
                for part in (row["first_name"] or "", row["last_name"] or "")
                if part
            ).strip()
            handle = f" @{row['username']}" if row["username"] else ""
            lines.append(f"- {esc_md((full or 'unknown name') + handle)} · id `{row['id']}`")
        return "\n".join(lines)

    uid = user_id if user_id is not None else config.MCP_USER_ID
    if uid is None:
        return (
            "No identity configured.\n\n"
            "Tools act for a Telegram user id: pass `user_id=<id>` on each "
            "call, or set `MCP_USER_ID` in `.env`. To find an id, ask the "
            "bot for `/whoami` in Telegram."
        )

    source = "passed as user_id" if user_id is not None else "MCP_USER_ID (.env)"
    row = await db.get_user(uid)
    lines = [
        f"**{await identity.profile(uid)}**",
        f"id `{uid}` · source: {source}",
    ]

    campaigns = await db.campaigns_for_user(uid)
    lines += ["", f"**Campaigns:** {len(campaigns)}"]

    characters = []
    for campaign in campaigns:
        character = await db.active_character(campaign["id"], uid)
        if character:
            characters.append(
                f"- {esc_md(campaign['name'])} — {esc_md(character['name'])}"
            )
    if characters:
        lines += ["", "**Active characters**"] + characters
    if row is None:
        lines += ["", "_This id has never spoken to the Telegram bot._"]
    return "\n".join(lines)


def get_help(topic: str | None = None) -> str:
    """The bot's own help pages. Telegram: ``/help [dice|campaign|session|character|srd]``.

    No topic gives the overview the bot answers ``/help`` with. The short
    aliases it accepts (``char``, ``campaigns``, ``sessions``) work here too.
    """
    if not topic:
        return to_markdown(WELCOME)

    key = topic.strip().lower()
    page = HELP_PAGES.get(key)
    if page is None:
        raise ToolError(
            f"No help topic {topic!r}. Try one of: " + ", ".join(HELP_TOPICS) + "."
        )
    return to_markdown(page)


def get_tutorial(topic: str | None = None) -> str:
    """The guided walkthrough. Telegram: ``/tutorial [start|setup|join|character|dice|srd|groups]``.

    No topic restarts at the beginning. Aliases behave as they do in
    Telegram: ``dm`` and ``campaign`` open setup, ``characters`` opens
    character.
    """
    if not topic:
        index = 0
    else:
        key = topic.strip().lower()
        key = ALIASES.get(key, key)
        if key not in PAGE_KEYS:
            raise ToolError(
                f"No tutorial page for {topic!r}. Try one of: "
                + ", ".join(PAGE_KEYS)
                + "."
            )
        index = PAGE_KEYS.index(key)
    return to_markdown(TUTORIAL_PAGES[index][1])


def recent_errors(user_id: int | None = None, max_entries: int = 8) -> str:
    """The tail of the shared error log. Telegram: ``/errors`` — admin only.

    The log carries tracebacks and local paths, so it asks for the same
    ``ADMIN_ID`` the bot uses (the command itself is still open to everyone;
    closing it is recommendation 6 of the plan). The reader matches the bot:
    each entry's first line, timestamp and message included, plus the
    exception line below it, newest last.
    """
    uid = identity.actor(user_id)
    if not is_admin(uid):
        raise ToolError(
            f"The error log is admin-only: id {uid} is not ADMIN_ID in .env."
        )

    blocks = error_log(max_entries=max_entries)
    path = errors_path()
    if not blocks:
        return f"✅ No errors logged.\n\nLog file: `{path}`"

    out = []
    for block in blocks:
        lines = [line for line in block.splitlines() if line.strip()]
        head = lines[0] if lines else ""
        exception = ""
        for line in lines[1:]:
            stripped = line.strip()
            if stripped and not stripped.startswith(("File ", "Traceback", "  ")):
                exception = stripped
                break
        entry = esc_md(head)
        if exception and exception != head:
            entry += f"\n*{esc_md(exception)}*"
        out.append(entry)

    size = path.stat().st_size if path.exists() else 0
    return (
        f"🐞 Last {len(out)} error(s)\n\n"
        + "\n\n".join(out)
        + f"\n\n_Full log: {esc_md(path)} ({size:,} bytes)_"
    )


def register(mcp) -> None:
    """Register this module's tools on the server (cleaned docstring first)."""
    for tool in (whoami, get_help, get_tutorial, recent_errors):
        mcp.add_tool(tool, description=cleandoc(tool.__doc__ or ""))
