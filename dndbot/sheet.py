"""Character sheet rules and rendering, free of Telegram event plumbing.

The handlers own the conversation - what to ask, when to reply, which buttons
to show. What a sheet *is* lives here instead, so the MCP server applies
exactly the same rules without importing a handler module.

Everything renders to Telegram HTML; the MCP server converts it with
``dndbot/mcp/render.py``.
"""

from __future__ import annotations

from typing import Any

from .common import safe
from .storage import ability_modifier

# Short spellings accepted for an ability: the typed form -> the column it is.
ABBREV = {"str": "str", "s": "str", "dex": "dex", "d": "dex", "con": "con", "c": "con",
          "int": "intl", "i": "intl", "wis": "wis", "w": "wis", "cha": "cha", "ch": "cha"}

# What ``/set <field> <value>`` accepts: the field name -> the column it writes.
SET_FIELD_COLUMNS = {
    "ac": "ac", "armor": "ac", "armour": "ac",
    "speed": "speed", "init": "initiative", "initiative": "initiative",
    "gold": "gold", "level": "level", "temp": "temp_hp",
    **ABBREV,
}


def apply_hp_delta(hp: int, max_hp: int, temp_hp: int, delta: int) -> tuple[int, int, int]:
    """Apply damage or healing to a character's hit points.

    Temporary hit points soak damage before real HP goes, damage past 0 is
    wasted, and healing never exceeds max HP. Returns
    ``(new_hp, remaining_temp_hp, wasted_points)``.
    """
    new_hp = hp + delta
    temp = temp_hp
    wasted = 0
    if new_hp < 0:
        absorbed = min(temp, -new_hp)
        temp -= absorbed
        new_hp += absorbed
        if new_hp < 0:
            wasted = -new_hp  # damage beyond even 0 HP
            new_hp = 0
    elif new_hp > max_hp:
        wasted = new_hp - max_hp
        new_hp = max_hp
    return new_hp, temp, wasted


def level_change_error(old_level: int, new_level: int) -> str | None:
    """Why a level change is refused, as a message to show; ``None`` when it is allowed.

    Levels run 1-20, a character only goes up, and a no-op is worth saying out
    loud. Going down is left to ``/set level``, which is not rate-limited.
    """
    if not 1 <= new_level <= 20:
        return "Levels run from 1 to 20 in the SRD."
    if new_level == old_level:
        return f"Already level {new_level}."
    if new_level < old_level:
        return (
            f"Going <i>down</i> a level needs the DM - set it yourself with "
            f"<code>/set level {new_level}</code> if you must."
        )
    return None


def level_up_gain(old_level: int, new_level: int) -> int:
    """Rough average HP a level grants; the player sets the exact number."""
    return (new_level - old_level) * 5  # rough average, the player can set exact HP


def stat_block(character, owner: Any = None, history: list | None = None) -> str:
    """Render a character sheet as Telegram HTML."""
    hp = character["hp"]
    max_hp = character["max_hp"]
    bar = hp_bar(hp, max_hp)
    temp = f" (+{character['temp_hp']} temp)" if character["temp_hp"] else ""

    subtitle = " \u2022 ".join(
        part
        for part in (safe(character["race"]), safe(character["class_name"]), safe(character["background"]))
        if part
    )

    lines = [
        f"<b>\U0001f9d9 {safe(character['name'])}</b>",
        f"<i>{subtitle or ' adventurer'}</i>",
        "",
        f"<b>\u2764\ufe0f HP:</b> {bar} <code>{hp}/{max_hp}</code>{temp}",
        f"<b>\U0001f6e1 AC:</b> {character['ac']}   "
        f"<b>\u1f4a3 Initiative:</b> {character['initiative']:+d}   "
        f"<b>\U0001f6b6 Speed:</b> {character['speed']} ft.",
        f"<b>\u2b50 Level:</b> {character['level']}   "
        f"<b>\U0001f4c8 XP:</b> {character['xp']:,}   "
        f"<b>\U0001f4b0 Gold:</b> {character['gold']:,}",
        "",
        "<b>Ability scores</b>",
    ]
    scores = [
        (label, character[key], ability_modifier(character[key]))
        for key, label in (("str", "STR"), ("dex", "DEX"), ("con", "CON"),
                           ("intl", "INT"), ("wis", "WIS"), ("cha", "CHA"))
    ]
    for start in (0, 3):
        lines.append(
            "  ".join(
                f"{label} <code>{score}</code>({mod:+d})" for label, score, mod in scores[start : start + 3]
            )
        )
    lines.append("")
    if character["inspiration"]:
        lines.append("\u2728 <i>Has inspiration</i>")
    if character["subclass"]:
        lines.append(f"<b>Path:</b> {safe(character['subclass'])}")
    if owner is not None:
        lines.append(f"<i>Played by {owner}</i>")
    if character["notes"]:
        lines += ["", f"<b>Notes</b>\n<i>{safe(character['notes'])}</i>"]
    if history:
        recent = "\n".join(f"• {e['kind']} {e['detail']}".rstrip() for e in history)
        if recent:
            lines += ["", f"<b>Recent</b>\n{recent}"]
    return "\n".join(lines)


def hp_bar(hp: int, max_hp: int) -> str:
    """The ten-heart meter used on the sheet and after a damage or heal."""
    filled = 0 if max_hp <= 0 else round(10 * max(0, min(hp, max_hp)) / max_hp)
    return "\U0001f49a" * filled + "\U0001f49b" * (10 - filled)


def party_block(party: list, campaign_name: str) -> str:
    """Render the active party of a campaign as Telegram HTML."""
    lines = [f"<b>\U0001f465 The party of {safe(campaign_name)}</b>", ""]
    for member in party:
        down = member["hp"] <= 0
        hp_text = f"{member['hp']}/{member['max_hp']}"
        if member["temp_hp"]:
            hp_text += f"+{member['temp_hp']}"
        star = " \u2728" if member["inspiration"] else ""
        state = " \U0001f480" if down else ""
        lines.append(
            f"• <b>{safe(member['name'])}</b> <i>{safe(member['class_name'])}</i> "
            f"L{member['level']} \u2022 AC {member['ac']} \u2022 HP {hp_text}{star}{state}"
        )
    total = sum(p["hp"] for p in party)
    lines += ["", f"<i>Party total HP: {total}</i>"]
    return "\n".join(lines)


def hp_reply(character, delta: int, new_hp: int, temp: int, wasted: int) -> str:
    """What the player is told after damage or healing is applied."""
    bar = hp_bar(new_hp, character["max_hp"]) if character["max_hp"] > 0 else ""

    note = ""
    if delta > 0 and new_hp == character["max_hp"] and wasted > 0:
        note = f"\n<i>{wasted} point(s) of healing wasted - already at max HP.</i>"
    elif wasted > 0:
        note = f"\n<i>{wasted} point(s) of damage went past 0.</i>"
    if new_hp <= 0:
        note += "\n\n\U0001f480 <b>They are down.</b> Death saves: 1 success, 2 failures."

    return (
        f"<b>{safe(character['name'])}</b> {delta:+d} HP \u2192 "
        f"{bar} <code>{new_hp}/{character['max_hp']}</code>"
        + (f" (+{temp} temp)" if temp else "")
        + note
    )
