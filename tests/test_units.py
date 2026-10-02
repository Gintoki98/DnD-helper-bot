"""Unit tests for the pure logic: dice parsing, paging and HTML escaping.

    python tests/test_units.py
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dndbot import dice  # noqa: E402
from dndbot.formatting import desc_text, esc, names_of, split_pages  # noqa: E402
from dndbot.keyboards import dice_roll_summary  # noqa: E402
from dndbot.srd import resolve_category, slugify  # noqa: E402
from dndbot.storage import ability_modifier, new_invite_code  # noqa: E402

PASS, FAIL = [], []


def check(label: str, condition: bool, detail: str = "") -> None:
    (PASS if condition else FAIL).append(label if condition else f"{label}: {detail}")


def expect_error(label: str, expression: str) -> None:
    try:
        dice.roll(expression)
    except dice.DiceError:
        PASS.append(label)
        return
    except Exception as exc:
        FAIL.append(f"{label}: raised {type(exc).__name__} instead of DiceError")
        return
    FAIL.append(f"{label}: {expression!r} should have been rejected")


def test_dice_ranges() -> None:
    """Every expression's total must stay inside its theoretical bounds."""
    cases = [
        ("d20", 1, 20),
        ("2d6+3", 5, 15),
        ("1d4+1d4", 2, 8),
        ("4d6kh3", 3, 18),
        ("4d6kl3", 3, 18),
        ("2d20kl1", 1, 20),
        ("2d20kh1", 1, 20),
        ("10d6-5", 5, 55),
        ("1d100", 1, 100),
        ("2d6!>=6", 2, None),  # exploding: unbounded above
        ("3d8r<=1", 3, 24),
    ]
    for expression, low, high in cases:
        for _ in range(60):
            result = dice.roll(expression)
            if result.total < low:
                FAIL.append(f"{expression}: total {result.total} below minimum {low}")
                break
            if high is not None and result.total > high:
                FAIL.append(f"{expression}: total {result.total} above maximum {high}")
                break
        else:
            PASS.append(f"{expression} stays in [{low}, {high or 'inf'}]")


def test_keep_semantics() -> None:
    """Keep-highest must always beat keep-lowest on the same rolls."""
    higher = lower = 0
    for _ in range(200):
        high = dice.roll("4d6kh3")
        low = dice.roll("4d6kl3")
        higher += high.total >= low.total
        lower += high.total == low.total
    # equality happens ~1/6 of the time with identical dice, so both dominate.
    check("kh3 dominates kl3", higher > 150, f"only {higher}/200")
    check("kh3 ties sometimes", lower < 60, f"tied {lower}/200")


def test_dropped_marked() -> None:
    result = dice.roll("4d6kh3")
    kept = sum(1 for d in result.all_dice if d.kept)
    check("kh3 keeps exactly 3 dice", kept == 3, f"kept {kept}")
    check("kh3 drops one die", len(result.dropped) == 1, f"dropped {len(result.dropped)}")
    check("kept dice carry the highest values",
          all(d.kept for d in sorted(result.all_dice, key=lambda x: x.value, reverse=True)[:3]),
          "highest three were not all kept")


def test_advantage() -> None:
    adv = dice.roll("adv")
    dis = dice.roll("dis")
    check("adv is two d20 keep highest",
          adv.advantage == "adv" and adv.terms[0].label == "2d20kh1", adv.terms[0].label)
    check("dis is two d20 keep lowest",
          dis.advantage == "dis" and dis.terms[0].label == "2d20kl1", dis.terms[0].label)
    check("adv total within d20 range", 1 <= adv.total <= 20, str(adv.total))
    check("dis total within d20 range", 1 <= dis.total <= 20, str(dis.total))
    # advantage should average higher than disadvantage over many rolls
    a = sum(dice.roll("adv").total for _ in range(2000))
    d = sum(dice.roll("dis").total for _ in range(2000))
    check("advantage averages above disadvantage", a > d + 1000, f"adv={a} dis={d}")


def test_exploding() -> None:
    saw_explosion = False
    for _ in range(300):
        result = dice.roll("3d6!>=6")
        if any("explode" in die.note for die in result.all_dice):
            saw_explosion = True
            break
    check("exploding dice can explode", saw_explosion, "never saw an explosion in 300 rolls")
    bounded = dice.roll("1d6!>=6")
    check("non-exploding result stays in range", 1 <= bounded.total <= 12, str(bounded.total))


def test_rerolls() -> None:
    result = dice.roll("20d6r<=1")
    ones = [d for d in result.all_dice if d.value == 1 and "r<=" in d.note]
    check("reroll<=1 leaves no rerolled 1s", not ones, f"{len(ones)} ones survived")


def test_natural_detection() -> None:
    check("natural only for lone d20",
          dice.roll("1d20+5").natural is not None and dice.roll("2d20").natural is None)


def test_errors() -> None:
    for bad in ("", "   ", "abc", "d1", "d0", "0d6", "1d20+", "2d6-", "+",
                "2d6kh9", "999d6", "2d6xx", "d20+", "2d6r>=3"):
        expect_error(f"reject {bad!r}", bad)


def test_flat_labels() -> None:
    result = dice.roll("2d6+3-1")
    labels = [t.label for t in result.terms]
    check("flat terms render as numbers, not 0d0", "0d0" not in " ".join(labels), str(labels))
    check("flat term keeps its sign", "-1" in labels, str(labels))


def test_summary() -> None:
    result = dice.roll("2d6+3")
    summary = dice_roll_summary(result)
    check("summary contains the total", str(result.total) in summary, summary)
    check("summary has no stray plus", not summary.startswith("+"), summary)


def test_esc() -> None:
    check("escapes angle brackets", esc("<b>") == "&lt;b&gt;", esc("<b>"))
    check("escapes ampersand", esc("a & b") == "a &amp; b", esc("a & b"))
    check("converts bold markers", "<b>hi</b>" in esc("**hi**"), esc("**hi**"))
    check("converts italic markers", "<i>hi</i>" in esc("*hi*"), esc("*hi*"))
    check("leaves ordinary text alone", esc("Goblin") == "Goblin", esc("Goblin"))


def test_desc_text() -> None:
    check("string desc", desc_text("a b") == "a b", desc_text("a b"))
    check("list desc joined", desc_text(["a", "b"]) == "a\n\nb", repr(desc_text(["a", "b"])))
    check(
        "dict desc",
        desc_text([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]) == "a\n\nb",
        repr(desc_text([{"type": "text", "text": "a"}])),
    )
    check("nested desc", desc_text({"desc": "deep"}) == "deep", desc_text({"desc": "deep"}))
    check("None desc", desc_text(None) == "", repr(desc_text(None)))


def test_names_of() -> None:
    check(
        "list of name refs",
        names_of([{"name": "Leather Armor"}, {"name": "Shield"}]) == "Leather Armor, Shield",
        names_of([{"name": "Leather Armor"}]),
    )
    check("dict name ref", names_of({"name": {"name": "Fire"}}) == "Fire",
          names_of({"name": {"name": "Fire"}}))
    check("plain string", names_of("Common, Goblin") == "Common, Goblin")


def test_split_pages() -> None:
    long_text = "\n\n".join("word " * 30 for _ in range(400))
    pages = split_pages(long_text, 1000)
    check("split produces several pages", len(pages) > 1, str(len(pages)))
    check("no page exceeds the limit", all(len(p) <= 1000 for p in pages),
          str(max(len(p) for p in pages)))
    check("short text stays on one page", split_pages("tiny", 1000) == ["tiny"])
    check("empty stays on one page", split_pages("", 1000) == [""])

    # Whitespace-insensitive: the point is that no *content* is dropped.
    squeeze = lambda text: re.sub(r"\s+", "", text)
    check(
        "no words lost while splitting",
        squeeze("".join(pages)) == squeeze(long_text),
        f"{len(squeeze(''.join(pages)))} vs {len(squeeze(long_text))} chars",
    )


def test_categories() -> None:
    for alias in ("mob", "spell", "item", "rule", "class", "condition", "bestiary", "loot"):
        category = resolve_category(alias)
        check(f"alias {alias!r} resolves", category is not None, str(category))
    check("unknown category is None", resolve_category("nonsense") is None)
    check("broken upstream categories dropped",
          resolve_category("feats") is None and resolve_category("backgrounds") is None)
    check("slugify", slugify("Adult Red Dragon!") == "adult-red-dragon", slugify("Adult Red Dragon!"))
    check("slugify apostrophe", slugify("Alchemist's Fire") == "alchemists-fire",
          slugify("Alchemist's Fire"))


def test_helpers() -> None:
    check("ability modifier", ability_modifier(10) == 0 and ability_modifier(20) == 5,
          f"{ability_modifier(10)}/{ability_modifier(20)}")
    check("ability modifier low", ability_modifier(1) == -5, str(ability_modifier(1)))
    check("invite code length", len(new_invite_code()) == 6, new_invite_code())
    check("invite code avoids confusables",
          not (set("01O") & set(new_invite_code(200))), "saw a confusable character")


def test_display_name() -> None:
    """display_name must handle every shape a name arrives in.

    sqlite3.Row supports key access but not attribute access, which once made
    every roster and damage-log entry read "Someone".
    """
    from types import SimpleNamespace

    import sqlite3

    from dndbot.common import display_name, plain_name

    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row  # aiosqlite does this for us
    row = connection.execute(
        "SELECT 'Sylra' AS first_name, 'Vane' AS last_name, 'sylva' AS username"
    ).fetchone()
    check("sqlite3.Row has no attribute access", not hasattr(row, "first_name"),
          "fixture is not a Row")
    check("sqlite3.Row gives a name", "Sylra" in display_name(row), display_name(row))
    check("sqlite3.Row username shown", "@sylva" in display_name(row), display_name(row))
    check("plain_name strips markup", "<code>" not in plain_name(row), plain_name(row))

    entity = SimpleNamespace(first_name="Ogre", last_name=None, username="ogreboss")
    check("Telethon entity works", display_name(entity).startswith("Ogre"), display_name(entity))

    mapping = {"first_name": "A", "last_name": "B", "username": "ab"}
    check("dict works", "A B" in display_name(mapping), display_name(mapping))

    only_username = {"first_name": None, "last_name": None, "username": "zed"}
    check("username-only fallback", "@zed" in display_name(only_username),
          display_name(only_username))

    check("None is Someone", display_name(None) == "Someone", display_name(None))
    check("plain string passes through", display_name("Sylra") == "Sylra", "changed")

    hostile = {"first_name": "<b>Evil</b> & co", "username": "evil"}
    rendered = display_name(hostile)
    check("names are html-escaped", "<b>" not in rendered and "&lt;b&gt;" in rendered, rendered)


def report() -> int:
    print(f"\n{'=' * 60}")
    print(f"checks passed: {len(PASS)}")
    print(f"failures:      {len(FAIL)}")
    if FAIL:
        print(f"{'-' * 60}")
        for failure in FAIL:
            print("FAIL", failure)
    else:
        print("ALL GREEN")
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    test_dice_ranges()
    test_keep_semantics()
    test_dropped_marked()
    test_advantage()
    test_exploding()
    test_rerolls()
    test_natural_detection()
    test_errors()
    test_flat_labels()
    test_summary()
    test_esc()
    test_desc_text()
    test_names_of()
    test_split_pages()
    test_categories()
    test_helpers()
    test_display_name()
    sys.exit(report())
