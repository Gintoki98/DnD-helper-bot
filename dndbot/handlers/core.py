"""Start, help, and the top-level inline menus."""

from __future__ import annotations

from telethon import Button, events

from .. import keyboards as kb
from ..common import display_name, is_admin, safe
from ..formatting import esc as escape
from ..storage import db

HELP_DICE = (
    "<b>\U0001f3b2 Dice</b>\n"
    "<code>/roll 2d6+3</code> \u2014 any notation\n"
    "<code>/roll 4d6kh3</code> \u2014 roll four, keep the best three\n"
    "<code>/adv</code> / <code>/dis</code> \u2014 advantage roll (add your own modifier)\n"
    "<code>/init</code> \u2014 initiative for the whole party\n"
    "<i>Also: 2d20kl1 (keep lowest), 4d6r&lt;=1 (reroll ones), 2d6!&gt;=6 (exploding).</i>"
)

HELP_CAMPAIGN = (
    "<b>\U0001f3dd Campaigns</b>\n"
    "<code>/newcampaign Name | blurb</code> \u2014 become the DM\n"
    "<code>/join ABC123</code> \u2014 ask to join (the DM approves)\n"
    "<code>/campaigns</code> \u2014 everything you are in\n"
    "<code>/select</code> \u2014 switch your active campaign\n"
    "<code>/roster</code> \u2014 who is in the party\n"
    "<code>/leave</code> \u2014 walk away"
)

HELP_SESSION = (
    "<b>\U0001f5c3\ufe0f Sessions</b>\n"
    "<code>/startsession</code> \u2014 DM only\n"
    "<code>/checkin</code> \u2014 sit at the table\n"
    "<code>/who</code> \u2014 who is here\n"
    "<code>/endsession [notes]</code> \u2014 DM only, records the length"
)

HELP_CHARACTER = (
    "<b>\U0001f9d9 Characters</b>\n"
    "<code>/newchar</code> \u2014 guided creation, six questions\n"
    "<code>/char</code> \u2014 your sheet (or <code>/char Name</code>)\n"
    "<code>/hp -7</code> / <code>/hp +3</code> \u2014 damage and healing\n"
    "<code>/sethp 38</code> / <code>/sethp 38 52</code> \u2014 set current / max\n"
    "<code>/levelup</code> / <code>/level 5</code> \u2014 levels\n"
    "<code>/xp 1250</code> \u2014 experience\n"
    "<code>/set ac 16</code> \u2014 or speed, init, gold, str\u2026cha\n"
    "<code>/note text</code> \u2014 sticky note on the sheet\n"
    "<code>/party</code> \u2014 the whole party's vitals\n"
    "<code>/switch Name</code> \u2014 change active character"
)

HELP_SRD = (
    "<b>\U0001f5c2\ufe0f SRD lookup</b>\n"
    "<code>/monster goblin</code> \u2014 full stat block\n"
    "<code>/spell fireball</code> \u2014 casting, damage, classes\n"
    "<code>/item adamantine armor</code> \u2014 magic items\n"
    "<code>/equipment longsword</code> \u2014 mundane gear\n"
    "<code>/rule long rest</code> \u2014 rules reference\n"
    "<code>/class wizard</code> \u2022 <code>/race elf</code> \u2022 <code>/condition blinded</code>\n"
    "<code>/search anything</code> \u2014 search everything at once\n"
    "<code>/randmonster 2</code> \u2014 random monster by CR (or <code>/randmonster 1/4</code>)\n"
    "<code>/randspell</code> \u2014 random spell\n"
    "<i>Fuzzy matching: <code>/monster ancient red drgn</code> finds the red dragon.</i>"
)

WELCOME = (
    "\U0001f3df\ufe0f <b>D&amp;D Helper</b>\n\n"
    "Dice, campaign sessions, character sheets and the whole SRD at your table.\n\n"
    "Start here: <code>/newcampaign Your Campaign</code> and share the invite code, "
    "or tap a button below.\n\n"
    "<i>New? Send /tutorial for a guided walkthrough.</i>"
)


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
        parts = event.raw_text.split(None, 1)
        topic = parts[1].strip().lower() if len(parts) > 1 else ""
        pages = {
            "dice": HELP_DICE,
            "campaign": HELP_CAMPAIGN,
            "campaigns": HELP_CAMPAIGN,
            "session": HELP_SESSION,
            "sessions": HELP_SESSION,
            "char": HELP_CHARACTER,
            "character": HELP_CHARACTER,
            "characters": HELP_CHARACTER,
            "srd": HELP_SRD,
        }
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
        from ..logs import errors_path, recent_errors

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
            page = {
                "dice": HELP_DICE,
                "campaign": HELP_CAMPAIGN,
                "session": HELP_SESSION,
                "character": HELP_CHARACTER,
                "srd": HELP_SRD,
            }.get(rest[0] if rest else "", HELP_DICE)
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
