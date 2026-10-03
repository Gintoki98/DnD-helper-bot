"""Inline keyboard builders.

Callback payloads are short strings under Telegram's 64-byte limit:

    dice:<op>[:arg]              dice keyboard
    srd:<token>:<page>           SRD entry, one page at a time
    srdmenu:<token>              entry overview
    browse:<cat>:<page>          paginated category listing
    join:<request_id>:<y|n>      DM approves or denies a player
    camp:<campaign_id>           open a campaign panel
    char:<action>                character sheet actions
    menu:<target>                back to a top-level menu

SRD slugs are far too long to embed (``amulet-of-proof-against-detection-and-
-location`` alone is 49 bytes), so entries are addressed by a short token that
maps back to ``(category, index)`` in :func:`entry_from_token`.
"""

from __future__ import annotations

import itertools
from typing import Any, Sequence

from telethon import Button

from . import dice as dice_mod
from .common import display_name
from .srd import CATEGORIES

DICE_SIDES = [4, 6, 8, 10, 12, 20, 100]

# token -> (category, index) and the reverse map
_TOKENS: dict[str, str] = {}
_TOKEN_ENTRIES: dict[str, tuple[str, str]] = {}
_SEQUENCE = itertools.count(1)


def entry_token(category: str, index: str) -> str:
    """A short, stable callback token for an SRD entry."""
    key = f"{category}:{index}"
    token = _TOKENS.get(key)
    if token is None:
        token = f"t{next(_SEQUENCE)}"
        _TOKENS[key] = token
        _TOKEN_ENTRIES[token] = (category, index)
    return token


def entry_from_token(token: str) -> tuple[str, str] | None:
    """Resolve a token back to (category, index), or None if unknown."""
    return _TOKEN_ENTRIES.get(token)


def dice_keyboard(expression: str = "", page: int = 0) -> list:
    """The dice pad: dice sizes, an operator row and roll/clear."""
    rows: list[list] = []
    row = [
        Button.inline(f"\U0001f3b2 d{sides}", f"dice:d{sides}")
        for sides in DICE_SIDES
    ]
    rows.append(row)

    expression = expression or ""
    rows.append(
        [
            Button.inline("\u2b06\ufe0f d20 adv", "dice:adv"),
            Button.inline("\u2b07\ufe0f d20 dis", "dice:dis"),
            Button.inline("\U0001f3b2 4d6kh3", "dice:kh3"),
        ]
    )
    rows.append(
        [
            Button.inline("+", "dice:op:+"),
            Button.inline("\u2212", "dice:op:-"),
            Button.inline("\u00d7", "dice:op:*"),
            Button.inline("\U0001f5d1\ufe0f clr", "dice:clr"),
        ]
    )
    rows.append(
        [
            Button.inline("\U0001f3b2 Roll", f"dice:go:{expression[:30]}"),
            Button.inline("\u2328 Done", "dice:done"),
        ]
    )
    if expression:
        rows.append([Button.inline(f"\U0001f5c2 {expression}", f"dice:noop:{expression[:30]}")])
    return rows


def main_menu() -> list:
    return [
        [
            Button.inline("\U0001f3b2 Roll dice", "dice:open"),
            Button.inline("\U0001f9d9 Character", "char:open"),
        ],
        [
            Button.inline("\U0001f3dd Campaign", "camp:menu"),
            Button.inline("\U0001f5c2\ufe0f SRD", "menu:srd"),
        ],
        [Button.inline("\u2753 Help", "menu:help"),
         Button.inline("\U0001f4d6 Tutorial", "tut:0")],
    ]


def srd_menu() -> list:
    """The SRD browser: one button per category that has usable data."""
    rows = []
    line = []
    for key in ("monsters", "spells", "magicitems", "equipment", "rules",
                "classes", "races", "subraces", "conditions"):
        cat = CATEGORIES.get(key)
        if cat is None:
            continue
        line.append(Button.inline(f"{cat.emoji} {cat.label}", f"browse:{cat.key}:0"))
        if len(line) == 2:
            rows.append(line)
            line = []
    if line:
        rows.append(line)
    rows.append([Button.inline("\U0001f3e0 Menu", "menu:home")])
    return rows


def browse_keyboard(category: str, page: int, total_pages: int) -> list:
    nav = []
    if page > 0:
        nav.append(Button.inline("\u25c0", f"browse:{category}:{page - 1}"))
    nav.append(Button.inline(f"{page + 1}/{total_pages}", f"browse:{category}:{page}"))
    if page + 1 < total_pages:
        nav.append(Button.inline("\u25b6", f"browse:{category}:{page + 1}"))
    return [nav, [Button.inline("\u2190 SRD", "menu:srd")]]


def entry_keyboard(category: str, index: str, page: int, total_pages: int) -> list:
    """Pager for a multi-page SRD record."""
    token = entry_token(category, index)
    rows = []
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(Button.inline("\u25c0", f"srd:{token}:{page - 1}"))
        nav.append(Button.inline(f"{page + 1}/{total_pages}", f"srd:{token}:{page}"))
        if page + 1 < total_pages:
            nav.append(Button.inline("\u25b6", f"srd:{token}:{page + 1}"))
        rows.append(nav)
    rows.append([Button.inline("\u2190 Back", f"srdmenu:{token}")])
    return rows


def join_request_keyboard(request_id: int) -> list:
    return [
        [
            Button.inline("\u2705 Approve", f"join:{request_id}:y"),
            Button.inline("\u274c Deny", f"join:{request_id}:n"),
        ]
    ]


def campaign_keyboard(campaign_id: int, is_dm: bool, has_session: bool) -> list:
    rows = [
        [
            Button.inline("\U0001f91d Roster", f"camp:roster:{campaign_id}"),
            Button.inline("\U0001f465 Party", f"char:party:{campaign_id}"),
        ]
    ]
    if is_dm:
        rows.append(
            [
                Button.inline(
                    "\u23f8 End session" if has_session else "\u25b6 Start session",
                    f"camp:{'end' if has_session else 'start'}:{campaign_id}",
                ),
                Button.inline("\u23f3 Pending", f"camp:pending:{campaign_id}"),
            ]
        )
    rows.append([Button.inline("\U0001f517 Invite", f"camp:invite:{campaign_id}")])
    return rows


def campaigns_list_keyboard(rows_db: Sequence[Any]) -> list:
    buttons = [
        [Button.inline(f"\U0001f3dd {row['name']}", f"camp:{row['id']}")]
        for row in rows_db
    ]
    if buttons:
        buttons.append([Button.inline("\u2795 New campaign", "camp:new")])
    buttons.append([Button.inline("\U0001f3e0 Menu", "menu:home")])
    return buttons


def roster_keyboard(members: Sequence[Any], campaign_id: int) -> list:
    """One button per player so the DM can open that person's character."""
    buttons = []
    for member in members:
        if member["role"] == "dm":
            continue
        label = display_name(member).split(" <code>")[0]
        buttons.append(
            [Button.inline(label[:40], f"char:of:{member['user_id']}:{campaign_id}")]
        )
    buttons.append([Button.inline("\u2190 Back", f"camp:{campaign_id}")])
    return buttons


def character_keyboard(character_id: int, is_owner: bool) -> list:
    rows = [
        [
            Button.inline("\u2764\ufe0f HP", f"char:hp:{character_id}"),
            Button.inline("\u2b50 Level", f"char:level:{character_id}"),
        ],
        [
            Button.inline("\U0001f4c8 XP", f"char:xp:{character_id}"),
            Button.inline("\U0001f9d9\ufe0f Sheet", f"char:view:{character_id}"),
        ],
    ]
    rows.append([Button.inline("\U0001f3e0 Menu", "menu:home")])
    return rows


def quick_roll_keyboard(expression: str) -> list:
    """Under a roll result: reroll, add a modifier, or swing back at something."""
    return [
        [
            Button.inline("\U0001f504 Reroll", f"dice:go:{expression[:30]}"),
            Button.inline("\u2795 +1", f"dice:go:({expression[:28]})+1"),
        ],
        [
            Button.inline("\U0001f409 Monster", "dice:encounter"),
            Button.inline("\U0001f3e0 Menu", "menu:home"),
        ],
    ]


def search_results_keyboard(hits: Sequence[tuple[str, str]]) -> list:
    buttons = [
        [
            Button.inline(
                f"{CATEGORIES[cat].emoji} {name[:38]}",
                f"srdmenu:{entry_token(cat, index)}",
            )
        ]
        for cat, index, name in hits
    ]
    buttons.append([Button.inline("\U0001f50d Search", "menu:search")])
    return buttons


def dice_roll_summary(result: dice_mod.RollResult) -> str:
    """One-line summary: ``2d6(6+3) +3 = 12``.

    Each term is labelled so a bare die total is never confused with a
    flat modifier.
    """
    parts: list[str] = []
    for term in result.terms:
        if term.count:
            kept = [str(d.value) for d in term.dice if d.kept]
            chunk = f"{term.count}d{term.sides}({'+'.join(kept) if kept else '0'})"
            if term.flat:
                chunk += f"{term.sign * term.flat:+d}"
        else:
            chunk = f"{term.sign * term.flat:+d}"
        parts.append(("-" if term.sign < 0 and term.count else "+") + chunk if term.count else chunk)
    body = " ".join(parts).lstrip("+")
    return f"{body} = <b>{result.total}</b>"
