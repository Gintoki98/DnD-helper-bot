"""Encounters: the DM preps them, then invokes one in a session.

A template is built with ``/newencounter`` and ``/addmonster``; each monster
copies its SRD stats so the DM can tailor it with ``/ms``. ``/fight`` then
instantiates the template as a live run with its own hit points.

HP visibility is fixed when the encounter is created and cannot change while a
fight is running:

* ``visible`` - players see current and maximum HP.
* ``hidden``  - players see no HP numbers at all, only the damage dealt and
  who dealt it. The DM always sees everything.

Editing a template never disturbs a fight already in progress: the live run
works on its own copy of the stats.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from telethon import events

from .. import keyboards as kb
from ..common import (
    NoCampaign,
    duration,
    NotAMember,
    NotTheDM,
    ensure_dm,
    ensure_member,
    short_name,
    relative,
    resolve_campaign,
    safe,
)
from ..dice import DiceError, roll
from ..srd import SRDError, srd
from ..storage import db

BAR_WIDTH = 10
BOSS_CROWN = "\U0001f451"

log = logging.getLogger("dndbot")


def numbered(units: list) -> dict[int, Any]:
    """Map unit id -> the number players use to target it.

    The number is the position among *standing* units, which is exactly what
    ``/hit N`` resolves to, so what the list shows is always what the command
    hits. Dead units drop out and the rest close up.
    """
    standing = [u for u in units if u["status"] != "dead"]
    return {u["id"]: index for index, u in enumerate(standing, start=1)}


def tag(unit, number: int | None = None) -> str:
    """Name with its target number and the boss crown when it applies."""
    prefix = f"{number}. " if number else ""
    crown = f"{BOSS_CROWN} " if unit["is_boss"] else ""
    return f"{crown}<b>{prefix}{safe(unit['label'])}</b>"

EDITABLE_FIELDS = (
    "ac, hp, speed, count, atk, cr, str, dex, con, int, wis, cha, "
    "dmg, name, note, hide, show, nohp, hpback, boss, noboss, rm"
)


# --------------------------------------------------------------------------
# SRD stat block -> editable monster fields
# --------------------------------------------------------------------------
def _ac_of(data: dict) -> int:
    value = data.get("armor_class")
    if isinstance(value, list) and value and isinstance(value[0], dict):
        for key in ("value", "ac"):
            if isinstance(value[0].get(key), int):
                return int(value[0][key])
    if isinstance(value, int):
        return value
    return 10


def _stat(data: dict, key: str) -> int:
    value = data.get(key)
    if isinstance(value, dict):
        value = value.get("value")
    try:
        return max(3, min(30, int(value)))
    except (TypeError, ValueError):
        return 10


def _speed_of(data: dict) -> int:
    speed = data.get("speed")
    if isinstance(speed, dict):
        text = str(speed.get("walk") or speed.get("fly") or speed.get("swim") or "")
    else:
        text = str(speed or "")
    digits = re.findall(r"\d+", text)
    return int(digits[0]) if digits else 30


def _first_attack(data: dict) -> dict:
    for section in ("actions", "reactions", "special_abilities"):
        for item in data.get(section) or []:
            if item.get("attack_bonus") or item.get("damage"):
                return item
    return {}


def _joined(data: dict, key: str) -> str:
    value = data.get(key)
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value or "")


def monster_from_srd(data: dict, name: str) -> dict:
    """Flatten an SRD stat block into the fields the DM can then edit."""
    attack = _first_attack(data)
    damage = ""
    for hit in attack.get("damage") or []:
        if hit.get("damage_dice"):
            damage = str(hit["damage_dice"])
            break
    return {
        "name": name,
        "srd_index": data.get("index", ""),
        "count": 1,
        "ac": _ac_of(data),
        "max_hp": max(1, int(data.get("hit_points") or 1)),
        "speed": _speed_of(data),
        "cr": str(data.get("challenge_rating") or ""),
        "size": str(data.get("size") or ""),
        "mtype": str(data.get("type") or ""),
        "alignment": str(data.get("alignment") or ""),
        "str": _stat(data, "strength"),
        "dex": _stat(data, "dexterity"),
        "con": _stat(data, "constitution"),
        "intl": _stat(data, "intelligence"),
        "wis": _stat(data, "wisdom"),
        "cha": _stat(data, "charisma"),
        "attack_bonus": int(attack.get("attack_bonus") or 0),
        "damage": damage or "1d6",
        "resistances": _joined(data, "damage_resistances"),
        "immunities": _joined(data, "damage_immunities"),
        "condition_immunity": _joined(data, "condition_immunities"),
    }


# --------------------------------------------------------------------------
# views
# --------------------------------------------------------------------------
def bar(hp: int, max_hp: int) -> str:
    if max_hp <= 0:
        return ""
    filled = round(BAR_WIDTH * max(0, min(hp, max_hp)) / max_hp)
    return "\U0001f49a" * filled + "\U0001f49b" * (BAR_WIDTH - filled)


def monster_card(monster, slot: int) -> str:
    detail = [f"AC {monster['ac']}", f"HP <code>{monster['max_hp']}</code>"]
    if monster["count"] > 1:
        detail.append(f"x{monster['count']}")
    if monster["speed"]:
        detail.append(f"{monster['speed']} ft")
    lines = [f"<b>{slot}. {safe(monster['name'])}</b>", "  " + " \u2022 ".join(detail)]

    extras = []
    if monster["attack_bonus"]:
        extras.append(f"attack {monster['attack_bonus']:+d}")
    if monster["damage"]:
        extras.append(f"damage <code>{safe(monster['damage'])}</code>")
    if extras:
        lines.append("  " + " \u2022 ".join(extras))

    flags = []
    if monster["no_hp"]:
        flags.append("\u2800 no HP — falls only to /kill")
    if monster["cr"]:
        flags.append(f"CR {safe(monster['cr'])}")
    if monster["size"]:
        flags.append(safe(str(monster["size"])))
    if monster["mtype"]:
        flags.append(safe(str(monster["mtype"])))
    if monster["hidden"]:
        flags.append("\U0001f576 hidden from players")
    if flags:
        lines.append("  <i>" + " \u2022 ".join(flags) + "</i>")
    if monster["notes"]:
        lines.append(f"  <i>{safe(monster['notes'])}</i>")
    return "\n".join(lines)


def template_view(encounter, monsters: list) -> str:
    mode = "hidden" if encounter["hp_mode"] == "hidden" else "visible"
    total = sum(max(1, int(m["count"] or 1)) for m in monsters)
    lines = [
        f"<b>\U0001f91d {safe(encounter['name'])}</b> <i>#{encounter['id']}</i>",
        f"<i>Players see hit points: <b>{mode}</b> \u2022 {total} combatants</i>",
    ]
    if encounter["notes"]:
        lines += ["", safe(encounter["notes"])]
    if monsters:
        lines += [""] + [monster_card(m, i + 1) for i, m in enumerate(monsters)]
    else:
        lines += ["", "<i>No monsters yet \u2014 try <code>/addmonster goblin 3</code></i>"]
    return "\n".join(lines)


def rollout(encounter, units: list) -> str:
    """What the table sees when a fight begins.

    Hidden monsters are simply absent - the party is never told how many are
    lurking, because that would give the ambush away.
    """
    numbers = numbered(units)
    visible = [u for u in units if not u["hidden"]]
    hidden_count = len(units) - len(visible)

    if encounter["hp_mode"] == "hidden":
        if visible:
            body = "\n".join(
                f"\u2022 {tag(u, numbers.get(u['id']))} \u2022 AC {u['ac']}" for u in visible
            )
        else:
            body = "<i>Something is out there.</i>"
        note = "<i>I am not telling you their hit points \u2014 track the damage you deal.</i>"
        if hidden_count:
            note += "\n<i>And that is all you get to know.</i>"
        return (
            f"{note}\n\n{body}\n\n<i>Attack with <code>/hit 1 2d6+3</code> "
            "\u2014 the number is the one in front of the name.</i>"
        )

    if not visible:
        return "<i>Something is out there.</i>"
    lines = []
    for unit in visible:
        head = f"\u2022 {tag(unit, numbers.get(unit['id']))} \u2022 AC {unit['ac']}"
        if unit["no_hp"]:
            lines.append(head)
        else:
            lines.append(
                f"{head} \u2022 HP {bar(unit['hp'], unit['max_hp'])} "
                f"<code>{unit['hp']}/{unit['max_hp']}</code>"
            )
    return "\n".join(lines)


async def fight_view(encounter, run, as_dm: bool) -> str:
    units = await db.run_units(run["id"])
    if not units:
        return f"<b>{safe(encounter['name'])}</b>\n\n<i>No combatants.</i>"

    totals = await db.unit_damage_totals(run["id"])
    alive = sum(1 for u in units if u["status"] != "dead")
    numbers = numbered(units)

    lines = []
    for unit in units:
        if not as_dm and unit["hidden"]:
            continue
        # A no-HP monster never shows numbers to anybody; it only shows the
        # damage dealt. Players learn nothing about its condition.
        show_hp = not unit["no_hp"] and (as_dm or encounter["hp_mode"] != "hidden")
        bits = [f"AC {unit['ac']}"]
        if show_hp:
            bits.append(
                f"HP {bar(unit['hp'], unit['max_hp'])} "
                f"<code>{unit['hp']}/{unit['max_hp']}</code>"
            )
        if unit["no_hp"] and as_dm:
            bits.append("<i>no HP</i>")
        if unit["status"] == "dead":
            bits.append("\u274c down")
        number = numbers.get(unit["id"])
        line = tag(unit, number) + " \u2022 " + " \u2022 ".join(bits)
        if unit["status"] == "dead":
            line += "  <i>(no longer a valid target)</i>"
        lines.append(line)

        if not show_hp:
            dealt = totals.get(unit["id"], 0)
            if dealt:
                events_ = await db.unit_damage(unit["id"], limit=8)
                names = []
                for entry in reversed(events_):
                    names.append(f"{await _actor_name(entry['actor_id'])} {entry['amount']}")
                lines.append(
                    f"    <i>{dealt} damage \u2014 " + " \u2022 ".join(names) + "</i>"
                )
            else:
                lines.append("    <i>untouched</i>")

    header = [
        f"\U0001f91d <b>{safe(encounter['name'])}</b>",
        f"<i>started {relative(run['started_at'])} \u2022 {alive}/{len(units)} standing</i>",
    ]
    if as_dm and encounter["hp_mode"] == "hidden":
        header.append("<i>Players cannot see these numbers.</i>")
    if as_dm and any(u["no_hp"] and u["status"] != "dead" for u in units):
        header.append(
            "<i>Marked no-HP: they stay up until you <code>/kill</code> them.</i>"
        )
    return "\n".join(header) + "\n\n" + "\n".join(lines)


_actor_cache: dict[int, str] = {}


async def _actor_name(actor_id: int | None) -> str:
    if not actor_id:
        return "Someone"
    if actor_id not in _actor_cache:
        row = await db.get_user(actor_id)
        _actor_cache[actor_id] = short_name(row) if row is not None else "Someone"
    return _actor_cache[actor_id]


# --------------------------------------------------------------------------
# parsing helpers
# --------------------------------------------------------------------------
NUMERIC_FIELDS = {
    "ac": "ac", "armor": "ac", "armour": "ac",
    "hp": "max_hp", "maxhp": "max_hp", "max_hp": "max_hp", "health": "max_hp",
    "speed": "speed", "count": "count", "qty": "count", "number": "count",
    "atk": "attack_bonus", "attack": "attack_bonus", "bonus": "attack_bonus",
    "cr": "cr",
    "str": "str", "dex": "dex", "con": "con", "int": "intl",
    "wis": "wis", "cha": "cha",
}

LIMITS = {
    "ac": (0, 40), "max_hp": (1, 999), "speed": (0, 200), "count": (1, 20),
    "attack_bonus": (-20, 20), "cr": (0, 30),
}


def pick_monster(monsters: list, selector: str):
    selector = (selector or "").strip()
    if selector.isdigit():
        position = int(selector)
        return monsters[position - 1] if 1 <= position <= len(monsters) else None
    needle = selector.lower()
    for monster in monsters:
        if monster["name"].lower() == needle:
            return monster
    for monster in monsters:
        if needle in monster["name"].lower():
            return monster
    return None


async def _set_visibility(campaign, encounter, monster, hidden: bool) -> int:
    """Mirror a template hide/show onto the units of a running fight."""
    run = await db.active_run(campaign["id"])
    if not run:
        return 0
    await db.set_monster_visibility(encounter["id"], monster["id"], hidden)
    return await db.set_unit_visibility(run["id"], monster["id"], hidden)


async def _set_no_hp(campaign, encounter, monster, value: int) -> int:
    """Mirror the no-HP flag onto the units of a running fight."""
    return await _mirror(campaign, encounter, monster, "no_hp", value)


async def _mirror(campaign, encounter, monster, column: str, value: int) -> int:
    """Copy a template flag onto the units of a running fight.

    Lets a DM mark something mid-combat with the same ``/ms`` they used while
    preparing it. Returns how many live units changed.
    """
    run = await db.active_run(campaign["id"])
    if not run:
        return 0
    if column == "is_boss":
        return await db.set_unit_boss(run["id"], monster["id"], value)
    if column == "no_hp":
        return await db.set_unit_no_hp(run["id"], monster["id"], value)
    raise ValueError(f"cannot mirror unknown column {column!r}")


def register(client) -> None:
    # ------------------------------------------------------------------
    # template editing (DM only)
    # ------------------------------------------------------------------
    async def current_template(event, term: str = ""):
        """Return (campaign, encounter, is_dm). ``term`` may name the template.

        With no term the most recently touched template is used, which is how
        a DM actually works: create one, then keep adding to it.
        """
        campaign = await resolve_campaign(event)
        membership = await ensure_member(campaign, event.sender_id)
        is_dm = membership["role"] == "dm"
        encounter = None
        if term:
            encounter = await db.find_encounter(campaign["id"], term)
        else:
            rows = await db.list_encounters(campaign["id"])
            encounter = rows[0] if rows else None
        return campaign, encounter, is_dm

    @client.on(events.NewMessage(pattern=r"^/newencounter(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def new_encounter(event: events.NewMessage.Event) -> None:
        parts = event.raw_text.split(None, 1)
        raw = parts[1].strip() if len(parts) > 1 else ""
        try:
            campaign = await resolve_campaign(event)
            await ensure_dm(campaign, event.sender_id)
        except (NoCampaign, NotAMember, NotTheDM) as exc:
            await event.reply(str(exc), parse_mode="html")
            return

        name, _, notes = raw.partition("|")
        name, notes = name.strip(), notes.strip()
        mode = "visible"
        # "hidden" may appear in either half; strip it out of both.
        if re.search(r"\bhidden\b", f"{name} {notes}", re.I):
            mode = "hidden"
            name = re.sub(r"\bhidden\b", "", name, flags=re.I).strip(" |-")
            notes = re.sub(r"\bhidden\b", "", notes, flags=re.I).strip(" |-")
        if not name:
            await event.reply(
                "Name the encounter:\n"
                "<code>/newencounter Ambush at the ford | 3 goblins on the bridge</code>\n\n"
                "Players see hit points by default. Add <b>hidden</b> and they will not:\n"
                "<code>/newencounter Ambush at the ford | hidden</code>",
                parse_mode="html",
            )
            return

        encounter = await db.create_encounter(
            campaign["id"], event.sender_id, name[:80], notes[:500], mode
        )
        await event.reply(
            f"\U0001f91d Created <b>{safe(encounter['name'])}</b>.\n"
            f"<i>Players will {'' if mode == 'visible' else 'NOT '}see hit points.</i>\n\n"
            "Add monsters with <code>/addmonster goblin 3</code>, tailor them with "
            "<code>/ms 1 ac 16</code>, then run it with <code>/fight</code>.",
            parse_mode="html",
        )

    @client.on(events.NewMessage(pattern=r"^/encounters(?:@[\w_]+)?$"))
    async def list_encounters(event: events.NewMessage.Event) -> None:
        try:
            campaign = await resolve_campaign(event)
            await ensure_dm(campaign, event.sender_id)
        except (NoCampaign, NotAMember, NotTheDM) as exc:
            await event.reply(str(exc), parse_mode="html")
            return
        rows = await db.list_encounters(campaign["id"])
        if not rows:
            await event.reply(
                "No encounters yet \u2014 build one with <code>/newencounter Name</code>.",
                parse_mode="html",
            )
            return
        lines = [f"<b>Encounters in {safe(campaign['name'])}</b>", ""]
        for row in rows:
            monsters = await db.encounter_monsters(row["id"])
            total = sum(max(1, int(m["count"] or 1)) for m in monsters)
            mode = (
                "\U0001f576 hidden HP"
                if row["hp_mode"] == "hidden"
                else "\U0001f441\ufe0f visible HP"
            )
            lines.append(
                f"• <b>{safe(row['name'])}</b> <i>{total} combatants \u2022 {mode}</i>"
            )
        lines += ["", "<i>Open one with /enc Name \u2022 remove with /delenc Name</i>"]
        await event.reply("\n".join(lines), parse_mode="html")

    @client.on(events.NewMessage(pattern=r"^/enc(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def open_encounter(event: events.NewMessage.Event) -> None:
        parts = event.raw_text.split(None, 1)
        term = parts[1].strip() if len(parts) > 1 else ""
        try:
            campaign = await resolve_campaign(event)
            await ensure_dm(campaign, event.sender_id)
        except (NoCampaign, NotAMember, NotTheDM) as exc:
            await event.reply(str(exc), parse_mode="html")
            return
        encounter = await db.find_encounter(campaign["id"], term) if term else None
        if encounter is None:
            rows = await db.list_encounters(campaign["id"])
            encounter = rows[0] if rows else None
        if encounter is None:
            await event.reply(
                "No encounters yet \u2014 build one with <code>/newencounter Name</code>.",
                parse_mode="html",
            )
            return
        monsters = await db.encounter_monsters(encounter["id"])
        await event.reply(template_view(encounter, monsters), parse_mode="html")

    @client.on(events.NewMessage(pattern=r"^/addmonster(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def add_monster(event: events.NewMessage.Event) -> None:
        # split(None, 1) so a two-word argument like "goblin 3" stays together.
        parts = event.raw_text.split(None, 1)
        term = parts[1].strip() if len(parts) > 1 else ""
        count = 1
        match = re.match(r"^(.*?)\s*[xX]\s*(\d{1,2})$", term) or re.match(
            r"^(.*?)\s+(\d{1,2})$", term
        )
        if match:
            term, count = match.group(1).strip(), max(1, min(20, int(match.group(2))))

        try:
            campaign, encounter, is_dm = await current_template(event)
            if not is_dm:
                raise NotTheDM("Only the DM can prepare encounters.")
        except (NoCampaign, NotAMember, NotTheDM) as exc:
            await event.reply(str(exc), parse_mode="html")
            return
        if encounter is None:
            await event.reply(
                "You have no encounter to add to. Create one first:\n"
                "<code>/newencounter Ambush at the ford</code>",
                parse_mode="html",
            )
            return
        if not term:
            # Never let an empty term fall through to a fuzzy match, which
            # would silently pick the first monster in the SRD.
            await event.reply(
                "Which monster? <code>/addmonster goblin 3</code>",
                parse_mode="html",
            )
            return

        try:
            entry, data = await srd.get("monsters", term)
        except SRDError as exc:
            suggestions = []
            try:
                for hit in await srd.search("monsters", term, limit=4):
                    suggestions.append(hit.name)
            except SRDError:
                pass
            extra = (
                "\n\nDid you mean: " + ", ".join(f"<code>{s}</code>" for s in suggestions)
                if suggestions
                else ""
            )
            await event.reply(f"\U0001f50d {exc}{extra}", parse_mode="html")
            return

        fields = monster_from_srd(data, entry.name)
        fields["count"] = count
        monster = await db.add_encounter_monster(encounter["id"], **fields)
        await db.update_encounter(encounter["id"])

        monsters = await db.encounter_monsters(encounter["id"])
        note = ""
        if await db.active_run(campaign["id"]):
            note = "\n<i>A fight is running; it keeps its own copy of these stats.</i>"
        await event.reply(
            f"Added <b>{safe(monster['name'])}</b> x{count} to "
            f"<b>{safe(encounter['name'])}</b>.{note}\n\n"
            + monster_card(monster, monsters.index(monster) + 1)
            + "\n\n<i>Tailor it: /ms 1 ac 18 \u2022 /ms 1 hp 30 \u2022 /ms 1 dmg 2d6+4</i>",
            parse_mode="html",
        )

    @client.on(events.NewMessage(pattern=r"^/ms(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def edit_monster(event: events.NewMessage.Event) -> None:
        parts = event.raw_text.split(None, 1)
        payload = parts[1].strip() if len(parts) > 1 else ""
        try:
            campaign, encounter, is_dm = await current_template(event)
            if not is_dm:
                raise NotTheDM("Only the DM can tailor monsters.")
        except (NoCampaign, NotAMember, NotTheDM) as exc:
            await event.reply(str(exc), parse_mode="html")
            return
        if encounter is None:
            await event.reply("You have no encounter yet \u2014 /newencounter Name", parse_mode="html")
            return

        monsters = await db.encounter_monsters(encounter["id"])
        if not monsters:
            await event.reply("This encounter has no monsters yet.", parse_mode="html")
            return
        if not payload:
            await event.reply(template_view(encounter, monsters), parse_mode="html")
            return

        bits = payload.split(None, 2)
        target = pick_monster(monsters, bits[0])
        if target is None:
            await event.reply(
                f"Nothing matching <b>{safe(bits[0])}</b>. Number them 1-{len(monsters)}.",
                parse_mode="html",
            )
            return
        slot = monsters.index(target) + 1

        if len(bits) == 1:
            await event.reply(
                f"<b>{safe(target['name'])}</b>\n\n" + monster_card(target, slot)
                + f"\n\n<code>/ms {slot} ac 16</code> \u2022 <code>/ms {slot} hp 40</code> \u2022 "
                f"<code>/ms {slot} dmg 2d6+4</code> \u2022 <code>/ms {slot} atk +5</code>\n"
                f"<code>/ms {slot} count 3</code> \u2022 <code>/ms {slot} hide</code> \u2022 "
                f"<code>/ms {slot} nohp</code> \u2022 <code>/ms {slot} rm</code>\n\n"
                f"<i>Fields: {EDITABLE_FIELDS}</i>",
                parse_mode="html",
            )
            return

        action = bits[1].lower()
        value = bits[2].strip() if len(bits) > 2 else ""

        if action in {"hide", "hidden"}:
            updated = await db.update_encounter_monster(target["id"], hidden=1)
            note = "\U0001f576 Players will not see this one."
            changed = await _set_visibility(campaign, encounter, target, True)
            if changed:
                note += f" <i>({changed} in the running fight)</i>"
        elif action in {"show", "visible", "unhide", "reveal"}:
            updated = await db.update_encounter_monster(target["id"], hidden=0)
            note = "\U0001f441\ufe0f Players can see this one."
            # A reveal mid-fight is worth announcing, but the party only ever
            # learns that this one is now visible - never what else lurks.
            changed = await _set_visibility(campaign, encounter, target, False)
            if changed:
                await announce(
                    event,
                    campaign["id"],
                    f"\U0001f441\ufe0f <b>{safe(target['name'])}</b> reveals itself!",
                    event.sender_id,
                )
                note += f" <i>({changed} in the running fight \u2014 announced)</i>"
        elif action in {"nohp", "no-hp", "nohitpoints"}:
            updated = await db.update_encounter_monster(target["id"], no_hp=1)
            note = (
                "<b>No HP.</b> Damage accumulates but it will not fall \u2014 "
                "drop it with <code>/kill Name</code>."
            )
            changed = await _set_no_hp(campaign, encounter, target, 1)
            if changed:
                note += f" <i>({changed} in the running fight)</i>"
        elif action in {"hpback", "normal", "withhp"}:
            updated = await db.update_encounter_monster(target["id"], no_hp=0)
            note = "Back to normal hit points."
            changed = await _set_no_hp(campaign, encounter, target, 0)
            if changed:
                note += f" <i>({changed} in the running fight)</i>"
        elif action in {"boss", "isboss"}:
            updated = await db.update_encounter_monster(target["id"], is_boss=1)
            note = f"{BOSS_CROWN} Marked a boss \u2014 the table sees the crown."
            changed = await _mirror(campaign, encounter, target, "is_boss", 1)
            if changed:
                note += f" <i>({changed} in the running fight)</i>"
        elif action in {"noboss", "notboss", "unboss"}:
            updated = await db.update_encounter_monster(target["id"], is_boss=0)
            note = "No longer a boss."
            changed = await _mirror(campaign, encounter, target, "is_boss", 0)
            if changed:
                note += f" <i>({changed} in the running fight)</i>"
        elif action in {"rm", "remove", "delete"}:
            await db.delete_encounter_monster(target["id"])
            await db.update_encounter(encounter["id"])
            await event.reply(
                f"Removed <b>{safe(target['name'])}</b> from {safe(encounter['name'])}.",
                parse_mode="html",
            )
            return
        elif action in {"name", "n"}:
            updated = await db.update_encounter_monster(target["id"], name=value[:60])
            note = f"Renamed to <b>{safe(value)}</b>."
        elif action in {"note", "notes"}:
            updated = await db.update_encounter_monster(
                target["id"], notes=value[:300]
            )
            note = "Note saved."
        elif action in {"damage", "dmg"}:
            if not value:
                await event.reply(
                    "Give dice, e.g. <code>/ms 1 dmg 2d6+4</code>", parse_mode="html"
                )
                return
            try:
                roll(value)
            except DiceError as exc:
                await event.reply(f"\u274c {exc}", parse_mode="html")
                return
            updated = await db.update_encounter_monster(target["id"], damage=value)
            note = f"Damage set to <code>{safe(value)}</code>."
        else:
            column = NUMERIC_FIELDS.get(action)
            if column is None:
                await event.reply(
                    f"I do not track <b>{safe(action)}</b>.\n"
                    f"Fields: {EDITABLE_FIELDS}",
                    parse_mode="html",
                )
                return
            digits = re.findall(r"-?\d+", value)
            if not digits:
                await event.reply(
                    f"<b>{safe(action)}</b> needs a number, e.g. "
                    f"<code>/ms {slot} {safe(action)} 16</code>",
                    parse_mode="html",
                )
                return
            number = int(digits[0])
            low, high = LIMITS.get(column, (3, 30))
            clamped = max(low, min(high, number))
            updated = await db.update_encounter_monster(target["id"], **{column: clamped})
            note = (
                f"{safe(action)} set to <b>{clamped}</b>."
                if clamped == number
                else f"{safe(action)} set to <b>{clamped}</b> (clamped from {number})."
            )

        await db.update_encounter(encounter["id"])
        await event.reply(
            f"{note}\n\n" + monster_card(updated, slot), parse_mode="html"
        )

    @client.on(events.NewMessage(pattern=r"^/hpmode(?:@[\w_]+)?(?:\s+(\w+))?$"))
    async def hp_mode(event: events.NewMessage.Event) -> None:
        parts = event.raw_text.split(None, 1)
        wanted = parts[1].strip().lower() if len(parts) > 1 else ""
        try:
            campaign, encounter, is_dm = await current_template(event)
            if not is_dm:
                raise NotTheDM("Only the DM decides what players can see.")
        except (NoCampaign, NotAMember, NotTheDM) as exc:
            await event.reply(str(exc), parse_mode="html")
            return
        if encounter is None:
            await event.reply("You have no encounter yet \u2014 /newencounter Name", parse_mode="html")
            return
        if wanted in {"", "?"}:
            mode = "hidden" if encounter["hp_mode"] == "hidden" else "visible"
            await event.reply(
                f"Players currently see hit points in <b>{safe(encounter['name'])}</b>: "
                f"<b>{mode}</b>.\n\n<code>/hpmode hidden</code> or <code>/hpmode visible</code>",
                parse_mode="html",
            )
            return
        if wanted not in {"hidden", "visible"}:
            await event.reply(
                "Choose <code>hidden</code> (players see damage only) or "
                "<code>visible</code>.",
                parse_mode="html",
            )
            return
        if await db.active_run(campaign["id"]):
            await event.reply(
                "A fight is running \u2014 end it with <code>/endfight</code> first. "
                "Visibility is fixed once combat starts.",
                parse_mode="html",
            )
            return
        await db.update_encounter(encounter["id"], hp_mode=wanted)
        await event.reply(
            f"Players will now {'not ' if wanted == 'hidden' else ''}see hit points in "
            f"<b>{safe(encounter['name'])}</b>."
            + ("\n\n<i>In hidden mode they see the damage dealt and by whom.</i>"
               if wanted == "hidden" else ""),
            parse_mode="html",
        )

    @client.on(events.NewMessage(pattern=r"^/delenc(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def delete_encounter(event: events.NewMessage.Event) -> None:
        parts = event.raw_text.split(None, 1)
        term = parts[1].strip() if len(parts) > 1 else ""
        try:
            campaign = await resolve_campaign(event)
            await ensure_dm(campaign, event.sender_id)
        except (NoCampaign, NotAMember, NotTheDM) as exc:
            await event.reply(str(exc), parse_mode="html")
            return
        encounter = await db.find_encounter(campaign["id"], term) if term else None
        if encounter is None:
            rows = await db.list_encounters(campaign["id"])
            encounter = rows[0] if rows else None
        if encounter is None:
            await event.reply("No encounters to delete.", parse_mode="html")
            return
        await db.delete_encounter(encounter["id"])
        await event.reply(f"Deleted <b>{safe(encounter['name'])}</b>.", parse_mode="html")

    # ------------------------------------------------------------------
    # live fights
    # ------------------------------------------------------------------
    async def announce(event, campaign_id: int, text: str, exclude: int) -> None:
        """Message every member of the campaign.

        Uses ``event.client`` rather than a captured client so the sender is
        always the connection this update arrived on.
        """
        from .campaign import broadcast

        try:
            await broadcast(event.client, campaign_id, text, exclude=exclude)
        except Exception:
            log.exception("could not announce to the party")

    async def death_notice(event, campaign_id: int, unit) -> None:
        """Tell the whole party that a combatant fell.

        Nobody is excluded: the point of the alert is that the rest of the
        table learns about it, whoever landed the blow.
        """
        await announce(
            event,
            campaign_id,
            f"\u274c <b>{safe(unit['label'])}</b> goes down!",
            exclude=None,
        )

    @client.on(events.NewMessage(pattern=r"^/fight(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def fight(event: events.NewMessage.Event) -> None:
        parts = event.raw_text.split(None, 1)
        term = parts[1].strip() if len(parts) > 1 else ""

        try:
            campaign = await resolve_campaign(event)
            membership = await ensure_member(campaign, event.sender_id)
        except (NoCampaign, NotAMember) as exc:
            await event.reply(str(exc), parse_mode="html")
            return
        is_dm = membership["role"] == "dm"

        run = await db.active_run(campaign["id"])
        encounter = await db.get_encounter(run["encounter_id"]) if run else None

        if is_dm and term:
            target = await db.find_encounter(campaign["id"], term)
            if target is None:
                await event.reply(f"No encounter called <b>{safe(term)}</b>.", parse_mode="html")
                return
            monsters = await db.encounter_monsters(target["id"])
            if not monsters:
                await event.reply(
                    f"<b>{safe(target['name'])}</b> has no monsters yet "
                    "(<code>/addmonster goblin 3</code>).",
                    parse_mode="html",
                )
                return
            if run:
                await db.end_run(run["id"])
            session = await db.active_session(campaign["id"])
            run = await db.start_encounter_run(
                target["id"], campaign["id"], event.sender_id,
                session["id"] if session else None,
            )
            encounter = target
            units = await db.run_units(run["id"])
            await event.reply(
                f"\U0001f91d <b>{safe(encounter['name'])}</b> begins!\n\n"
                + rollout(encounter, units),
                parse_mode="html",
            )
            await announce(
                event,
                campaign["id"],
                f"\U0001f91d Combat in <b>{safe(campaign['name'])}</b>.\n\n"
                + rollout(encounter, units)
                + "\n\n<code>/fight</code> to look \u2022 <code>/hit 1 2d6+3</code> to attack",
                exclude=event.sender_id,
            )
            return

        if not run:
            await event.reply(
                "No fight is running.\n\n"
                "<b>DM:</b> <code>/fight Ambush at the ford</code>\n"
                "<i>Prepare it first with /newencounter and /addmonster.</i>",
                buttons=kb.main_menu(),
                parse_mode="html",
            )
            return

        await event.reply(await fight_view(encounter, run, is_dm), parse_mode="html")

    @client.on(events.NewMessage(pattern=r"^/endfight(?:@[\w_]+)?$"))
    async def end_fight(event: events.NewMessage.Event) -> None:
        try:
            campaign = await resolve_campaign(event)
            await ensure_dm(campaign, event.sender_id)
        except (NoCampaign, NotAMember, NotTheDM) as exc:
            await event.reply(str(exc), parse_mode="html")
            return
        run = await db.active_run(campaign["id"])
        if not run:
            await event.reply("No fight is running.", parse_mode="html")
            return
        encounter = await db.get_encounter(run["encounter_id"])
        units = await db.run_units(run["id"])
        await db.end_run(run["id"])
        standing = [u for u in units if u["status"] != "dead"]
        totals = await db.unit_damage_totals(run["id"])
        dealt = sum(totals.values())
        length = duration(time.time() - run["started_at"])

        summary = (
            f"\U0001f6d1 <b>The fight is over</b> in <b>{safe(campaign['name'])}</b>.\n"
            f"<i>{safe(encounter['name'])} \u2022 {length} \u2022 "
            f"{len(standing)}/{len(units)} still standing \u2022 "
            f"{dealt} total damage dealt.</i>"
        )
        await announce(event, campaign["id"], summary, exclude=event.sender_id)
        await event.reply(
            f"\u2694\ufe0f <b>{safe(encounter['name'])}</b> is over \u2014 I have told "
            f"everyone who was in the fight.\n"
            f"<i>{len(standing)}/{len(units)} still standing \u2022 "
            f"{dealt} total damage dealt.</i>",
            parse_mode="html",
        )

    @client.on(events.NewMessage(pattern=r"^/hit(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def hit(event: events.NewMessage.Event) -> None:
        parts = event.raw_text.split(None, 1)
        payload = parts[1].strip() if len(parts) > 1 else ""
        bits = payload.split(None, 1)
        selector = bits[0] if bits else ""
        amount_text = bits[1].strip() if len(bits) > 1 else ""
        if not bits:
            await event.reply(
                "Deal damage:\n"
                "<code>/hit 1 7</code> \u2014 a fixed amount\n"
                "<code>/hit ogre 2d6+4</code> \u2014 I roll it and remember who did it\n"
                "<code>/heal 1 10</code> \u2014 patch one up",
                parse_mode="html",
            )
            return
        if not amount_text:
            await event.reply(
                f"How much damage to <b>{safe(selector)}</b>? <code>/hit {selector} 7</code>",
                parse_mode="html",
            )
            return

        try:
            campaign = await resolve_campaign(event)
            membership = await ensure_member(campaign, event.sender_id)
        except (NoCampaign, NotAMember) as exc:
            await event.reply(str(exc), parse_mode="html")
            return
        is_dm = membership["role"] == "dm"

        run = await db.active_run(campaign["id"])
        if not run:
            await event.reply(
                "No fight is running \u2014 the DM starts one with <code>/fight</code>.",
                parse_mode="html",
            )
            return
        unit = await db.find_unit(run["id"], selector)
        if unit is None or (unit["hidden"] and not is_dm):
            # A hidden monster must not be discoverable by guessing its
            # number, so players get the same answer as for a target that is
            # not there at all.
            await event.reply(
                f"No combatant matching <b>{safe(selector)}</b> is standing.",
                parse_mode="html",
            )
            return
        if unit["status"] == "dead":
            await event.reply(f"<b>{safe(unit['label'])}</b> is already down.", parse_mode="html")
            return

        crit = False
        try:
            parsed = roll(amount_text)
            amount = parsed.total
            crit = parsed.natural == 20
        except DiceError:
            digits = re.findall(r"\d+", amount_text)
            if not digits:
                await event.reply(
                    f"\u274c I could not read <b>{safe(amount_text)}</b> as damage.",
                    parse_mode="html",
                )
                return
            amount = int(digits[0])
        if amount <= 0:
            await event.reply("That is not damage.", parse_mode="html")
            return

        updated = await db.damage_unit(unit["id"], amount, event.sender_id, amount_text)
        encounter = await db.get_encounter(run["encounter_id"])
        who = short_name(event.sender)
        crit_tag = " \u2022 \u2705 <b>critical!</b>" if crit else ""

        if updated["no_hp"]:
            # It cannot be dropped by damage; only the DM decides when it falls.
            total = (await db.unit_damage_totals(run["id"])).get(unit["id"], 0)
            tail = (
                "\n\n<i>It does not seem to be slowing down.</i>"
                if not is_dm
                else f"\n\n<i>Marked no-HP \u2014 drop it with <code>/kill "
                f"{safe(unit['label'])}</code>.</i>"
            )
            await event.reply(
                f"\U0001f3af <b>{safe(unit['label'])}</b> takes {amount} from "
                f"{safe(who)}.{crit_tag}\n"
                f"<i>{total} damage dealt so far.</i>{tail}",
                parse_mode="html",
            )
            return

        if updated["hp"] <= 0 and unit["status"] != "dead":
            # Reached zero for the first time: let the whole table know.
            await death_notice(event, campaign["id"], updated)

        if encounter["hp_mode"] == "hidden" and not is_dm:
            total = (await db.unit_damage_totals(run["id"])).get(unit["id"], 0)
            await event.reply(
                f"\U0001f3af <b>{safe(unit['label'])}</b> takes {amount} from "
                f"{safe(who)}.{crit_tag}\n<i>{total} damage dealt to it so far.</i>",
                parse_mode="html",
            )
            return

        down = "\n\n\U0001f480 <b>It drops.</b>" if updated["hp"] <= 0 else ""
        await event.reply(
            f"\U0001f3af <b>{safe(unit['label'])}</b> takes {amount} from "
            f"{safe(who)}.{crit_tag}\n"
            f"{bar(updated['hp'], updated['max_hp'])} "
            f"<code>{updated['hp']}/{updated['max_hp']}</code>{down}",
            parse_mode="html",
        )

    @client.on(events.NewMessage(pattern=r"^/kill(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def kill(event: events.NewMessage.Event) -> None:
        """Drop a combatant outright - the only way a no-HP monster goes down."""
        parts = event.raw_text.split(None, 1)
        selector = parts[1].strip() if len(parts) > 1 else ""
        try:
            campaign = await resolve_campaign(event)
            await ensure_dm(campaign, event.sender_id)
        except (NoCampaign, NotAMember, NotTheDM) as exc:
            await event.reply(str(exc), parse_mode="html")
            return
        if not selector:
            await event.reply(
                "Which one? <code>/kill wraith</code> or <code>/kill 3</code>\n"
                "<i>A monster marked no-HP only falls when you do this.</i>",
                parse_mode="html",
            )
            return

        run = await db.active_run(campaign["id"])
        if not run:
            await event.reply("No fight is running.", parse_mode="html")
            return
        unit = await db.find_unit(run["id"], selector)
        if unit is None:
            await event.reply(
                f"No combatant matching <b>{safe(selector)}</b> is standing.",
                parse_mode="html",
            )
            return
        if unit["status"] == "dead":
            await event.reply(f"<b>{safe(unit['label'])}</b> is already down.", parse_mode="html")
            return

        updated = await db.kill_unit(unit["id"], event.sender_id)
        await death_notice(event, campaign["id"], updated)
        total = (await db.unit_damage_totals(run["id"])).get(unit["id"], 0)
        note = f"\n<i>It had taken {total} damage.</i>" if total else ""
        await event.reply(
            f"\u2694\ufe0f <b>{safe(unit['label'])}</b> is killed.{note}",
            parse_mode="html",
        )

    @client.on(events.NewMessage(pattern=r"^/heal(?:@[\w_]+)?\s+(\S+)\s+(\d+)$"))
    async def heal(event: events.NewMessage.Event) -> None:
        match = re.match(r"^/heal\s+(\S+)\s+(\d+)$", event.raw_text.strip())
        selector, amount = match.group(1), int(match.group(2))
        try:
            campaign = await resolve_campaign(event)
            membership = await ensure_member(campaign, event.sender_id)
        except (NoCampaign, NotAMember) as exc:
            await event.reply(str(exc), parse_mode="html")
            return
        is_dm = membership["role"] == "dm"
        run = await db.active_run(campaign["id"])
        if not run:
            await event.reply("No fight is running.", parse_mode="html")
            return
        unit = await db.find_unit(run["id"], selector)
        if unit is None or (unit["hidden"] and not is_dm):
            await event.reply(f"No combatant matching <b>{safe(selector)}</b>.", parse_mode="html")
            return
        updated = await db.heal_unit(unit["id"], amount, event.sender_id)
        encounter = await db.get_encounter(run["encounter_id"])
        if encounter["hp_mode"] == "hidden" and not is_dm:
            await event.reply(
                f"\u2764\ufe0f <b>{safe(unit['label'])}</b> is patched up by "
                f"{safe(short_name(event.sender))}.",
                parse_mode="html",
            )
            return
        await event.reply(
            f"\u2764\ufe0f <b>{safe(unit['label'])}</b> heals {amount} \u2192 "
            f"<code>{updated['hp']}/{updated['max_hp']}</code>",
            parse_mode="html",
        )