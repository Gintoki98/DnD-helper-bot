"""Character sheets: guided creation, viewing and quick edits."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from telethon import events

from .. import keyboards as kb
from ..common import (
    NoCampaign,
    NotAMember,
    command_argument,
    display_name,
    ensure_member,
    member_campaign,
    resolve_campaign,
    safe,
)
from ..sheet import (
    ABBREV,
    SET_FIELD_COLUMNS,
    apply_hp_delta,
    hp_reply,
    level_change_error,
    level_up_gain,
    party_block,
    stat_block,
)
from ..services import character_for, switch_to
from ..srd import SRDError, srd
from ..storage import ability_modifier, db

# user_id -> in-flight character creation
DRAFT: dict[int, dict] = {}
DRAFT_TTL = 600

# Test hook. Flipped by tests/test_flow.py to force a handler failure so the
# error log and the guard's behaviour can be asserted.
BROKEN = False


async def _ask_class(event) -> None:
    """Prompt for the character's class, listing the SRD classes."""
    hint = ""
    try:
        classes = await srd.index("classes")
        hint = "\n\n<i>SRD classes: " + ", ".join(c.name for c in classes) + "</i>"
    except SRDError:
        pass
    await event.reply(
        "<b>2/6</b> Class and subclass?\n"
        "<i>e.g. \"Rogue\" or \"Wizard / School of Evocation\"</i>" + hint,
        parse_mode="html",
    )


async def load_character(event, name: str = "") -> tuple[Any, Any]:
    """Find the character a command refers to: an explicit name or the active one."""
    if BROKEN:  # test hook: see tests/test_flow.py
        raise RuntimeError("deliberate failure for the error-log test")
    campaign = await resolve_campaign(event)
    await ensure_member(campaign, event.sender_id)
    return campaign, await character_for(event.sender_id, campaign, name)


async def character_or_reply(event, name: str = "") -> Any:
    """The character a command targets, or ``None`` after replying with the reason.

    Wraps :func:`load_character` for commands: instead of every handler
    repeating the same ``try``/``except`` and reply, they guard one value.
    """
    try:
        _campaign, character = await load_character(event, name)
    except (LookupError, NoCampaign, NotAMember) as exc:
        await event.reply(str(exc), parse_mode="html")
        return None
    return character


# -- guided creation steps ------------------------------------------------
ABILITY_KEYS = ("str", "dex", "con", "intl", "wis", "cha")


def _advance(draft: dict, step: str, loop) -> None:
    """Move the wizard to its next question and restart the idle timer."""
    draft["step"] = step
    draft["at"] = loop.time()


def _split_choice(answer: str) -> tuple[str, str]:
    """Split ``"Half-elf / Urchin"`` into its two halves (either may be empty)."""
    parts = answer.split("/", 1)
    main = parts[0].strip()[:60]
    extra = parts[1].strip()[:60] if len(parts) > 1 else ""
    return main, extra


async def _step_name(event, draft: dict, answer: str, loop) -> None:
    """Question 1/6: the character's name."""
    draft["data"]["name"] = answer[:60]
    _advance(draft, "class", loop)
    await _ask_class(event)


async def _step_class(event, draft: dict, answer: str, loop) -> None:
    """Question 2/6: class and optional subclass."""
    draft["data"]["class_name"], draft["data"]["subclass"] = _split_choice(answer)
    _advance(draft, "origin", loop)
    hint = ""
    try:
        races = [r.name for r in await srd.index("races")]
        subraces = [r.name for r in await srd.index("subraces")]
        hint = f"\n\n<i>SRD races: {', '.join(races)}\nSubraces: {', '.join(subraces)}</i>"
    except SRDError:
        pass
    await event.reply(
        "<b>3/6</b> Race and background?\n"
        "<i>e.g. \"Half-elf / Urchin\" or just \"Half-elf\"</i>" + hint,
        parse_mode="html",
    )


async def _step_origin(event, draft: dict, answer: str, loop) -> None:
    """Question 3/6: race and background."""
    draft["data"]["race"], draft["data"]["background"] = _split_choice(answer)
    _advance(draft, "level", loop)
    await event.reply(
        "<b>4/6</b> What level did you start at? (1-20)\n<i>e.g. <code>1</code></i>",
        parse_mode="html",
    )


async def _step_level(event, draft: dict, answer: str, loop) -> None:
    """Question 4/6: the starting level. Anything outside 1-20 is re-asked."""
    level = re.search(r"\d+", answer)
    if not level or not 1 <= int(level.group()) <= 20:
        await event.reply("Give me a number from 1 to 20.", parse_mode="html")
        return
    draft["data"]["level"] = int(level.group())
    _advance(draft, "abilities", loop)
    await event.reply(
        "<b>5/6</b> Your ability scores.\n\n"
        "Send all six, space separated, in this order:\n"
        "<code>STR DEX CON INT WIS CHA</code>\n\n"
        "<i>e.g. <code>10 16 14 12 13 8</code>  \u2022  anything you leave out becomes 10</i>",
        parse_mode="html",
    )


async def _step_abilities(event, draft: dict, answer: str, loop) -> None:
    """Question 5/6: six scores in order, defaulting the missing ones to 10."""
    numbers = [int(n) for n in re.findall(r"\d+", answer)][:6]
    while len(numbers) < 6:
        numbers.append(10)
    if any(not 1 <= value <= 30 for value in numbers):
        bad = [v for v in numbers if not 1 <= v <= 30][0]
        await event.reply(
            f"Scores must be between 1 and 30 - <code>{bad}</code> is not.",
            parse_mode="html",
        )
        return
    for key, value in zip(ABILITY_KEYS, numbers):
        draft["data"][key] = value
    _advance(draft, "defenses", loop)
    await event.reply(
        "<b>6/6</b> Last one: AC, max HP and speed.\n\n"
        "<code>16 40 30</code>  \u2192  AC 16, max HP 40, speed 30 ft.\n"
        "<i>Send <code>skip</code> for AC 10, HP 10, speed 30.</i>",
        parse_mode="html",
    )


async def _step_defenses(event, draft: dict, answer: str, loop) -> None:
    """Question 6/6: AC, hit points and speed - then create the character."""
    if answer.lower() in {"skip", "none"}:
        numbers = [10, 10, 30]
    else:
        numbers = [int(n) for n in re.findall(r"\d+", answer)]
    ac = numbers[0] if len(numbers) > 0 else 10
    max_hp = numbers[1] if len(numbers) > 1 else 10
    speed = numbers[2] if len(numbers) > 2 else 30
    max_hp = max(1, max_hp)
    data = draft["data"]
    data.update(
        ac=ac,
        max_hp=max_hp,
        hp=max_hp,
        speed=speed,
        initiative=ability_modifier(data.get("dex", 10)),
    )
    DRAFT.pop(event.sender_id, None)

    character = await db.create_character(
        campaign_id=draft["campaign_id"],
        user_id=event.sender_id,
        **data,
    )
    await db.log_event(
        character["id"], "created", f"level {character['level']}", event.sender_id
    )
    await db.touch_campaign(draft["campaign_id"])

    await event.reply(
        f"\U0001f389 <b>{safe(character['name'])}</b> is ready.\n\n" + stat_block(character),
        buttons=kb.character_keyboard(character["id"], True),
        parse_mode="html",
    )


CREATION_STEPS = {
    "name": _step_name,
    "class": _step_class,
    "origin": _step_origin,
    "level": _step_level,
    "abilities": _step_abilities,
    "defenses": _step_defenses,
}


def register(client) -> None:
    # ------------------------------------------------------------------
    # commands
    # ------------------------------------------------------------------
    @client.on(events.NewMessage(pattern=r"^/newchar(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def new_character(event: events.NewMessage.Event) -> None:
        argument = command_argument(event)
        forced = False
        if argument.lower().startswith("force"):
            forced = True
            argument = argument[5:].strip()

        campaign = await member_campaign(event)
        if campaign is None:
            return

        existing = await db.active_character(campaign["id"], event.sender_id)
        if existing and not forced:
            await event.reply(
                f"You are already playing <b>{safe(existing['name'])}</b> here.\n\n"
                "<code>/newchar force Name</code> starts another (it becomes your active sheet), "
                "or <code>/switch &lt;name&gt;</code> to pick an old one.",
                parse_mode="html",
            )
            return

        DRAFT[event.sender_id] = {
            "campaign_id": campaign["id"],
            "step": "class" if argument else "name",
            "data": {"name": argument[:60]} if argument else {},
            "at": asyncio.get_event_loop().time(),
        }
        if argument:
            await _ask_class(event)
            return
        await event.reply(
            f"\U0001f9d9 <b>New character for {safe(campaign['name'])}</b>\n\n"
            "<b>1/6</b> What is the character's name?",
            parse_mode="html",
        )

    @client.on(events.NewMessage(pattern=r"^/char(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def show_character(event: events.NewMessage.Event) -> None:
        name = command_argument(event)
        character = await character_or_reply(event, name)
        if character is None:
            return

        owner = character["user_id"] == event.sender_id
        log = await db.events(character["id"], 5) if owner else None
        await event.reply(
            stat_block(character, None if owner else event.sender_id, log),
            buttons=kb.character_keyboard(character["id"], owner),
            parse_mode="html",
        )

    @client.on(events.NewMessage(pattern=r"^/party(?:@[\w_]+)?$"))
    async def party_sheet(event: events.NewMessage.Event) -> None:
        campaign = await member_campaign(event)
        if campaign is None:
            return

        party = await db.party(campaign["id"])
        if not party:
            await event.reply(
                "Nobody has a character here yet. Players use <code>/newchar</code>.",
                parse_mode="html",
            )
            return

        await event.reply(party_block(party, campaign["name"]), parse_mode="html")

    @client.on(events.NewMessage(pattern=r"^/hp(?:@[\w_]+)?\s+([+-]?\d+)(?:\s+(.*))?$"))
    async def change_hp(event: events.NewMessage.Event) -> None:
        match = re.match(r"^/hp\s+([+-]?\d+)(?:\s+(.*))?$", event.raw_text.strip(), re.S)
        if not match:
            await event.reply(
                "Heal or damage a character:\n"
                "<code>/hp -7</code>  \u2022  <code>/hp +3</code>  \u2022  "
                "<code>/hp -7 bite from the wolf</code>",
                parse_mode="html",
            )
            return
        delta = int(match.group(1))
        reason = (match.group(2) or "").strip()

        character = await character_or_reply(event)
        if character is None:
            return

        new_hp, temp, wasted = apply_hp_delta(
            character["hp"], character["max_hp"], character["temp_hp"], delta
        )

        await db.update_character(character["id"], hp=new_hp, temp_hp=temp)
        await db.log_event(
            character["id"],
            "HP" if delta > 0 else "damage",
            f"{delta:+d} \u2192 {new_hp}/{character['max_hp']}"
            + (f" ({reason})" if reason else ""),
            event.sender_id,
        )

        await event.reply(
            hp_reply(character, delta, new_hp, temp, wasted),
            buttons=kb.character_keyboard(character["id"], True),
            parse_mode="html",
        )

    @client.on(events.NewMessage(pattern=r"^/(?:level|levelup)(?:@[\w_]+)?(?:\s+(\d+))?$"))
    async def change_level(event: events.NewMessage.Event) -> None:
        argument = command_argument(event)
        target = int(argument) if argument.isdigit() else 0
        character = await character_or_reply(event)
        if character is None:
            return

        old_level = character["level"]
        new_level = target or old_level + 1
        refused = level_change_error(old_level, new_level)
        if refused:
            await event.reply(refused, parse_mode="html")
            return

        await db.update_character(character["id"], level=new_level)
        await db.log_event(
            character["id"], "level", f"{old_level} \u2192 {new_level}", event.sender_id
        )
        gain = level_up_gain(old_level, new_level)
        await event.reply(
            f"\U0001f31f <b>{safe(character['name'])}</b> is now level <b>{new_level}</b>.\n"
            f"Average HP gain \u2248 {gain} - adjust it with <code>/sethp 38</code>.",
            buttons=kb.character_keyboard(character["id"], True),
            parse_mode="html",
        )

    @client.on(events.NewMessage(pattern=r"^/sethp(?:@[\w_]+)?\s+(\d+)(?:\s+(\d+))?$"))
    async def set_hp(event: events.NewMessage.Event) -> None:
        match = re.match(r"^/sethp\s+(\d+)(?:\s+(\d+))?$", event.raw_text.strip())
        if not match:
            await event.reply("Set current and max HP: <code>/sethp 38 52</code>", parse_mode="html")
            return
        hp, max_hp = int(match.group(1)), int(match.group(2) or match.group(1))
        character = await character_or_reply(event)
        if character is None:
            return
        await db.update_character(character["id"], hp=hp, max_hp=max_hp)
        await db.log_event(character["id"], "set HP", f"{hp}/{max_hp}", event.sender_id)
        await event.reply(
            f"\U0001f49a <b>{safe(character['name'])}</b> set to <code>{hp}/{max_hp}</code> HP.",
            parse_mode="html",
        )

    @client.on(events.NewMessage(pattern=r"^/xp(?:@[\w_]+)?\s+(\d+)$"))
    async def set_xp(event: events.NewMessage.Event) -> None:
        xp = int(re.search(r"(\d+)$", event.raw_text).group(1))
        character = await character_or_reply(event)
        if character is None:
            return
        await db.update_character(character["id"], xp=xp)
        await db.log_event(character["id"], "XP", f"set to {xp}", event.sender_id)
        await event.reply(
            f"\U0001f4c8 <b>{safe(character['name'])}</b> now has <b>{xp:,}</b> XP.",
            parse_mode="html",
        )

    @client.on(events.NewMessage(pattern=r"^/set(?:@[\w_]+)?\s+(\w+)\s+(-?\d+)$"))
    async def set_field(event: events.NewMessage.Event) -> None:
        match = re.match(r"^/set\s+(\w+)\s+(-?\d+)$", event.raw_text.strip(), re.I)
        if not match:
            await event.reply(
                "Set a numeric field:\n"
                "<code>/set ac 16</code> \u2022 <code>/set speed 35</code> \u2022 "
                "<code>/set init 3</code> \u2022 <code>/set gold 500</code> \u2022 "
                "<code>/set str 16</code>",
                parse_mode="html",
            )
            return
        field, value = match.group(1).lower(), int(match.group(2))
        if field not in SET_FIELD_COLUMNS:
            await event.reply(
                f"I do not track <code>{field}</code>.\n"
                "Try: ac, speed, init, gold, level, temp, str, dex, con, int, wis, cha.",
                parse_mode="html",
            )
            return
        character = await character_or_reply(event)
        if character is None:
            return
        column = SET_FIELD_COLUMNS[field]
        await db.update_character(character["id"], **{column: value})
        await db.log_event(character["id"], field, f"set to {value}", event.sender_id)
        await event.reply(
            f"\u2705 <b>{safe(character['name'])}</b> \u2014 {field} set to <b>{value}</b>."
            + (f" Modifier {ability_modifier(value):+d}." if column in ABBREV.values() else ""),
            buttons=kb.character_keyboard(character["id"], True),
            parse_mode="html",
        )

    @client.on(events.NewMessage(pattern=r"^/switch(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def switch_character(event: events.NewMessage.Event) -> None:
        campaign = await member_campaign(event)
        if campaign is None:
            return
        _character, message = await switch_to(
            event.sender_id, campaign, command_argument(event)
        )
        await event.reply(message, parse_mode="html")

    @client.on(events.NewMessage(pattern=r"^/note(?:@[\w_]+)?\s+(.*)$"))
    async def set_note(event: events.NewMessage.Event) -> None:
        note = command_argument(event)
        character = await character_or_reply(event)
        if character is None:
            return
        await db.update_character(character["id"], notes=note)
        await event.reply(
            f"\U0001f4dd Note saved on <b>{safe(character['name'])}</b>.", parse_mode="html"
        )

    # ------------------------------------------------------------------
    # guided creation
    # ------------------------------------------------------------------
    @client.on(events.NewMessage(pattern=r"^(?!/).*$"))
    async def creation_step(event: events.NewMessage.Event) -> None:
        draft = DRAFT.get(event.sender_id)
        if draft is None:
            return
        loop = asyncio.get_event_loop()
        if loop.time() - draft["at"] > DRAFT_TTL:
            DRAFT.pop(event.sender_id, None)
            await event.reply("Character creation timed out. Send <code>/newchar</code> to start again.")
            return
        if event.is_reply:
            return

        answer = event.raw_text.strip()
        if answer.lower() in {"cancel", "stop", "quit"}:
            DRAFT.pop(event.sender_id, None)
            await event.reply("Dropped. <code>/newchar</code> starts over.", parse_mode="html")
            return

        step = draft["step"]
        handler = CREATION_STEPS.get(step)
        if handler is None:
            return
        await handler(event, draft, answer, loop)

    # ------------------------------------------------------------------
    # callbacks
    # ------------------------------------------------------------------
    @client.on(events.CallbackQuery(pattern=r"^char:"))
    async def character_buttons(event: events.CallbackQuery.Event) -> None:
        parts = event.data.decode().split(":")
        action = parts[1]

        if action == "open":
            try:
                _campaign, character = await load_character(event)
            except (LookupError, NoCampaign, NotAMember) as exc:
                await event.answer(str(exc)[:200], alert=True)
                return
            log = await db.events(character["id"], 5)
            await event.edit(
                stat_block(character, None, log),
                buttons=kb.character_keyboard(character["id"], True),
                parse_mode="html",
            )
            await event.answer()
            return

        if action == "of":
            # DM tapping a roster member: show that player's active character.
            user_id, campaign_id = int(parts[2]), int(parts[3])
            character = await db.active_character(campaign_id, user_id)
            if character is None:
                await event.answer("That player has no character here.", alert=True)
                return
            log = await db.events(character["id"], 5)
            await event.edit(
                stat_block(character, display_name(event.sender), log),
                buttons=kb.character_keyboard(character["id"], False),
                parse_mode="html",
            )
            await event.answer()
            return

        if action == "party":
            campaign = await db.get_campaign(int(parts[2]))
            if campaign is None:
                await event.answer("Gone.", alert=True)
                return
            party = await db.party(campaign["id"])
            if not party:
                await event.answer("No characters yet.", alert=True)
                return
            lines = [f"<b>\U0001f465 {safe(campaign['name'])}</b>", ""]
            for member in party:
                lines.append(
                    f"• <b>{safe(member['name'])}</b> <i>{safe(member['class_name'])}</i> "
                    f"L{member['level']} \u2022 HP {member['hp']}/{member['max_hp']} "
                    f"\u2022 AC {member['ac']}"
                )
            await event.edit("\n".join(lines), buttons=None, parse_mode="html")
            await event.answer()
            return

        character_id = int(parts[2]) if len(parts) > 2 else 0
        character = await db.get_character(character_id)
        if character is None:
            await event.answer("That character is gone.", alert=True)
            return

        if action == "view":
            log = await db.events(character_id, 5)
            await event.edit(
                stat_block(character, None, log),
                buttons=kb.character_keyboard(character_id, True),
                parse_mode="html",
            )
            await event.answer()
            return

        if action in {"hp", "level", "xp"}:
            hints = {
                "hp": "Send <code>/hp -7</code> or <code>/hp +4</code>",
                "level": "Send <code>/levelup</code> or <code>/level 5</code>",
                "xp": "Send <code>/xp 1250</code>",
            }
            await event.answer(hints[action], alert=True)
            return

        await event.answer("Nothing to do here.", alert=True)
