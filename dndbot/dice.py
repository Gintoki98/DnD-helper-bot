"""Dice notation parser and roller.

Supports the notation players actually type:

    d20                      a single d20
    2d6+3                    two d6 plus a flat modifier
    4d6kh3                   roll four d6, keep the highest three
    2d20kl1                  roll two d20, keep the lowest one
    4d6r<=1                  reroll each 1 until it is not a 1
    2d6!>=6                  exploding dice: a 6 explodes and rolls again
    4d6kh3+2d10+1            several terms, added and subtracted
    adv / dis / a / d        roll a d20 with advantage or disadvantage
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

MAX_DICE_PER_TERM = 100
MAX_TOTAL_DICE = 200
MAX_REROLLS = 20
# Hard ceiling on individual die rolls, so a pathological expression
# (explosions plus rerolls) cannot spin forever.
MAX_DIE_ROLLS = 1000

_TERM_SPLIT = re.compile(r"(?<!^)(?=[+-])")
_DIE_TERM = re.compile(r"^(?P<count>\d*)[dD](?P<sides>\d+)(?P<mods>.*)$")
_MODIFIER = re.compile(
    r"^(?P<kind>kh|kl|r|!|min|max)(?P<op>[<>=]*)(?P<value>\d*)?",
    re.IGNORECASE,
)

ADVANTAGE_ALIASES = {"adv", "a", "advantage"}
DISADVANTAGE_ALIASES = {"dis", "d", "disadvantage", "disadv"}


class DiceError(ValueError):
    """Raised when a notation cannot be parsed."""


@dataclass
class Die:
    """One rolled die, kept or not, for the breakdown display."""

    sides: int
    value: int
    kept: bool = True
    note: str = ""


@dataclass
class Term:
    """One ``[N]dS`` chunk of an expression."""

    count: int
    sides: int
    sign: int = 1
    flat: int = 0
    keep: str = ""          # "", "kh", "kl"
    keep_n: int = 0
    reroll_below: int = 0
    explode_at: int = 0
    explodes: bool = False
    dice: list[Die] = field(default_factory=list)

    @property
    def subtotal(self) -> int:
        kept = [d.value for d in self.dice if d.kept]
        return sum(kept) + self.flat

    @property
    def label(self) -> str:
        if not self.count:
            # A flat modifier carries its own sign so "-1" does not read "+1".
            return f"{self.sign * self.flat:+d}"
        base = f"{self.count}d{self.sides}"
        if self.keep:
            base += f"{self.keep}{self.keep_n}"
        if self.reroll_below:
            base += f"r<={self.reroll_below}"
        if self.explodes:
            base += f"!>={self.explode_at}"
        if self.flat:
            base += f"{self.sign * self.flat:+d}"
        return base


@dataclass
class RollResult:
    expression: str
    terms: list[Term]
    advantage: str = ""  # "", "adv", "dis"

    @property
    def total(self) -> int:
        return sum(t.sign * t.subtotal for t in self.terms)

    @property
    def all_dice(self) -> list[Die]:
        return [d for t in self.terms for d in t.dice]

    @property
    def dropped(self) -> list[Die]:
        return [d for d in self.all_dice if not d.kept]

    @property
    def natural(self) -> int | None:
        """Natural roll of a lone d20, used for crit/fumble messaging."""
        if len(self.all_dice) != 1:
            return None
        die = self.all_dice[0]
        return die.value if die.sides == 20 else None


def _parse_modifiers(term: Term, raw: str) -> None:
    pos = 0
    raw = raw.strip()
    while pos < len(raw):
        match = _MODIFIER.match(raw, pos)
        if not match:
            raise DiceError(f"I do not understand the modifier {raw[pos:]!r}")
        kind = match.group("kind").lower()
        value = int(match.group("value") or 0)
        op = match.group("op")

        if kind == "kh":
            term.keep, term.keep_n = "kh", value or 1
        elif kind == "kl":
            term.keep, term.keep_n = "kl", value or 1
        elif kind == "r":
            if op not in {"<", "<="}:
                raise DiceError("Rerolls use r<=N or r<N, for example 4d6r<=1")
            term.reroll_below = value
        elif kind == "!":
            term.explodes = True
            if op in {">=", ">"}:
                term.explode_at = value
            else:
                term.explode_at = max(6, term.sides // 3)
        elif kind == "min":
            term.flat += value
        elif kind == "max":
            term.flat -= value
        pos = match.end()


def parse(expression: str) -> RollResult:
    """Parse a dice expression without rolling it."""
    raw = expression.strip().lower().replace(" ", "")
    if not raw:
        raise DiceError("Give me something to roll, for example 2d6+3")

    advantage = ""
    if raw in ADVANTAGE_ALIASES:
        raw, advantage = "2d20kh1", "adv"
    elif raw in DISADVANTAGE_ALIASES:
        raw, advantage = "2d20kl1", "dis"
    elif raw.startswith("adv:"):
        raw, advantage = raw[4:], "adv"
    elif raw.startswith("dis:"):
        raw, advantage = raw[4:], "dis"

    terms: list[Term] = []
    for chunk in _TERM_SPLIT.split(raw):
        if not chunk:
            continue
        sign = 1
        if chunk[0] in "+-":
            sign = -1 if chunk[0] == "-" else 1
            chunk = chunk[1:]
            if not chunk:
                raise DiceError(
                    "That expression ends with a + or - and no number after it"
                )

        match = _DIE_TERM.match(chunk)
        if match:
            count = int(match.group("count") or 1)
            sides = int(match.group("sides"))
            if count < 1 or count > MAX_DICE_PER_TERM:
                raise DiceError(f"Roll between 1 and {MAX_DICE_PER_TERM} dice per term")
            if sides < 2 or sides > 1000:
                raise DiceError("Dice need between 2 and 1000 sides")
            term = Term(count=count, sides=sides, sign=sign)
            _parse_modifiers(term, match.group("mods"))
            if term.keep and not 1 <= term.keep_n <= term.count:
                raise DiceError(
                    f"You cannot keep {term.keep_n} of {term.count}d{term.sides}"
                )
        else:
            try:
                term = Term(count=0, sides=0, sign=sign, flat=int(chunk))
            except ValueError as exc:
                raise DiceError(
                    f"{chunk!r} is not something I can roll. Try 2d6+3 or 4d6kh3."
                ) from exc
        terms.append(term)

    if not terms:
        raise DiceError("Nothing to roll in that expression")
    total_dice = sum(t.count for t in terms)
    if total_dice > MAX_TOTAL_DICE:
        raise DiceError(f"That is {total_dice} dice at once - the limit is {MAX_TOTAL_DICE}")
    return RollResult(expression=raw, terms=terms, advantage=advantage)


def _roll_die(sides: int) -> int:
    return random.randint(1, sides)


def _apply_keep(term: Term) -> None:
    """Drop every die beyond ``keep_n``, best (kh) or worst (kl) first."""
    if not term.keep or len(term.dice) <= term.keep_n:
        return
    ordered = sorted(
        range(len(term.dice)),
        key=lambda i: term.dice[i].value,
        reverse=term.keep == "kh",
    )
    for i in ordered[term.keep_n :]:
        term.dice[i].kept = False


def _roll_term(term: Term) -> None:
    """Roll one term, applying rerolls and explosions before keep/drop."""
    dice: list[Die] = []
    # (count, note) - an explosion pushes one more die onto the back.
    queue: list[tuple[int, str]] = [(term.count, "")]
    rolled = 0

    while queue and rolled < MAX_DIE_ROLLS:
        count, note = queue.pop(0)
        for _ in range(count):
            value = _roll_die(term.sides)
            rolled += 1

            # Reroll until the die clears the threshold (a rerolled 1 is
            # rerolled again, per the SRD).
            spins = 0
            while term.reroll_below and value <= term.reroll_below:
                spins += 1
                if spins > MAX_REROLLS or rolled >= MAX_DIE_ROLLS:
                    break
                value = _roll_die(term.sides)
                rolled += 1
            die = Die(
                sides=term.sides,
                value=value,
                note=f"r<={term.reroll_below}" if spins else note,
            )
            dice.append(die)

            if term.explodes and value >= term.explode_at:
                die.note = f"explode>={term.explode_at}"
                queue.append((1, note))

    term.dice = dice
    _apply_keep(term)


def roll(expression: str) -> RollResult:
    """Parse and roll an expression, returning the full breakdown."""
    result = parse(expression)
    for term in result.terms:
        _roll_term(term)
    return result


