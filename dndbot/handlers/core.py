"""Start, help, and the top-level inline menus."""

from __future__ import annotations

from telethon import Button, events

from .. import keyboards as kb
from ..common import command_argument, display_name, is_admin, safe
from ..formatting import esc as escape
from ..help import HELP_PAGES, WELCOME
from ..logs import errors_path, recent_errors
from ..storage import db


def register(client) -> None:
    @client.on(events.NewMessage(pattern=r"^/start(?:@[\w_]+)?$"))
    async def start(event: events.NewMessage.Event) -> None:
        await db.upsert_user(
            event.sender_id,
            username=getattr(event.sender, "username", None),
            first_name=getattr(event.sender, "first_name", None),
            last_name=getattr(event.sender, "last_name", None),
        )
        campaigns = await db.campaigns_for_user(event.sender_id)
        text = WELCOME
        if campaigns:
            lines = "\n".join(
                f"• <b>{safe(row['name'])}</b> <code>{row['invite_code']}</code>"
                + (" <i>DM</i>" if row["role"] == "dm" else "")
                for row in campaigns[:5]
            )
            text += f"\n\n<b>Your campaigns</b>\n{lines}"
        if is_admin(event.sender_id):
            text += "\n\n<i>You are an admin of this bot.</i>"
        await event.reply(text, buttons=kb.main_menu(), parse_mode="html")

    @client.on(events.NewMessage(pattern=r"^/help(?:@[\w_]+)?(?:\s+(\w+))?$"))
    async def help_command(event: events.NewMessage.Event) -> None:
        topic = command_argument(event).lower()
        pages = HELP_PAGES
        if topic and topic in pages:
            await event.reply(f"{pages[topic]}\n\n<i>/help for everything</i>", parse_mode="html")
            return
        if topic and topic not in pages:
            await event.reply(
                f"No help topic <code>{topic}</code>. Try: dice, campaign, session, "
                "character or srd.",
                parse_mode="html",
            )
            return
        await event.reply(
            "<b>What do you need?</b>\n\n"
            "<code>/help dice</code> \u2022 <code>/help campaign</code> \u2022 "
            "<code>/help session</code> \u2022 <code>/help character</code> \u2022 "
            "<code>/help srd</code>",
            buttons=[
                [
                    ("Dice", "menu:help:dice"),
                    ("Campaigns", "menu:help:campaign"),
                ],
                [
                    ("Sessions", "menu:help:session"),
                    ("Characters", "menu:help:character"),
                ],
                [("SRD", "menu:help:srd")],
                [Button.inline("\U0001f4d6 Tutorial", "tut:0")],
            ],
            parse_mode="html",
        )

    @client.on(events.NewMessage(pattern=r"^/whoami(?:@[\w_]+)?$"))
    async def whoami(event: events.NewMessage.Event) -> None:
        campaigns = await db.campaigns_for_user(event.sender_id)
        characters = []
        for campaign in campaigns:
            character = await db.active_character(campaign["id"], event.sender_id)
            if character:
                characters.append(f"• <i>{safe(campaign['name'])}</i> \u2014 {safe(character['name'])}")
        text = [
            f"You are <b>{display_name(event.sender)}</b>",
            f"<code>{event.sender_id}</code>",
            f"<b>Campaigns:</b> {len(campaigns)}",
        ]
        if characters:
            text += ["", "<b>Characters</b>"] + characters
        await event.reply("\n".join(text), parse_mode="html")

    @client.on(events.NewMessage(pattern=r"^/errors(?:@[\w_]+)?$"))
    async def errors_command(event: events.NewMessage.Event) -> None:
        """Show the tail of the error log. Anyone may read it."""
        entries = recent_errors(max_entries=8)
        path = errors_path()
        if not entries:
            await event.reply(
                "\u2705 <b>No errors logged.</b>\n\n"
                f"Log file: <code>{path}</code>",
                parse_mode="html",
            )
            return

        blocks = []
        for block in entries:
            # Keep the first line (timestamp + message) and the exception line.
            lines = [line for line in block.splitlines() if line.strip()]
            head = lines[0] if lines else ""
            exception = ""
            for line in lines[1:]:
                stripped = line.strip()
                if stripped and not stripped.startswith(("File ", "Traceback", "  ")):
                    exception = stripped
                    break
            entry = escape(head)
            if exception and exception != head:
                entry += f"\n<i>{escape(exception)}</i>"
            blocks.append(entry)

        body = "\n\n".join(blocks)
        if len(body) > 3800:
            body = body[:3800] + "\n<i>truncated</i>"

        size = path.stat().st_size if path.exists() else 0
        await event.reply(
            f"\U0001f41e <b>Last {len(entries)} error(s)</b>\n\n{body}\n\n"
            f"<i>Full log: {path} ({size:,} bytes)</i>",
            parse_mode="html",
        )

    @client.on(events.CallbackQuery(pattern=r"^menu:"))
    async def menus(event: events.CallbackQuery.Event) -> None:
        _, target, *rest = event.data.decode().split(":")
        if target == "home":
            await event.edit(WELCOME, buttons=kb.main_menu(), parse_mode="html")
            await event.answer()
            return
        if target == "srd":
            await event.edit(
                "<b>\U0001f5c2\ufe0f SRD 5.2.1</b>\n\nPick a category, or use commands like "
                "<code>/spell fireball</code>.",
                buttons=kb.srd_menu(),
                parse_mode="html",
            )
            await event.answer()
            return
        if target == "help":
            page = HELP_PAGES.get(rest[0] if rest else "", HELP_PAGES["dice"])
            await event.edit(page, buttons=kb.main_menu(), parse_mode="html")
            await event.answer()
            return
        if target == "search":
            await event.edit(
                "\U0001f50d Send what you are looking for:\n<code>/search fire</code>",
                parse_mode="html",
            )
            await event.answer()
            return
        await event.answer("Nothing here.", alert=True)
