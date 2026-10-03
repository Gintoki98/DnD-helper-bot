"""Character tools: the sheet commands of the bot, without the conversation.

The rules live in ``dndbot/sheet.py`` (HP arithmetic, level rules, which
fields ``/set`` takes, the sheet itself) and the selection flows in
``dndbot/services.py``; this module resolves who and where, runs the rule and
speaks the answer in Markdown - Telegram HTML stays behind ``render.py``.
"""

from __future__ import annotations

from inspect import cleandoc

from mcp.server.mcpserver.exceptions import ToolError

from ..services import character_for, switch_to
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
from ..storage import ability_modifier, db
from . import inputs
from .render import esc_md, strip, to_markdown


async def _sheet(user_id: int | None, campaign: str | None, name: str | None):
    """``(actor, campaign, character)`` for a call, or a ToolError explaining why not."""
    uid, row = await inputs.member(user_id, campaign)
    try:
        character = await character_for(uid, row, name or "")
    except LookupError as exc:
        raise ToolError(strip(exc)) from exc
    return uid, row, character


async def create_character(
    name: str,
    class_name: str = "",
    race: str = "",
    background: str = "",
    subclass: str = "",
    level: int = 1,
    strength: int = 10,
    dexterity: int = 10,
    constitution: int = 10,
    intelligence: int = 10,
    wisdom: int = 10,
    charisma: int = 10,
    ac: int = 10,
    max_hp: int = 10,
    speed: int = 30,
    force: bool = False,
    user_id: int | None = None,
    campaign: str | None = None,
) -> str:
    """Create a character in one call. Telegram: the six-step ``/newchar`` wizard.

    Everything the wizard asks for is an argument here: the six abilities are
    1-30 and default to 10, ``level`` runs 1-20, ``max_hp`` is at least 1
    (``hp`` starts equal to it) and initiative is derived from DEX. The new
    sheet becomes your active one. If you already have an active sheet, pass
    ``force: true`` the way ``/newchar force`` does.
    """
    uid, row = await inputs.member(user_id, campaign)

    existing = await db.active_character(row["id"], uid)
    if existing and not force:
        raise ToolError(
            f"You are already playing {existing['name']} here. "
            "Pass force: true to start another, or use switch_character."
        )

    label = (name or "").strip()
    if not label:
        raise ToolError("Give the character a name.")
    if not 1 <= level <= 20:
        raise ToolError("Levels run from 1 to 20 in the SRD.")
    scores = {
        "str": strength,
        "dex": dexterity,
        "con": constitution,
        "intl": intelligence,
        "wis": wisdom,
        "cha": charisma,
    }
    for key, value in scores.items():
        if not 1 <= value <= 30:
            raise ToolError(f"Scores must be between 1 and 30 - {value} is not.")

    character = await db.create_character(
        campaign_id=row["id"],
        user_id=uid,
        name=label[:60],
        class_name=class_name,
        race=race,
        background=background,
        subclass=subclass,
        level=level,
        hp=max(1, max_hp),
        max_hp=max(1, max_hp),
        ac=ac,
        speed=speed,
        initiative=ability_modifier(dexterity),
        **scores,
    )
    await db.log_event(character["id"], "created", f"level {character['level']}", uid)
    await db.touch_campaign(row["id"])
    return f"\U0001f389 {esc_md(character['name'])} is ready.\n\n" + to_markdown(
        stat_block(character)
    )


async def get_character(
    name: str | None = None, user_id: int | None = None, campaign: str | None = None
) -> str:
    """A character sheet. Telegram: ``/char [name]``.

    Without ``name`` it is yours (the active sheet); with one it is whoever
    matches in the campaign. The owner also gets the last five events.
    """
    uid, _row, character = await _sheet(user_id, campaign, name)
    owner = character["user_id"] == uid
    log = await db.events(character["id"], 5) if owner else None
    return to_markdown(stat_block(character, None if owner else uid, log))


async def list_party(
    user_id: int | None = None, campaign: str | None = None
) -> str:
    """Every active character in the campaign, with the party's total HP.
    Telegram: ``/party``."""
    _uid, row = await inputs.member(user_id, campaign)
    party = await db.party(row["id"])
    if not party:
        raise ToolError("Nobody has a character here yet. Players use create_character.")
    return to_markdown(party_block(party, row["name"]))


async def change_hp(
    delta: int,
    reason: str = "",
    name: str | None = None,
    user_id: int | None = None,
    campaign: str | None = None,
) -> str:
    """Damage or heal the sheet. Telegram: ``/hp -7 [reason]`` (``/hp +3`` heals).

    Temporary HP soak damage first, damage past 0 is wasted and healing stops
    at max HP - the same arithmetic ``/hp`` does.
    """
    uid, _row, character = await _sheet(user_id, campaign, name)
    new_hp, temp, wasted = apply_hp_delta(
        character["hp"], character["max_hp"], character["temp_hp"], delta
    )
    await db.update_character(character["id"], hp=new_hp, temp_hp=temp)
    why = reason.strip()
    await db.log_event(
        character["id"],
        "HP" if delta > 0 else "damage",
        f"{delta:+d} \u2192 {new_hp}/{character['max_hp']}"
        + (f" ({why})" if why else ""),
        uid,
    )
    return to_markdown(hp_reply(character, delta, new_hp, temp, wasted))


async def set_hp(
    hp: int,
    max_hp: int | None = None,
    name: str | None = None,
    user_id: int | None = None,
    campaign: str | None = None,
) -> str:
    """Set current and max hit points outright. Telegram: ``/sethp 38 52``
    (one number sets both current and max)."""
    _uid, _row, character = await _sheet(user_id, campaign, name)
    maximum = hp if max_hp is None else max_hp
    await db.update_character(character["id"], hp=hp, max_hp=maximum)
    await db.log_event(character["id"], "set HP", f"{hp}/{maximum}", _uid)
    return (
        f"\U0001f49a **{esc_md(character['name'])}** set to "
        f"`{hp}/{maximum}` HP."
    )


async def level_up(
    level: int | None = None,
    name: str | None = None,
    user_id: int | None = None,
    campaign: str | None = None,
) -> str:
    """Gain a level (or jump straight to one). Telegram: ``/levelup`` or
    ``/level 5``.

    Levels only go up here and must stay within 1-20; going down is what
    ``set_character_field`` is for. The sheet grows by the average HP for the
    levels gained - set the exact number with ``set_hp``.
    """
    uid, _row, character = await _sheet(user_id, campaign, name)
    old_level = character["level"]
    new_level = level if level else old_level + 1
    refused = level_change_error(old_level, new_level)
    if refused:
        raise ToolError(strip(refused))

    await db.update_character(character["id"], level=new_level)
    await db.log_event(character["id"], "level", f"{old_level} \u2192 {new_level}", uid)
    gain = level_up_gain(old_level, new_level)
    return (
        f"\U0001f31f **{esc_md(character['name'])}** is now level **{new_level}**.\n"
        f"Average HP gain \u2248 {gain} - set the exact number with set_hp."
    )


async def set_xp(
    xp: int,
    name: str | None = None,
    user_id: int | None = None,
    campaign: str | None = None,
) -> str:
    """Set total experience points. Telegram: ``/xp 3200``."""
    _uid, _row, character = await _sheet(user_id, campaign, name)
    await db.update_character(character["id"], xp=xp)
    await db.log_event(character["id"], "XP", f"set to {xp}", _uid)
    return f"\U0001f4c8 **{esc_md(character['name'])}** now has **{xp:,}** XP."


async def set_character_field(
    field: str,
    value: int,
    name: str | None = None,
    user_id: int | None = None,
    campaign: str | None = None,
) -> str:
    """Set a numeric field of the sheet. Telegram: ``/set ac 16``.

    Fields: ``ac``, ``speed``, ``init``, ``gold``, ``level``, ``temp`` and the
    abilities ``str``/``dex``/``con``/``int``/``wis``/``cha`` (full names or
    the usual abbreviations). Going *down* a level is allowed here; that is
    what the DM uses.
    """
    key = (field or "").strip().lower()
    if key not in SET_FIELD_COLUMNS:
        raise ToolError(
            f"I do not track {field!r}. "
            "Try one of: ac, speed, init, gold, level, temp, str, dex, con, int, wis, cha."
        )
    uid, _row, character = await _sheet(user_id, campaign, name)
    column = SET_FIELD_COLUMNS[key]
    await db.update_character(character["id"], **{column: value})
    await db.log_event(character["id"], field, f"set to {value}", uid)
    modifier = f" Modifier {ability_modifier(value):+d}." if column in ABBREV.values() else ""
    return (
        f"\u2705 **{esc_md(character['name'])}** \u2014 {key} set to **{value}**."
        f"{modifier}"
    )


async def add_note(
    text: str,
    name: str | None = None,
    user_id: int | None = None,
    campaign: str | None = None,
) -> str:
    """Replace the sheet's notes. Telegram: ``/note <text>`` (it overwrites,
    it does not append)."""
    _uid, _row, character = await _sheet(user_id, campaign, name)
    await db.update_character(character["id"], notes=text)
    return f"\U0001f4dd Note saved on **{esc_md(character['name'])}**."


async def switch_character(
    name: str | None = None, user_id: int | None = None, campaign: str | None = None
) -> str:
    """Make another of your sheets the active one. Telegram: ``/switch [Name]``.

    Without ``name`` it lists your characters in the campaign; with one it
    switches to the first you own that matches. The active sheet is what
    every other character tool edits when ``name`` is left out.
    """
    uid, row = await inputs.member(user_id, campaign)
    active, message = await switch_to(uid, row, name or "")
    if active is None and name:
        raise ToolError(strip(message))
    return to_markdown(message)


def register(mcp) -> None:
    """Register this module's tools on the server (cleaned docstring first)."""
    for tool in (
        create_character,
        get_character,
        list_party,
        change_hp,
        set_hp,
        level_up,
        set_xp,
        set_character_field,
        add_note,
        switch_character,
    ):
        mcp.add_tool(tool, description=cleandoc(tool.__doc__ or ""))
