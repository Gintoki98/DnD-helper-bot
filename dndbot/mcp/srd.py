"""SRD tools: read-only access to the 5e System Reference Document.

Same client, same ``.cache/`` and same formatters as the bot; the differences
are the output dialect (Markdown instead of Telegram HTML) and the fact that
there is no pager - a tool answers with everything at once.
"""

from __future__ import annotations

from inspect import cleandoc

from mcp.server.mcpserver.exceptions import ToolError

from ..formatting import format_entry, format_search_hit
from ..srd import (
    BROWSE_PAGE,
    CATEGORIES,
    SRDError,
    resolve_category,
    srd as srd_client,
)
from .render import esc_md, strip, to_markdown

# Long records arrive as pages; a tool has no 4096-character limit, so they
# are joined rather than paged.
_PAGE_JOIN = "\n\n---\n\n"


def _category(name: str):
    """The category behind a word the caller wrote, or a ToolError listing them."""
    cat = resolve_category(name)
    if cat is None:
        raise ToolError(
            f"I do not know a category called {name!r}. "
            "Try one of: " + ", ".join(sorted(CATEGORIES)) + "."
        )
    return cat


async def srd_lookup(category: str, name: str) -> str:
    """Look up one SRD entry. Telegram: ``/monster``, ``/spell``, ``/item``, ``/rule``, ``/equipment``, ``/condition``, ``/class``, ``/race``, ``/subrace``, ``/skill``, ``/prof``, ``/damagetype``, ``/align``.

    ``category`` takes the singular or the plural ("monster" or "monsters");
    ``name`` is the entry name, e.g. "adult red dragon" or "fireball". Ask for
    a category with no name to be told how many entries it holds.
    """
    cat = _category(category)

    try:
        entry, data = await srd_client.get(cat.key, name)
        if cat.key == "classes":
            data = await srd_client.with_class_levels(data)
    except SRDError as exc:
        raise ToolError(await _with_suggestions(cat.key, name, exc)) from exc

    return to_markdown(_PAGE_JOIN.join(format_entry(cat.key, entry, data)))


async def _with_suggestions(category: str, name: str, exc: SRDError) -> str:
    """The lookup failure, plus near misses when the API has any to offer."""
    try:
        candidates = await srd_client.search(category, name, limit=5)
    except SRDError:
        candidates = []
    message = strip(exc)
    if candidates:
        message += "\n\nDid you mean: " + ", ".join(
            f"`{entry.name}`" for entry in candidates
        )
    return message


async def srd_search(query: str, limit: int = 12) -> str:
    """Search every SRD category at once. Telegram: ``/search fireball``.

    Hits come from spells, monsters, magic items, equipment, rules, classes,
    races and conditions - at most four per category and ``limit`` in total.
    Read any hit back with ``srd_lookup``.
    """
    term = query.strip()
    if not term:
        raise ToolError('What should I look for? Something like "fireball".')

    hits = await srd_client.search_all(term, limit=max(1, limit))
    if not hits:
        raise ToolError(
            f"Nothing in the SRD matches {term!r}. "
            'Try fewer words - "dragon" or "long rest".'
        )

    lines = [f'🔍 Results for "{term}"', ""]
    for key, entry in hits:
        lines.append(
            f"{CATEGORIES[key].emoji} {to_markdown(format_search_hit(key, entry))}"
        )
    return "\n".join(lines)


async def srd_random(category: str, challenge_rating: str | None = None) -> str:
    """A random entry from a category. Telegram: ``/randmonster [cr]``, ``/randspell``.

    ``challenge_rating`` is for monsters only and uses the command's own
    syntax: ``"2"`` is exactly CR 2, while ``"1/4"`` is the *range* 1 to 4 -
    the slash separates the two ends, it is not a fraction. Omit it for any
    other category and for an unfiltered monster.
    """
    cat = _category(category)
    if challenge_rating is not None and cat.key != "monsters":
        raise ToolError(
            "challenge_rating is a monsters-only argument; "
            f"{cat.label.lower()} do not have one. Omit it."
        )

    low = high = None
    if challenge_rating is not None:
        low, sep, high = challenge_rating.partition("/")
        try:
            low = float(low)
            high = float(high) if sep else low
        except ValueError:
            raise ToolError(
                f'challenge_rating must look like "2" or "1/4", '
                f"got {challenge_rating!r}."
            ) from None

    try:
        if low is None:
            entry, data = await srd_client.random_entry(cat.key)
        else:
            entry, data = await srd_client.random_by_cr(low, high)
    except SRDError as exc:
        raise ToolError(strip(exc)) from exc

    return to_markdown(_PAGE_JOIN.join(format_entry(cat.key, entry, data)))


async def srd_list(category: str, page: int = 1) -> str:
    """Browse a whole SRD category, eight entries per page. Telegram: the SRD menu (``/start`` -> SRD).

    ``page`` is 1-based and out-of-range pages are clamped rather than
    rejected; the footer says how many pages there are. Spells carry their
    level, as the bot's browser shows them.
    """
    cat = _category(category)

    try:
        entries = await srd_client.index(cat.key)
    except SRDError as exc:
        raise ToolError(strip(exc)) from exc

    total_pages = max(1, -(-len(entries) // BROWSE_PAGE))
    page = max(1, min(page, total_pages))
    window = entries[(page - 1) * BROWSE_PAGE : page * BROWSE_PAGE]

    lines = [f"**{cat.emoji} {cat.label}** *({len(entries)} in the SRD)*", ""]
    for entry in window:
        tag = (
            f" *L{entry.level}*"
            if isinstance(entry.level, int) and cat.key == "spells"
            else ""
        )
        lines.append(f"- **{esc_md(entry.name)}**{tag}")
    lines += ["", f"_Page {page} of {total_pages}_"]
    return "\n".join(lines)


def register(mcp) -> None:
    """Register this module's tools on the server (cleaned docstring first)."""
    for tool in (srd_lookup, srd_search, srd_random, srd_list):
        mcp.add_tool(tool, description=cleandoc(tool.__doc__ or ""))
