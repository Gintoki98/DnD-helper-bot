"""End-to-end exercise of the MCP server's tools, without a transport.

    python tests/test_mcp.py

``build_server()`` yields the real registry; every tool is then called as a
plain function - the same object a ``tools/call`` reaches - against a
throwaway database. Environment is set before importing dndbot (config reads
it at import time) and both sessions close in ``finally``, because
aiosqlite's worker thread otherwise hangs the interpreter.

What it protects: the 34-tool registry and its descriptions, identity, dice,
help, the SRD path (needs the API or a warm ``.cache/``), characters,
campaigns, sessions, and the two contracts every tool owes the model -
domain failures arrive as ``ToolError`` in plain text, successes arrive as
Telegram-free Markdown.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import re
import sys
import tempfile

TMP_DB = os.path.join(tempfile.mkdtemp(prefix="dndbot-mcp-"), "mcp.db")
os.environ["DB_PATH"] = TMP_DB
# Keep test logs out of the real logs/ directory.
os.environ["LOG_DIR"] = os.path.join(tempfile.mkdtemp(prefix="dndbot-mcplog-"), "logs")
# Deterministic identity: whatever .env says, this run has no default actor
# and a fixed admin, so both branches below can be asserted.
os.environ["MCP_USER_ID"] = ""
os.environ["ADMIN_ID"] = "1001"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

from dndbot.mcp import build_server  # noqa: E402
from dndbot.mcp.campaign import (  # noqa: E402
    campaign_info,
    checkin,
    create_campaign,
    end_session,
    get_roster,
    join_campaign,
    leave_campaign,
    list_campaigns,
    list_join_requests,
    resolve_join,
    select_campaign,
    session_history,
    start_session,
    who_is_here,
)
from dndbot.mcp.character import (  # noqa: E402
    add_note,
    change_hp,
    create_character,
    get_character,
    level_up,
    list_party,
    set_character_field,
    set_hp,
    set_xp,
    switch_character,
)
from dndbot.mcp.dice import party_initiative, roll  # noqa: E402
from dndbot.mcp.meta import get_help, get_tutorial, recent_errors, whoami  # noqa: E402
from dndbot.mcp.srd import srd_list, srd_lookup, srd_random, srd_search  # noqa: E402
from dndbot.srd import srd  # noqa: E402
from dndbot.storage import db  # noqa: E402

# The same tag set test_flow.py polices: a tool result must be Markdown.
TAG = re.compile(r"</?(b|i|u|s|code|pre|a|tg-spoiler)(\s[^>]*)?>")
PASS, FAIL = [], []

EXPECTED_TOOLS = {
    # dice
    "roll", "party_initiative",
    # srd
    "srd_lookup", "srd_search", "srd_random", "srd_list",
    # meta
    "whoami", "get_help", "get_tutorial", "recent_errors",
    # characters
    "create_character", "get_character", "list_party", "change_hp", "set_hp",
    "level_up", "set_xp", "set_character_field", "add_note", "switch_character",
    # campaigns and sessions
    "list_campaigns", "create_campaign", "join_campaign", "list_join_requests",
    "resolve_join", "select_campaign", "get_roster", "campaign_info",
    "leave_campaign", "start_session", "end_session", "session_history",
    "checkin", "who_is_here",
}

DM, PLAYER, GUEST = 1001, 2002, 3003
CAMPAIGN = "MCP Test"
SECOND = "MCP Second"


def check(label: str, condition: bool, detail: str = "") -> None:
    (PASS if condition else FAIL).append(label if condition else f"{label}: {detail}")


def expect_contains(label: str, text: str, *needles: str) -> None:
    missing = [needle for needle in needles if needle not in text]
    check(label, not missing, f"missing {missing!r} in {text[:220]!r}")


async def call(label: str, factory) -> str:
    """Run a tool and assert the output contract on its way out.

    ``factory`` is a zero-arg callable: some tools are sync (roll, help) and
    most are async, and both must answer with Markdown, not Telegram HTML.
    """
    result = factory()
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, str):
        FAIL.append(f"{label}: returned {type(result).__name__}, expected str")
        return str(result)
    tags = TAG.findall(result)
    check(label + ": Telegram-free Markdown", not tags, f"{tags[:3]} left in the output")
    return result


async def expect_error(label: str, factory, needle: str = "") -> None:
    """A domain failure must arrive as ToolError the model can read."""
    try:
        result = factory()
        if inspect.isawaitable(result):
            await result
    except ToolError as exc:
        message = str(exc)
        tags = TAG.findall(message)
        ok = bool(message.strip()) and not tags
        if needle:
            ok = ok and needle.lower() in message.lower()
        check(label, ok, f"message {message[:220]!r} (needle {needle!r}, tags {tags[:3]})")
    except Exception as exc:  # noqa: BLE001 - any other type is the bug
        FAIL.append(f"{label}: raised {type(exc).__name__}: {exc}")
    else:
        FAIL.append(f"{label}: should have raised ToolError")


async def test_registry() -> None:
    """The server exposes exactly the v1 inventory, documented for a model."""
    tools = await build_server().list_tools()
    names = {tool.name for tool in tools}
    check(
        "registry: 34 tools",
        names == EXPECTED_TOOLS,
        f"missing {sorted(EXPECTED_TOOLS - names)} extra {sorted(names - EXPECTED_TOOLS)}",
    )
    for tool in tools:
        description = tool.description or ""
        check(f"{tool.name}: documented", bool(description.strip()), "empty description")
        check(f"{tool.name}: names its command", "Telegram" in description, description[:90])
        indented = [line for line in description.splitlines() if line.startswith((" ", "\t"))]
        check(f"{tool.name}: cleandoc applied", not indented, f"indented line {indented[:1]!r}")
        check(f"{tool.name}: has a schema", bool(getattr(tool, "input_schema", None)), "no schema")


async def test_identity() -> None:
    """No default actor, and every tool says so when one is missing."""
    text = await call("whoami", whoami)
    expect_contains("whoami: reports no identity", text, "No identity configured")
    text = await call("whoami", lambda: whoami(user_id=DM))
    expect_contains("whoami: explicit id", text, f"id `{DM}`", "never spoken")
    await expect_error("list_campaigns: needs an id", list_campaigns, "Telegram user id")
    await expect_error("user_id: must be positive", lambda: list_campaigns(user_id=0), "Telegram id")


async def test_dice_and_help() -> None:
    """Pure tools: results as Markdown, bad input as ToolError."""
    text = await call("roll", lambda: roll("2d20"))
    expect_contains("roll: total and notation", text, "🎲 **", "`2d20`")
    await expect_error("roll: rejects nonsense", lambda: roll("nope"))

    text = await call("get_help", get_help)
    check("get_help: overview", len(text) > 200, f"{len(text)} chars")
    await expect_error("get_help: unknown topic", lambda: get_help("nope"), "No help topic")
    text = await call("get_tutorial", lambda: get_tutorial("start"))
    check("get_tutorial: first page", len(text) > 200, f"{len(text)} chars")
    await expect_error("get_tutorial: unknown page", lambda: get_tutorial("nope"), "No tutorial page")

    await expect_error("recent_errors: admin-only", lambda: recent_errors(user_id=PLAYER), "admin-only")
    text = await call("recent_errors", lambda: recent_errors(user_id=DM))
    check("recent_errors: admin reads it", isinstance(text, str) and "Log" in text, text[:120])


async def test_srd() -> None:
    """The read-only SRD tools share the bot's cache and formatter."""
    text = await call("srd_search", lambda: srd_search("dragon"))
    expect_contains("srd_search: hits", text, "Results for")
    await expect_error("srd_search: blank query", lambda: srd_search("   "))
    await expect_error("srd_search: no match", lambda: srd_search("qqqzzz"), "Nothing in the SRD")
    text = await call("srd_lookup", lambda: srd_lookup("spell", "fireball"))
    expect_contains("srd_lookup: fireball", text, "Fireball")
    await expect_error("srd_lookup: unknown category", lambda: srd_lookup("banana", "x"), "do not know a category")
    text = await call("srd_list", lambda: srd_list("spells", 1))
    check("srd_list: a page of spells", len(text) > 100, f"{len(text)} chars")
    text = await call("srd_random", lambda: srd_random("spell"))
    check("srd_random: an entry", bool(text.strip()), "empty result")


async def test_characters() -> None:
    """Fase B: one-call creation and every sheet command's arithmetic."""
    text = await call(
        "create_campaign",
        lambda: create_campaign(CAMPAIGN, "A haunted road", user_id=DM),
    )
    expect_contains("create_campaign: code and guidance", text, "Invite code", "join_campaign")
    row = await db.lookup_campaign(CAMPAIGN)
    check("create_campaign: stored", row is not None, "no row")
    assert row is not None
    await db.add_member(row["id"], PLAYER)

    text = await call(
        "create_character",
        lambda: create_character(
            name="Aldric", class_name="Fighter", race="Human",
            level=3, max_hp=30, ac=16, dexterity=14, user_id=DM, campaign=CAMPAIGN,
        ),
    )
    expect_contains("create_character: ready", text, "Aldric", "is ready")
    await expect_error(
        "create_character: active sheet blocks",
        lambda: create_character(name="Brynn", user_id=DM, campaign=CAMPAIGN),
        "force",
    )

    text = await call("get_character", lambda: get_character(user_id=DM, campaign=CAMPAIGN))
    expect_contains("get_character: the sheet", text, "Aldric", "Recent", "`30/30`")

    text = await call("change_hp", lambda: change_hp(-7, user_id=DM, campaign=CAMPAIGN))
    expect_contains("change_hp: damage", text, "Aldric", "`23/30`")
    text = await call("get_character", lambda: get_character(user_id=DM, campaign=CAMPAIGN))
    expect_contains("change_hp: persisted", text, "`23/30`")

    text = await call("set_hp", lambda: set_hp(20, user_id=DM, campaign=CAMPAIGN))
    expect_contains("set_hp: both numbers", text, "`20/20`")
    text = await call("set_hp", lambda: set_hp(15, user_id=DM, campaign=CAMPAIGN))
    expect_contains("set_hp: max defaults to hp", text, "`15/15`")

    text = await call("level_up", lambda: level_up(user_id=DM, campaign=CAMPAIGN))
    expect_contains("level_up: 3 to 4", text, "level **4**")
    await expect_error("level_up: cannot go down", lambda: level_up(level=1, user_id=DM, campaign=CAMPAIGN))
    await expect_error("level_up: out of range", lambda: level_up(level=99, user_id=DM, campaign=CAMPAIGN))

    text = await call("set_xp", lambda: set_xp(3200, user_id=DM, campaign=CAMPAIGN))
    expect_contains("set_xp: formatted", text, "3,200")
    text = await call(
        "set_character_field", lambda: set_character_field("ac", 17, user_id=DM, campaign=CAMPAIGN)
    )
    expect_contains("set_character_field: ac", text, "ac set to **17**")
    text = await call(
        "set_character_field", lambda: set_character_field("dex", 18, user_id=DM, campaign=CAMPAIGN)
    )
    expect_contains("set_character_field: modifier", text, "Modifier +4")
    await expect_error(
        "set_character_field: unknown field",
        lambda: set_character_field("hp", 5, user_id=DM, campaign=CAMPAIGN),
        "I do not track",
    )
    text = await call("add_note", lambda: add_note("Keeps the map.", user_id=DM, campaign=CAMPAIGN))
    expect_contains("add_note: saved", text, "Note saved", "Aldric")

    text = await call(
        "create_character",
        lambda: create_character(name="Brynn", class_name="Rogue", level=2,
                                 max_hp=22, force=True, user_id=DM, campaign=CAMPAIGN),
    )
    expect_contains("create_character: force switches", text, "Brynn", "is ready")
    text = await call("switch_character", lambda: switch_character(user_id=DM, campaign=CAMPAIGN))
    expect_contains("switch_character: pick list", text, "Aldric", "Brynn")
    text = await call("get_character", lambda: get_character(user_id=DM, campaign=CAMPAIGN))
    expect_contains("create_character: the new sheet is active", text, "Brynn")
    text = await call(
        "switch_character", lambda: switch_character(name="Aldric", user_id=DM, campaign=CAMPAIGN)
    )
    expect_contains("switch_character: back to Aldric", text, "is now your active character")
    await expect_error(
        "switch_character: unknown sheet",
        lambda: switch_character(name="Nope", user_id=DM, campaign=CAMPAIGN),
        "no character called",
    )

    text = await call(
        "create_character",
        lambda: create_character(name="Corvin", class_name="Wizard", level=1,
                                 max_hp=7, user_id=PLAYER, campaign=CAMPAIGN),
    )
    expect_contains("create_character: second player", text, "Corvin", "is ready")
    text = await call(
        "get_character", lambda: get_character(name="Aldric", user_id=PLAYER, campaign=CAMPAIGN)
    )
    expect_contains("get_character: someone else's sheet", text, "Aldric")
    check("get_character: no history for others", "Recent" not in text, "history leaked")

    # PLAYER has a single campaign, so the only-one fallback resolves it.
    text = await call("list_party", lambda: list_party(user_id=PLAYER))
    expect_contains("list_party: everyone active", text, "Aldric", "Corvin", "Party total HP")
    text = await call("party_initiative", lambda: party_initiative(user_id=DM, campaign=CAMPAIGN))
    expect_contains("party_initiative: ordered", text, "Initiative for", "Aldric", "Corvin")

    await expect_error(
        "list_party: outsider", lambda: list_party(user_id=GUEST, campaign=CAMPAIGN), "not a member"
    )
    await expect_error(
        "get_character: outsider", lambda: get_character(user_id=GUEST, campaign=CAMPAIGN), "not a member"
    )
    await expect_error(
        "create_character: outsider",
        lambda: create_character(name="Ghost", user_id=GUEST, campaign=CAMPAIGN),
        "not a member",
    )


async def test_campaigns() -> None:
    """Fase C: the DM's panel, the join queue and the session lifecycle."""
    text = await call("list_campaigns", lambda: list_campaigns(user_id=DM))
    expect_contains("list_campaigns: mine", text, CAMPAIGN, "DM")
    await expect_error(
        "list_campaigns: outsider",
        lambda: list_campaigns(user_id=9999),
        "create_campaign",
    )
    text = await call("create_campaign", lambda: create_campaign(SECOND, user_id=DM))
    expect_contains("create_campaign: second", text, SECOND, "join_campaign")
    await expect_error(
        "create_campaign: duplicate", lambda: create_campaign(SECOND, user_id=DM), "already have"
    )

    row = await db.lookup_campaign(CAMPAIGN)
    assert row is not None
    code = row["invite_code"]

    await expect_error("join_campaign: no code", lambda: join_campaign(user_id=GUEST), "invite code")
    text = await call("join_campaign", lambda: join_campaign(code=code, user_id=GUEST))
    expect_contains("join_campaign: requested", text, "Requested to join", CAMPAIGN)
    await expect_error(
        "join_campaign: unknown code", lambda: join_campaign(code="ZZZZZZ", user_id=PLAYER), "No campaign matches"
    )
    text = await call("join_campaign", lambda: join_campaign(code=code, user_id=PLAYER))
    expect_contains("join_campaign: already in", text, "already in")

    await expect_error(
        "list_join_requests: player",
        lambda: list_join_requests(user_id=PLAYER, campaign=CAMPAIGN),
        "Only the DM",
    )
    text = await call(
        "list_join_requests", lambda: list_join_requests(user_id=DM, campaign=CAMPAIGN)
    )
    expect_contains("list_join_requests: ids to act on", text, "resolve_join", "#")

    pending = await db.pending_requests(row["id"])
    check("join queue: one request", len(pending) == 1, f"{len(pending)} requests")
    request_id = pending[0]["id"]
    expect_contains(
        "list_join_requests: the real id",
        text,
        f"#{request_id}",
    )
    await expect_error(
        "resolve_join: player cannot decide",
        lambda: resolve_join(request_id, approve=True, user_id=PLAYER),
        "Only the DM",
    )
    await expect_error(
        "resolve_join: unknown request",
        lambda: resolve_join(999999, approve=True, user_id=DM),
    )
    text = await call(
        "resolve_join", lambda: resolve_join(request_id, approve=True, user_id=DM)
    )
    expect_contains("resolve_join: approved", text, "joined", CAMPAIGN)
    check("resolve_join: applicant is a member", await db.is_member(row["id"], GUEST), "not added")
    await expect_error(
        "resolve_join: same id twice",
        lambda: resolve_join(request_id, approve=True, user_id=DM),
        "already been handled",
    )
    text = await call(
        "list_join_requests", lambda: list_join_requests(user_id=DM, campaign=CAMPAIGN)
    )
    expect_contains("list_join_requests: queue empty", text, "No one is waiting")

    text = await call("get_roster", lambda: get_roster(user_id=DM, campaign=CAMPAIGN))
    expect_contains("get_roster: with sheets", text, "Aldric", "Corvin")
    text = await call("campaign_info", lambda: campaign_info(user_id=DM, campaign=CAMPAIGN))
    expect_contains("campaign_info: the DM's view", text, "Dungeon Master", "Invite code")
    text = await call("campaign_info", lambda: campaign_info(user_id=PLAYER, campaign=CAMPAIGN))
    expect_contains("campaign_info: a player's view", text, "Player")

    text = await call("select_campaign", lambda: select_campaign(user_id=DM))
    expect_contains("select_campaign: pick list", text, CAMPAIGN, SECOND, "select_campaign")
    text = await call("select_campaign", lambda: select_campaign(SECOND, user_id=DM))
    expect_contains("select_campaign: switched", text, "Working on")
    await expect_error(
        "select_campaign: unknown name", lambda: select_campaign("Nope", user_id=DM), "No campaign called"
    )


async def test_sessions() -> None:
    """Opening, sitting at, and closing a session - with the player's words."""
    row = await db.lookup_campaign(CAMPAIGN)
    assert row is not None

    await expect_error(
        "start_session: only the DM",
        lambda: start_session(title="Marsh", user_id=PLAYER, campaign=CAMPAIGN),
        "Only the DM",
    )
    text = await call(
        "start_session", lambda: start_session(title="Marsh", user_id=DM, campaign=CAMPAIGN)
    )
    expect_contains("start_session: opened", text, "Session started", "Players tracked", "Marsh")
    await expect_error(
        "start_session: one at a time",
        lambda: start_session(user_id=DM, campaign=CAMPAIGN),
        "already running",
    )

    text = await call("checkin", lambda: checkin(user_id=PLAYER, campaign=CAMPAIGN))
    expect_contains("checkin: seated", text, "Checked in")
    text = await call("who_is_here", lambda: who_is_here(user_id=PLAYER, campaign=CAMPAIGN))
    expect_contains("who_is_here: at the table", text, "At the table")
    text = await call("session_history", lambda: session_history(user_id=DM, campaign=CAMPAIGN))
    expect_contains("session_history: live", text, "live", "Marsh")

    await expect_error(
        "end_session: only the DM",
        lambda: end_session(user_id=PLAYER, campaign=CAMPAIGN),
        "Only the DM",
    )
    text = await call(
        "end_session",
        lambda: end_session(notes="The dragon fled.", user_id=DM, campaign=CAMPAIGN),
    )
    expect_contains("end_session: closed", text, "ended after", "dragon fled")
    await expect_error(
        "end_session: nothing running",
        lambda: end_session(user_id=DM, campaign=CAMPAIGN),
        "No session is running",
    )
    await expect_error(
        "checkin: after the close",
        lambda: checkin(user_id=PLAYER, campaign=CAMPAIGN),
        "No session is running",
    )
    text = await call("who_is_here", lambda: who_is_here(user_id=PLAYER, campaign=CAMPAIGN))
    expect_contains("who_is_here: falls back to roster", text, "roster")
    text = await call("session_history", lambda: session_history(user_id=DM, campaign=CAMPAIGN))
    expect_contains("session_history: ended", text, "ended", f"#{row['id']}")

    text = await call("leave_campaign", lambda: leave_campaign(user_id=PLAYER, campaign=CAMPAIGN))
    expect_contains("leave_campaign: gone", text, "You left", "characters stay saved")
    check(
        "leave_campaign: membership removed",
        not await db.is_member(row["id"], PLAYER),
        "still a member",
    )
    await expect_error(
        "leave_campaign: the DM stays",
        lambda: leave_campaign(user_id=DM, campaign=CAMPAIGN),
        "cannot leave",
    )
    text = await call("get_roster", lambda: get_roster(user_id=DM, campaign=CAMPAIGN))
    expect_contains("get_roster: after the leave", text, "Aldric")
    check("get_roster: departed sheet is gone", "Corvin" not in text, "Corvin still listed")


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


async def main() -> int:
    await db.connect()
    await srd.start()
    try:
        await test_registry()
        await test_identity()
        await test_dice_and_help()
        await test_srd()
        await test_characters()
        await test_campaigns()
        await test_sessions()
    finally:
        # aiosqlite runs a worker thread; leaving it open hangs the interpreter.
        await srd.close()
        await db.close()
    return report()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
