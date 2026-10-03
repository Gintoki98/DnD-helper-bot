"""SRD lookups: /monster, /spell, /item, /rule, /search and the browser."""

from __future__ import annotations

from typing import Any

from telethon import events

from .. import keyboards as kb
from ..common import MESSAGE_LIMIT, command_argument, send_view
from ..formatting import format_entry, format_search_hit
from ..keyboards import entry_from_token
from ..srd import BROWSE_PAGE, CATEGORIES, SRDError, srd


async def send_entry(
    event,
    category: str,
    term: str,
    page: int = 0,
    random_pick: bool = False,
    replace: bool | None = None,
) -> None:
    """Resolve a term, render the record and show it with a pager if it is long.

    ``replace`` defaults to editing the triggering message for a button press
    (pagers, search results) and replying for a typed command. The dice pad
    passes ``replace=False`` so a random monster does not overwrite the roll
    that is already on screen.
    """
    if random_pick:
        entry, data = await srd.random_entry(category)
    else:
        entry, data = await srd.get(category, term)
        if category == "classes":
            data = await srd.with_class_levels(data)

    pages = format_entry(category, entry, data)
    page = max(0, min(page, len(pages) - 1))
    buttons = kb.entry_keyboard(category, entry.index, page, len(pages)) if len(pages) > 1 else None

    text = pages[page]
    if buttons and len(text) + 60 > MESSAGE_LIMIT:
        text = text[: MESSAGE_LIMIT - 200].rsplit("\n", 1)[0]

    await send_view(event, text, buttons, replace=replace)


async def send_search(event, term: str) -> None:
    """Search every usable category and show the best hits."""
    if not term:
        await event.reply(
            "What should I look for?\n<code>/search fireball</code>",
            parse_mode="html",
        )
        return

    hits: list[tuple[str, Any]] = await srd.search_all(term, limit=12, per_category=4)

    if not hits:
        await event.reply(
            f"\U0001f50d Nothing in the SRD matches <b>{term}</b>.\n"
            "Try fewer words - <code>/search dragon</code> or <code>/search long rest</code>.",
            parse_mode="html",
        )
        return

    lines = [f"<b>\U0001f50d Results for \"{term}</b>", ""]
    for cat, entry in hits:
        lines.append(f"{CATEGORIES[cat].emoji} {format_search_hit(cat, entry)}")
    await event.reply(
        "\n".join(lines),
        buttons=kb.search_results_keyboard([(c, e.index, e.name) for c, e in hits]),
        parse_mode="html",
    )


def register(client) -> None:
    lookup_patterns = [
        ("monster", "monsters", r"^/monster(?:@[\w_]+)?(?:\s+(.*))?$"),
        ("spell", "spells", r"^/spell(?:@[\w_]+)?(?:\s+(.*))?$"),
        ("item", "magicitems", r"^/item(?:@[\w_]+)?(?:\s+(.*))?$"),
        ("rule", "rules", r"^/rule(?:@[\w_]+)?(?:\s+(.*))?$"),
    ]

    for command, category, pattern in lookup_patterns:
        client.add_event_handler(_make_lookup(command, category), events.NewMessage(pattern=pattern))

    @client.on(events.NewMessage(pattern=r"^/(?:equipment|gear)(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def equipment_lookup(event: events.NewMessage.Event) -> None:
        await _lookup(event, "equipment")

    @client.on(events.NewMessage(pattern=r"^/condition(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def condition_lookup(event: events.NewMessage.Event) -> None:
        await _lookup(event, "conditions")

    @client.on(events.NewMessage(pattern=r"^/(?:class|classes)(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def class_lookup(event: events.NewMessage.Event) -> None:
        await _lookup(event, "classes")

    @client.on(events.NewMessage(pattern=r"^/race(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def race_lookup(event: events.NewMessage.Event) -> None:
        await _lookup(event, "races")

    @client.on(events.NewMessage(pattern=r"^/subrace(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def subrace_lookup(event: events.NewMessage.Event) -> None:
        await _lookup(event, "subraces")

    @client.on(events.NewMessage(pattern=r"^/skill(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def skill_lookup(event: events.NewMessage.Event) -> None:
        await _lookup(event, "skills")

    @client.on(events.NewMessage(pattern=r"^/(?:prof|proficiency)(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def proficiency_lookup(event: events.NewMessage.Event) -> None:
        await _lookup(event, "proficiencies")

    @client.on(events.NewMessage(pattern=r"^/damagetype(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def damage_type_lookup(event: events.NewMessage.Event) -> None:
        await _lookup(event, "damagetypes")

    @client.on(events.NewMessage(pattern=r"^/align(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def alignment_lookup(event: events.NewMessage.Event) -> None:
        await _lookup(event, "alignments")

    @client.on(events.NewMessage(pattern=r"^/search(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def search_command(event: events.NewMessage.Event) -> None:
        term = command_argument(event)
        await send_search(event, term)

    @client.on(
        events.NewMessage(pattern=r"^/randmonster(?:@[\w_]+)?(?:\s+([\d/]+))?$")
    )
    async def random_monster(event: events.NewMessage.Event) -> None:
        cr = command_argument(event)
        if cr:
            if "/" in cr:
                low, _, high = cr.partition("/")
                try:
                    low_cr, high_cr = float(low), float(high)
                except ValueError:
                    await event.reply("Give the CR range like <code>/randmonster 1/4</code>", parse_mode="html")
                    return
                await _random_cr(event, low_cr, high_cr)
                return
            try:
                value = float(cr)
            except ValueError:
                await event.reply("Give a number like <code>/randmonster 2</code>", parse_mode="html")
                return
            await _random_cr(event, value, value)
            return
        await send_entry(event, "monsters", "", random_pick=True)

    @client.on(events.NewMessage(pattern=r"^/randspell(?:@[\w_]+)?(?:\s+(\d))?$"))
    async def random_spell(event: events.NewMessage.Event) -> None:
        await send_entry(event, "spells", "", random_pick=True)

    # -- callbacks -------------------------------------------------------
    @client.on(events.CallbackQuery(pattern=r"^srdmenu:"))
    async def open_entry(event: events.CallbackQuery.Event) -> None:
        token = event.data.decode().split(":", 1)[1]
        resolved = entry_from_token(token)
        if resolved is None:
            await event.answer("That entry expired - search for it again.", alert=True)
            return
        category, index = resolved
        await event.answer("Loading\u2026")
        try:
            await send_entry(event, category, index, page=0)
        except SRDError as exc:
            await event.answer(str(exc)[:180], alert=True)

    @client.on(events.CallbackQuery(pattern=r"^srd:"))
    async def page_entry(event: events.CallbackQuery.Event) -> None:
        _, token, page = event.data.decode().split(":")
        resolved = entry_from_token(token)
        if resolved is None:
            await event.answer("That entry expired - search for it again.", alert=True)
            return
        category, index = resolved
        await event.answer(f"Page {int(page) + 1}")
        try:
            await send_entry(event, category, index, page=int(page))
        except SRDError as exc:
            await event.answer(str(exc)[:180], alert=True)

    @client.on(events.CallbackQuery(pattern=r"^browse:"))
    async def browse(event: events.CallbackQuery.Event) -> None:
        _, category, page = event.data.decode().split(":")
        page = int(page)
        cat = CATEGORIES.get(category)
        if cat is None:
            await event.answer("Unknown category")
            return
        try:
            entries = await srd.index(category)
        except SRDError as exc:
            await event.answer(str(exc), alert=True)
            return

        total_pages = max(1, -(-len(entries) // BROWSE_PAGE))
        page = max(0, min(page, total_pages - 1))
        window = entries[page * BROWSE_PAGE : (page + 1) * BROWSE_PAGE]

        lines = [
            f"<b>{cat.emoji} {cat.label}</b> <i>({len(entries)} in the SRD)</i>",
            "",
        ]
        for entry in window:
            level = entry.level
            tag = f" <i>L{level}</i>" if isinstance(level, int) and category == "spells" else ""
            lines.append(f"• <b>{entry.name}</b>{tag}")

        await event.edit(
            "\n".join(lines),
            buttons=kb.browse_keyboard(category, page, total_pages),
            parse_mode="html",
        )


def _make_lookup(command: str, category: str):
    async def handler(event: events.NewMessage.Event) -> None:
        await _lookup(event, category)

    handler.__name__ = f"lookup_{command}"
    return handler


async def _lookup(event, category: str) -> None:
    term = command_argument(event)
    if not term:
        entries = await _safe_index(category)
        cat = CATEGORIES[category]
        await event.reply(
            f"Give me a name, e.g. <code>/{category[:6]} goblin</code>.\n"
            f"Tap to browse all {len(entries)} {cat.label.lower()}.",
            buttons=kb.browse_keyboard(category, 0, 1),
            parse_mode="html",
        )
        return
    try:
        await send_entry(event, category, term)
    except SRDError as exc:
        suggestions = []
        try:
            for entry in await srd.search(category, term, limit=5):
                suggestions.append(entry.name)
        except SRDError:
            pass
        extra = ""
        if suggestions:
            extra = "\n\nDid you mean: " + ", ".join(f"<code>{s}</code>" for s in suggestions)
        await event.reply(f"\U0001f50d {exc}{extra}", parse_mode="html")


async def _safe_index(category: str) -> list:
    try:
        return await srd.index(category)
    except SRDError:
        return []


async def _random_cr(event, low: float, high: float) -> None:
    """Pick a monster whose challenge rating sits in the given range."""
    try:
        pick, _data = await srd.random_by_cr(low, high)
    except SRDError as exc:
        await event.reply(str(exc), parse_mode="html")
        return
    await send_entry(event, "monsters", pick.name)
