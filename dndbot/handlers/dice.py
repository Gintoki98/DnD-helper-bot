"""Dice commands and the inline dice pad."""

from __future__ import annotations

from telethon import events

from .. import keyboards as kb
from ..common import display_name, safe
from ..dice import DiceError, RollResult, roll

# user_id -> the expression currently being typed on the dice pad
PAD: dict[int, str] = {}


def describe(result: RollResult) -> str:
    """A human-readable breakdown of a roll."""
    lines = [f"<b>\U0001f3b2 {kb.dice_roll_summary(result)}</b>"]
    if result.advantage:
        word = "Advantage" if result.advantage == "adv" else "Disadvantage"
        lines.append(f"<i>{word}</i>")
    for term in result.terms:
        if not term.count:
            continue
        shown = []
        for die in term.dice:
            mark = "" if die.kept else "\u2717"
            note = f"<i>{die.note}</i>" if die.note else ""
            shown.append(f"<code>{die.value}</code>{mark}{note}")
        lines.append(" ".join(shown) + f"  <i>({term.label})</i>")

    natural = result.natural
    if natural == 20:
        lines.append("\U0001f3c5 <b>Critical hit!</b>")
    elif natural == 1:
        lines.append("\U0001f480 <b>Critical failure.</b>")
    if result.dropped:
        lines.append("<i>\u2717 = discarded</i>")
    return "\n".join(lines)


async def signed_roll(event, expression: str) -> None:
    """Roll an expression, handling crit/fumble rolls and the dice pad."""
    result = roll(expression)
    body = describe(result)
    who = display_name(event.sender)
    text = f"{body}\n<i>{who}</i>"
    await event.reply(
        text,
        buttons=kb.quick_roll_keyboard(result.expression),
        parse_mode="html",
        link_preview=False,
    )


def register(client) -> None:
    @client.on(events.NewMessage(pattern=r"^/roll(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def roll_command(event: events.NewMessage.Event) -> None:
        expression = (event.raw_text.split(None, 1)[1] if len(event.raw_text.split(None, 1)) > 1 else "")
        expression = (expression or "").strip()
        if not expression:
            await event.reply(
                "What should I roll?\n\n"
                "Try <code>/roll 2d6+3</code>, <code>/roll 4d6kh3</code>, "
                "<code>/roll adv</code> or tap the pad below.",
                buttons=kb.dice_keyboard(),
            )
            return
        try:
            await signed_roll(event, expression)
        except DiceError as exc:
            await event.reply(f"\u274c {exc}", parse_mode="html")

    @client.on(events.NewMessage(pattern=r"^/(?:advantage|adv)(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def advantage_command(event: events.NewMessage.Event) -> None:
        extra = (event.raw_text.split(None, 1)[1] if len(event.raw_text.split(None, 1)) > 1 else "").strip()
        await signed_roll(event, f"adv:{extra or 'd20'}")

    @client.on(events.NewMessage(pattern=r"^/(?:disadvantage|dis)(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def disadvantage_command(event: events.NewMessage.Event) -> None:
        extra = (event.raw_text.split(None, 1)[1] if len(event.raw_text.split(None, 1)) > 1 else "").strip()
        await signed_roll(event, f"dis:{extra or 'd20'}")

    @client.on(events.NewMessage(pattern=r"^/init(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def initiative_command(event: events.NewMessage.Event) -> None:
        """Roll initiative for everyone who has a character in this campaign."""
        from ..common import resolve_campaign
        from ..storage import db

        bonus = 0
        parts = event.raw_text.split(None, 1)
        if len(parts) > 1:
            bonus = int(parts[1].strip() or 0)
        try:
            campaign = await resolve_campaign(event)
            party = await db.party(campaign["id"])
        except Exception as exc:
            await event.reply(str(exc), parse_mode="html")
            return
        if not party:
            await event.reply("Nobody in this campaign has a character yet.", parse_mode="html")
            return

        scores = []
        for member in party:
            result = roll(f"d20+{bonus + member['initiative']}")
            scores.append((result.total, member))
        scores.sort(key=lambda pair: pair[0], reverse=True)

        lines = [f"<b>\U0001f3af Initiative for {safe(campaign['name'])}</b>", ""]
        for total, member in scores:
            lines.append(
                f"{total:>3}. <b>{safe(member['name'])}</b> <i>{safe(member['class_name'])}</i> "
                f"<code>{member['initiative']:+d}</code>"
            )
        await event.reply("\n".join(lines), parse_mode="html")

    # -- inline dice pad -------------------------------------------------
    @client.on(events.CallbackQuery(pattern=r"^dice:"))
    async def dice_pad(event: events.CallbackQuery.Event) -> None:
        user = event.sender_id
        payload = event.data.decode().split(":")[1:]
        op = payload[0]
        expression = PAD.get(user, "")

        if op == "open":
            PAD[user] = ""
            await event.edit(
                "\U0001f3b2 <b>Dice pad</b>\n\nBuild an expression, then tap Roll."
                f"\n\n<i>now: {PAD[user] or 'nothing'}</i>",
                buttons=kb.dice_keyboard(PAD[user]),
                parse_mode="html",
            )
            return

        if op.startswith("d") and op[1:].isdigit():
            expression = f"{expression}{expression and '+'}{op}"
        elif op in {"adv", "dis"}:
            expression = f"{op}:d20"
        elif op == "kh3":
            expression = expression or "4d6"
            expression = expression if "kh" in expression else f"{expression}kh3"
        elif op == "op":
            if len(payload) < 2:
                return
            symbol = payload[1]
            expression = f"{expression}{symbol}"
        elif op == "clr":
            expression = ""
        elif op == "noop":
            pass
        elif op == "done":
            PAD[user] = ""
            await event.answer("Closed the dice pad")
            await event.edit("\U0001f5c2 Done.", parse_mode="html")
            return
        elif op == "go":
            candidate = ":".join(payload[1:]) if len(payload) > 1 else ""
            candidate = candidate or expression or "d20"
            PAD[user] = ""
            try:
                result = roll(candidate)
            except DiceError as exc:
                await event.answer(f"\u274c {exc}", alert=True)
                return
            await event.answer(f"\U0001f3b2 {result.total}")
            await event.edit(
                "\U0001f5c2 Pad cleared.",
                parse_mode="html",
            )
            await event.client.send_message(
                event.chat_id,
                f"{describe(result)}\n<i>{display_name(event.sender)}</i>",
                buttons=kb.quick_roll_keyboard(result.expression),
                parse_mode="html",
            )
            return
        elif op == "encounter":
            from .srd_lookup import send_entry

            await event.answer("Searching for a random monster\u2026")
            # replace=False: the message under this button shows a roll, so the
            # monster has to arrive as a new message.
            await send_entry(event, "monsters", "", random_pick=True, replace=False)
            return
        else:
            return

        PAD[user] = expression
        await event.answer(expression or "cleared")
        await event.edit(
            "\U0001f3b2 <b>Dice pad</b>\n\nBuild an expression, then tap Roll."
            f"\n\n<i>now: {PAD[user] or 'nothing'}</i>",
            buttons=kb.dice_keyboard(PAD[user]),
            parse_mode="html",
        )
