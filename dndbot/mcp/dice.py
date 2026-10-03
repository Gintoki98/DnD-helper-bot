"""Dice tools.

Parsing, limits and rolling all live in ``dndbot/dice.py``; this module only
turns a roll into words the model can read.
"""

from __future__ import annotations

from inspect import cleandoc

from mcp.server.mcpserver.exceptions import ToolError

from ..dice import DiceError, RollResult, initiative_order, roll as roll_notation
from ..storage import db
from . import inputs
from .render import esc_md, strip


def summary(result: RollResult) -> str:
    """Markdown twin of the bot's roll breakdown (``handlers/dice.describe``).

    Same information, different dialect: the bot says it in Telegram HTML with
    a keyboard attached, here it has to stand alone as text.
    """
    lines = [f"🎲 **{result.total}** — `{result.expression}`"]
    if result.advantage:
        lines.append(f"*{'Advantage' if result.advantage == 'adv' else 'Disadvantage'}*")
    for term in result.terms:
        if not term.count:
            continue  # a flat modifier shows up in the headline total
        shown = []
        for die in term.dice:
            mark = "" if die.kept else " ✗"
            note = f" *{die.note}*" if die.note else ""
            shown.append(f"`{die.value}`{mark}{note}")
        lines.append(" ".join(shown) + f"  (*{term.label}*)")

    natural = result.natural
    if natural == 20:
        lines.append("🌟 **Critical hit!**")
    elif natural == 1:
        lines.append("💀 **Critical failure.**")
    if result.dropped:
        lines.append("*✗ = discarded*")
    return "\n".join(lines)


async def party_initiative(
    bonus: int = 0, user_id: int | None = None, campaign: str | None = None
) -> str:
    """Roll initiative for the whole party. Telegram: ``/init [bonus]``.

    Everyone with a character in the campaign rolls d20 + their own initiative
    modifier + ``bonus``, highest first. ``campaign`` is a name or invite code;
    without one the user's selected campaign is used.
    """
    row = await inputs.campaign(user_id, campaign)
    party = await db.party(row["id"])
    if not party:
        raise ToolError("Nobody in this campaign has a character yet.")

    lines = [f"🎯 **Initiative for {esc_md(row['name'])}**", ""]
    for total, member in initiative_order(party, bonus):
        lines.append(
            f"{total:>3}. **{esc_md(member['name'])}** "
            f"*{esc_md(member['class_name'])}* `{member['initiative']:+d}`"
        )
    return "\n".join(lines)


def roll(expression: str) -> str:
    """Roll dice. Telegram: ``/roll 2d6+3``, ``/roll adv``, ``/roll dis``, ``/adv:+5``.

    Notation: ``d20``, ``2d6+3``, ``4d6kh3`` (keep highest), ``2d20kl1``
    (keep lowest), ``4d6r<=1`` (reroll), ``2d6!>=6`` (exploding), several
    ``+``/``-`` terms, and ``adv:``/``dis:`` prefixes for advantage.
    """
    try:
        result = roll_notation(expression)
    except DiceError as exc:
        raise ToolError(strip(exc)) from exc
    return summary(result)


def register(mcp) -> None:
    """Register this module's tools on the server.

    ``description=cleandoc(...)`` because the SDK reads ``fn.__doc__`` as it
    sits in the source, indentation included.
    """
    for tool in (roll, party_initiative):
        mcp.add_tool(tool, description=cleandoc(tool.__doc__ or ""))
