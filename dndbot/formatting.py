"""Turn SRD JSON into readable Telegram HTML.

Telegram caps a message at 4096 characters, so every formatter returns a
*list of pages* that the sender walks through, either by sending them in
sequence or by putting them behind a "next page" button.
"""

from __future__ import annotations

import html
import re
from typing import Any, Iterable

PAGE_SIZE = 3900
SRD_CREDIT = "<i>SRD 5.2.1 (2014) \u00b7 dnd5eapi.co</i>"

# Inline markers the API uses inside desc strings.
_MARKUP = (
    (re.compile(r"\*\*([^*]+)\*\*"), r"<b>\1</b>"),
    (re.compile(r"(?<!\w)\*([^*\n]+)\*(?!\w)"), r"<i>\1</i>"),
    (re.compile(r"_{2}([^_]+)_{2}"), r"<u>\1</u>"),
    (re.compile(r"`([^`]+)`"), r"<code>\1</code>"),
    (re.compile(r"\\\\\*"), "*"),
    (re.compile(r"\\{2,}"), "\n"),
)


def esc(text: Any) -> str:
    """Escape for Telegram HTML, then apply the API's own light markup."""
    out = html.escape(str(text if text is not None else ""), quote=False)
    for pattern, replacement in _MARKUP:
        out = pattern.sub(replacement, out)
    return out


def clean(text: str) -> str:
    """Normalise whitespace for a single line."""
    return re.sub(r"\s+", " ", str(text or "")).strip()


def desc_text(value: Any) -> str:
    """Flatten the API's polymorphic ``desc`` field into plain text.

    Handles a bare string, a list of strings, a list of ``{type, text}``
    objects and ``{"text": ...}`` references.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        if "desc" in value:
            return desc_text(value["desc"])
        if "text" in value:
            return str(value["text"]).strip()
        return ""
    if isinstance(value, Iterable):
        parts = [desc_text(item) for item in value]
        return "\n\n".join(part for part in parts if part)
    return str(value).strip()


def names_of(value: Any) -> str:
    """Render a list of ``{name: ...}`` / ``{name: {name: ...}}`` references."""
    if not value:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        value = [value]
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            name = item.get("name")
            if isinstance(name, dict):
                name = name.get("name")
            if name:
                out.append(str(name))
            elif "desc" in item:
                out.append(clean(desc_text(item)))
    return ", ".join(out)


def page(lines: Iterable[str]) -> str:
    return "\n".join(line for line in lines if line)


def split_pages(text: str, size: int = PAGE_SIZE) -> list[str]:
    """Break a long page on paragraph boundaries, hard-splitting if needed."""
    if len(text) <= size:
        return [text]
    pages: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= size:
            current = candidate
            continue
        if current:
            pages.append(current)
        while len(block) > size:
            cut = block.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            pages.append(block[:cut].rstrip())
            block = block[cut:].lstrip()
        current = block
    if current:
        pages.append(current)
    return pages


# -- monsters -------------------------------------------------------------
def _armor_class(value: Any) -> str:
    if not value:
        return "\u2014"
    if isinstance(value, (int, float)):
        return str(value)
    parts: list[str] = []
    for entry in value if isinstance(value, list) else [value]:
        if not isinstance(entry, dict):
            parts.append(str(entry))
            continue
        ac = entry.get("value") or entry.get("ac")
        kinds = [str(k) for k in entry.get("type", []) if k != "natural"]
        source = names_of(entry.get("from")) or names_of(entry.get("armor"))
        text = str(ac) if ac is not None else "\u2014"
        if source:
            text += f" ({source})"
        elif kinds:
            text += f" ({', '.join(kinds)})"
        parts.append(text)
    return ", ".join(parts)


def _speed(value: Any) -> str:
    if not value:
        return "\u2014"
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return ", ".join(f"{key} {val}" for key, val in value.items())
    return str(value)


def _stat(score: Any) -> str:
    if isinstance(score, dict):
        score = score.get("value")
    if score is None:
        return "\u2014"
    return f"{score} ({int(score) // 2 - 5:+d})"


def format_monster(entry, data: dict) -> list[str]:
    header = [
        f"<b>\U0001f409 {esc(data.get('name', entry.name))}</b>",
        "",
        "<b>\u2022 Size:</b> {}{} \u2022 <b>Type:</b> {} \u2022 <b>Alignment:</b> {}".format(
            esc(data.get("size", "\u2014")),
            f" / {esc(data['subtype'])}" if data.get("subtype") else "",
            esc(data.get("type", "\u2014")),
            esc(data.get("alignment", "\u2014")),
        ),
        "<b>\u2022 Armor Class:</b> {}".format(_armor_class(data.get("armor_class"))),
        "<b>\u2022 Hit Points:</b> {} ({})".format(
            esc(data.get("hit_points", "\u2014")), esc(data.get("hit_dice", ""))
        ),
        "<b>\u2022 Challenge:</b> {} (XP {})".format(
            esc(data.get("challenge_rating", "\u2014")), esc(data.get("xp", 0))
        ),
        "<b>\u2022 Speed:</b> {}".format(_speed(data.get("speed"))),
        "",
        "<b>Ability Scores</b>",
        "STR {} \u2022 DEX {} \u2022 CON {}".format(
            _stat(data.get("strength")), _stat(data.get("dexterity")), _stat(data.get("constitution"))
        ),
        "INT {} \u2022 WIS {} \u2022 CHA {}".format(
            _stat(data.get("intelligence")), _stat(data.get("wisdom")), _stat(data.get("charisma"))
        ),
    ]

    extras: list[str] = []
    for label, key in (
        ("Saving Throws", "proficiencies"),
        ("Damage Resistances", "damage_resistances"),
        ("Damage Immunities", "damage_immunities"),
        ("Damage Vulnerabilities", "damage_vulnerabilities"),
        ("Condition Immunities", "condition_immunities"),
    ):
        text = names_of(data.get(key))
        if text:
            extras.append(f"<b>{label}:</b> {esc(text)}")
    senses = data.get("senses")
    if senses:
        extras.append(f"<b>Senses:</b> {esc(clean(str(senses)))}")
    languages = names_of(data.get("languages"))
    if languages:
        extras.append(f"<b>Languages:</b> {esc(languages)}")
    proficiency = data.get("proficiency_bonus")
    if proficiency:
        extras.append(f"<b>Proficiency Bonus:</b> +{esc(proficiency)}")
    if extras:
        header += ["", *extras]

    body: list[str] = []
    for section, label in (
        ("special_abilities", "\U0001f9ea Special Abilities"),
        ("actions", "\u2694\ufe0f Actions"),
        ("reactions", "\U0001f504 Reactions"),
        ("legendary_actions", "\u2728 Legendary Actions"),
    ):
        items = data.get(section) or []
        if not items:
            continue
        body.append(f"<b>{label}</b>")
        for item in items:
            name = clean(item.get("name", ""))
            body.append(f"<b>{esc(name)}:</b> {esc(desc_text(item.get('desc')))}")
            for attack in item.get("actions") or []:
                body.append(f"  \u2022 <b>{esc(clean(attack.get('name','Attack')))}:</b> "
                            f"{esc(desc_text(attack.get('desc')))}")
        body.append("")

    pages = [page(header)]
    if body:
        pages += split_pages(page(body))
    pages.append(SRD_CREDIT)
    return pages


# -- spells ---------------------------------------------------------------
def format_spell(entry, data: dict) -> list[str]:
    level = data.get("level", entry.level)
    school = data.get("school", {})
    school_name = school.get("name", "") if isinstance(school, dict) else str(school)
    slot = "cantrip" if level == 0 else f"level {level}"
    ritual = " \u2022 Ritual" if data.get("ritual") else ""
    concentration = (
        " \u2022 <b>Concentration</b>" if data.get("concentration") else ""
    )

    lines = [
        f"<b>\U0001f52e {esc(data.get('name', entry.name))}</b>",
        f"<i>{esc(school_name)} {esc(slot)}{ritual}{concentration}</i>",
        "",
    ]
    for label, key in (
        ("Casting Time", "casting_time"),
        ("Range", "range"),
        ("Components", "components"),
        ("Duration", "duration"),
        ("Materials", "materials"),
    ):
        value = data.get(key)
        if key == "components" and value:
            parts = []
            for comp in value:
                if isinstance(comp, dict):
                    amount = comp.get("amount")
                    label_text = comp.get("name") or str(comp.get("type", "")).title()
                    parts.append(f"{esc(label_text)} ({esc(amount)})" if amount else esc(label_text))
            value = ", ".join(parts)
        if value:
            lines.append(f"<b>{label}:</b> {esc(str(value))}")

    lines.append("")
    lines.append(esc(desc_text(data.get("desc"))))

    higher = data.get("higher_level")
    if higher:
        lines += ["", f"<b>At Higher Levels:</b> {esc(desc_text(higher))}"]
    elif isinstance(data.get("damage_at_slot_level"), dict) and data["damage_at_slot_level"]:
        levels = data["damage_at_slot_level"]
        shown = ", ".join(
            f"L{slot} {esc(notation)}" for slot, notation in sorted(levels.items(), key=lambda kv: int(kv[0]))
        )
        lines += ["", f"<b>Damage by Slot Level:</b> {shown}"]

    damage = data.get("damage")
    if isinstance(damage, dict) and damage.get("damage_type"):
        lines += ["", f"<b>Damage Type:</b> {esc(names_of(damage['damage_type']))}"]
    elif isinstance(damage, list) and damage:
        lines += ["", f"<b>Damage:</b> {esc(names_of(damage))}"]

    classes = data.get("classes")
    if classes:
        lines += ["", f"<b>Classes:</b> {esc(names_of(classes))}"]
    lines += ["", SRD_CREDIT]
    return split_pages(page(lines))


# -- items ----------------------------------------------------------------
def format_item(entry, data: dict) -> list[str]:
    lines = [f"<b>\U0001f4b0 {esc(data.get('name', entry.name))}</b>"]
    headline: list[str] = []
    for label, key in (
        ("Category", "type"),
        ("Rarity", "rarity"),
        ("Cost", "cost"),
        ("Weight", "weight"),
    ):
        value = data.get(key)
        if value:
            headline.append(f"<b>{label}:</b> {esc(value)}")
    if headline:
        lines += [esc(" \u2022 ".join(headline))]
    if data.get("requires_attunement"):
        lines.append("<i>Requires attunement</i>")
    lines.append("")

    lines.append(esc(desc_text(data.get("desc"))))
    for key, label in (("damage", "Damage"), ("armor_class", "Armor Class")):
        value = data.get(key)
        if value:
            lines += ["", f"<b>{label}:</b> {esc(clean(desc_text(value)) or _armor_class(value))}"]
    if data.get("attunement"):
        lines += ["", f"<b>Attunement:</b> {esc(desc_text(data['attunement']))}"]
    lines += ["", SRD_CREDIT]
    return split_pages(page(lines))


# -- rules ----------------------------------------------------------------
def format_rule(entry, data: dict) -> list[str]:
    lines = [f"<b>\u2696 {esc(data.get('name', entry.name))}</b>", ""]
    lines.append(esc(desc_text(data.get("desc"))))
    children = data.get("children") or []
    for child in children:
        child_name = child.get("name", "")
        lines += ["", f"<b>{esc(child_name)}</b>", esc(desc_text(child.get("desc")))]
    lines += ["", SRD_CREDIT]
    return split_pages(page(lines))


# -- classes --------------------------------------------------------------
def format_class(entry, data: dict) -> list[str]:
    levels = data.get("class_levels") or []
    saving = names_of(data.get("saving_throws"))
    lines = [
        f"<b>\U0001f977 {esc(data.get('name', entry.name))}</b>",
        f"<b>Hit Die:</b> d{esc(data.get('hit_die', 8))}",
    ]
    if saving:
        lines.append(f"<b>Saving Throws:</b> {esc(saving)}")
    lines += ["", esc(desc_text(data.get("desc")) or "See the class levels below.")]

    subclasses = data.get("subclasses")
    if subclasses:
        lines += ["", f"<b>Subclasses:</b> {esc(names_of(subclasses))}"]

    if levels:
        lines += ["", "<b>Levels</b>"]
        for level in sorted(levels, key=lambda item: item.get("level", 0)):
            number = level.get("level")
            features = names_of(level.get("features"))
            bits = [f"<b>Level {esc(number)}</b>"]
            bonuses = level.get("ability_score_bonuses")
            if bonuses:
                bits.append(f"\u2022 ASI: +{esc(bonuses)}")
            if level.get("prof_bonus"):
                bits.append(f"\u2022 Proficiency +{esc(level['prof_bonus'])}")
            if features:
                bits.append(f"\u2022 {esc(features)}")
            lines.append(" ".join(bits))

    lines += ["", SRD_CREDIT]
    return split_pages(page(lines))


# -- races / conditions / generic ----------------------------------------
def format_race(entry, data: dict) -> list[str]:
    increases = data.get("ability_score_increases") or {}
    size = data.get("size")
    lines = [
        f"<b>\U0001f9d9 {esc(data.get('name', entry.name))}</b>",
    ]
    bits: list[str] = []
    if size:
        bits.append(f"<b>Size:</b> {esc(size.get('value', size) if isinstance(size, dict) else size)}")
    speed = data.get("speed")
    if speed:
        bits.append(f"<b>Speed:</b> {esc(_speed(speed))}")
    if increases:
        bits.append(
            "<b>Ability Increases:</b> "
            + ", ".join(f"{esc(k)} +{esc(v)}" for k, v in increases.items())
        )
    if bits:
        lines += [esc(" \u2022 ".join(bits))]
    lines += ["", esc(desc_text(data.get("desc")))]
    for trait in data.get("traits") or []:
        lines += ["", f"<b>{esc(clean(trait.get('name', 'Trait')))}</b>",
                  esc(desc_text(trait.get("desc")))]
    lines += ["", SRD_CREDIT]
    return split_pages(page(lines))


def format_condition(entry, data: dict) -> list[str]:
    lines = [
        f"<b>\U0001f912 {esc(data.get('name', entry.name))}</b>",
        "",
        esc(desc_text(data.get("desc"))),
        "",
        SRD_CREDIT,
    ]
    return split_pages(page(lines))


def format_generic(entry, data: dict) -> list[str]:
    lines = [f"<b>{esc(data.get('name', entry.name))}</b>", "", esc(desc_text(data.get("desc")))]
    if isinstance(data.get("type"), str):
        lines += ["", f"<b>Type:</b> {esc(data['type'])}"]
    lines += ["", SRD_CREDIT]
    return split_pages(page(lines))


FORMATTERS = {
    "monsters": format_monster,
    "spells": format_spell,
    "magicitems": format_item,
    "equipment": format_item,
    "rules": format_rule,
    "classes": format_class,
    "races": format_race,
    "subraces": format_race,
    "conditions": format_condition,
}


def format_entry(category: str, entry, data: dict) -> list[str]:
    """Render an SRD record as a list of Telegram-ready HTML pages."""
    formatter = FORMATTERS.get(category, format_generic)
    pages = formatter(entry, data)
    if pages and pages[-1] != SRD_CREDIT:
        pages.append(SRD_CREDIT)
    return [p for p in pages if p.strip()]


def format_search_hit(category, entry) -> str:
    """One line for a search-result list."""
    bits = [f"<code>{esc(entry.name)}</code>"]
    if entry.level is not None and entry.level != "":
        bits.append("L0" if entry.level == 0 else f"L{entry.level}")
    for key, label in (("challenge_rating", "CR"), ("rarity", ""), ("type", ""), ("school", "")):
        value = entry.extra.get(key)
        if not value:
            continue
        if key == "challenge_rating":
            bits.append(f"CR {value}")
        elif isinstance(value, dict) and value.get("name"):
            bits.append(str(value["name"]))
        elif isinstance(value, str):
            bits.append(value.title() if key == "rarity" else value)
    return " \u2022 ".join(bits)
