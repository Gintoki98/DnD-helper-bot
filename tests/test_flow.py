"""End-to-end exercise of the bot's handlers with fake Telegram events.

No network and no Telegram account: handlers are pulled off a real
TelegramClient and invoked with a stub event that records what the bot would
have sent.  Every reply is checked for balanced Telegram HTML.

    python tests/test_flow.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import tempfile
from types import SimpleNamespace

TMP_DB = os.path.join(tempfile.mkdtemp(prefix="dndbot-test-"), "test.db")
os.environ["DB_PATH"] = TMP_DB
# Keep test logs out of the real logs/ directory.
os.environ["LOG_DIR"] = os.path.join(tempfile.mkdtemp(prefix="dndbot-log-"), "logs")
os.environ.setdefault("API_ID", "11111")
os.environ.setdefault("API_HASH", "0" * 32)
os.environ.setdefault("BOT_TOKEN", "0:test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telethon import TelegramClient  # noqa: E402

from dndbot import __main__ as botmain  # noqa: E402
from dndbot.handlers import character as character_mod  # noqa: E402
from dndbot.keyboards import entry_from_token, entry_token  # noqa: E402
from dndbot.logs import recent_errors, setup_logging  # noqa: E402
from dndbot.srd import srd  # noqa: E402
from dndbot.storage import db  # noqa: E402

TAG = re.compile(r"</?(b|i|u|s|code|pre|a|tg-spoiler)(\s[^>]*)?>")
PASS, FAIL = [], []
# Shared so the callback helpers can reach the registered handler list.
ENTRIES: list = []


class ErrorCapture(logging.Handler):
    """Records ERROR+ log records.

    The bot's handler guard logs exceptions and replies politely instead of
    letting them escape, so a crash looks like a normal reply. Watching the
    log is the only way a test can tell the two apart.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def take(self) -> list[logging.LogRecord]:
        found, self.records = self.records, []
        return found


ERRORS = ErrorCapture()
logging.getLogger().addHandler(ERRORS)
logging.getLogger().setLevel(logging.WARNING)


def check_html(label: str, text: str) -> None:
    if not isinstance(text, str):
        FAIL.append(f"{label}: reply is not a string ({type(text)})")
        return
    stack = []
    for match in TAG.finditer(text):
        tag = match.group(1)
        if match.group(0).startswith("</"):
            if not stack or stack[-1] != tag:
                FAIL.append(f"{label}: stray </{tag}>")
                return
            stack.pop()
        else:
            stack.append(tag)
    if stack:
        FAIL.append(f"{label}: unclosed {stack}")
        return
    if re.search(r"&(?!amp;|lt;|gt;|quot;|#39;)", text):
        FAIL.append(f"{label}: unescaped '&' in {text[:90]!r}")
        return
    if len(text) > 4096:
        FAIL.append(f"{label}: {len(text)} chars exceeds the 4096 limit")
        return
    PASS.append(label)


class FakeEvent:
    """Stands in for a Telethon NewMessage event."""

    def __init__(self, client, sender_id: int, text: str, chat_id: int = 1):
        self.client = client
        self.sender_id = sender_id
        self.raw_text = text
        self.chat_id = chat_id
        self.is_private = True
        self.is_group = False
        self.is_reply = False
        self.out = False
        self.name = f"user{sender_id}"
        self.sender = SimpleNamespace(
            id=sender_id, username=self.name, first_name=f"P{sender_id}", last_name=None
        )
        self.replies: list[dict] = []
        self.chat = SimpleNamespace(id=chat_id, title=None)

    async def reply(self, text=None, *args, **kwargs):
        self.replies.append({"text": text, "buttons": kwargs.get("buttons")})
        return SimpleNamespace(edit=lambda **kw: None)

    async def get_chat_id(self):
        return self.chat_id

    async def get_sender(self):
        return self.sender


class FakeClient:
    """Records outbound sends (DM notifications, broadcasts)."""

    def __init__(self, real):
        self.real = real
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, entity, message, **kwargs):
        self.sent.append((entity, message))
        return SimpleNamespace()

    def __getattr__(self, item):
        return getattr(self.real, item)


def collect(client):
    """Return [(kind, matcher, callback)] for every registered handler.

    Telethon stores a bound ``re.Pattern.match`` on ``NewMessage.pattern`` and
    a bytes matcher on ``CallbackQuery.match`` - neither is a compiled regex,
    so match through the bound method rather than ``re.search``.
    """
    entries = []
    for callback, builder in client.list_event_handlers():
        kind = type(builder).__name__
        if kind == "NewMessage":
            matcher = getattr(builder, "pattern", None)
            if matcher is None:
                continue
            entries.append(("message", matcher, callback))
        elif kind == "CallbackQuery":
            matcher = getattr(builder, "match", None)
            if matcher is None:
                continue
            entries.append(("callback", matcher, callback))
    return entries


def find_message_handler(entries, text):
    for kind, matcher, callback in entries:
        if kind != "message":
            continue
        try:
            if matcher(text):
                return callback
        except Exception:
            continue
    return None


def find_callback_handler(entries, data):
    raw = data.encode() if isinstance(data, str) else data
    for kind, matcher, callback in entries:
        if kind != "callback":
            continue
        try:
            if matcher(raw):
                return callback
        except Exception:
            continue
    return None


async def drive(entries, client, user_id, text, expect_reply=True, label=None,
                expect_error=False):
    """Send `text` as `user_id` and return the fake event holding the replies.

    When ``expect_reply`` is set (the default) a command that answers nothing
    is a failure. That catches handlers which forget to ``await`` their send,
    which otherwise fail silently against the real API.

    Any ERROR log record is a failure too, unless ``expect_error`` is set -
    the bot's guard swallows handler exceptions and replies politely, so a
    crash is otherwise indistinguishable from normal output.
    """
    event = FakeEvent(client, user_id, text)
    label = label or f"/{text.split()[0]} ({user_id})"
    # Mirror the bot's always-on sender tracking so names resolve in output
    # such as the encounter damage log.
    await db.upsert_user(
        user_id, username=event.sender.username, first_name=event.sender.first_name
    )
    handler = find_message_handler(entries, text)
    if handler is None:
        if expect_reply:
            FAIL.append(f"{label}: no handler matched {text!r}")
        return event
    try:
        await handler(event)
    except Exception as exc:  # pragma: no cover - surfaced as a failure
        import traceback

        FAIL.append(f"{label}: raised {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
        return event
    logged = ERRORS.take()
    for record in logged:
        detail = record.exc_info[1] if record.exc_info else record.getMessage()
        if expect_error:
            PASS.append(f"{label}: failed as expected ({type(detail).__name__})")
        else:
            FAIL.append(f"{label}: handler logged an error: {type(detail).__name__}: {detail}")
    if expect_error and not logged:
        FAIL.append(f"{label}: expected a failure but the handler succeeded")
    for reply in event.replies:
        check_html(label, reply["text"] or "")
    if expect_reply and not event.replies:
        FAIL.append(f"{label}: handler matched {text!r} but sent nothing")
    return event


async def main() -> int:
    real = TelegramClient("/tmp/test-flow", 11111, "0" * 32, connection_retries=0)
    botmain._install_safety_nets(real)
    botmain.register_handlers(real)
    client = FakeClient(real)
    ENTRIES[:] = collect(real)
    table = ENTRIES
    try:
        return await run(real, client, table)
    finally:
        # aiosqlite runs a worker thread; leaving it open hangs the interpreter.
        await srd.close()
        await db.close()
        try:
            real.session.close()
        except Exception:
            pass


async def run(real, client, table) -> int:
    await db.connect()
    await srd.start()
    # Write a real errors.log into the throwaway LOG_DIR so /errors has
    # something to read back.
    setup_logging()

    DM, PLAYER1, PLAYER2 = 1001, 2002, 3003

    # -- basics ---------------------------------------------------------
    await drive(table, client, DM, "/start")
    await drive(table, client, DM, "/help")
    for topic in ("dice", "campaign", "session", "character", "srd", "encounter",
                  "nonsense"):
        await drive(table, client, DM, f"/help {topic}")

    # -- tutorial --------------------------------------------------------
    await drive(table, client, PLAYER1, "/tutorial")
    for topic in ("setup", "join", "dice", "character", "srd", "groups", "start",
                  "encounters", "fight", "dm", "campaign", "nonsense", "999999999"):
        await drive(table, client, PLAYER1, f"/tutorial {topic}")
    for page in range(10):  # includes out-of-range pages on purpose
        await open_callback(client, real, f"tut:{page}", PLAYER1)

    # -- campaign creation ----------------------------------------------
    event = await drive(table, client, DM, "/newcampaign The Amber Court | A haunted road")
    match = re.search(r"<code>([A-Z0-9]{6})</code>", event.replies[0]["text"])
    if not match:
        FAIL.append("newcampaign: no invite code in the reply")
        return report()
    code = match.group(1)

    await drive(table, client, DM, "/newcampaign The Amber Court")  # duplicate name
    await drive(table, client, DM, "/campaigns")
    await drive(table, client, DM, "/campaign")

    # -- join + DM approval ---------------------------------------------
    await drive(table, client, PLAYER1, f"/join {code}")
    await drive(table, client, PLAYER2, f"/join {code}")
    await drive(table, client, PLAYER2, f"/join {code}")  # duplicate request
    await drive(table, client, PLAYER1, "/pending")  # player is not the DM
    pending_event = await drive(table, client, DM, "/pending")
    if not pending_event.replies or not pending_event.replies[0]["buttons"]:
        FAIL.append("pending: DM got no approve/deny buttons")

    # approve player 1 through the callback, deny player 2
    requests = await db.pending_requests((await db.campaigns_for_user(DM))[0]["id"])
    await resolve_join(client, real, requests[0]["id"], "y", DM)
    await resolve_join(client, real, requests[1]["id"], "n", DM)
    if not await db.is_member(requests[0]["campaign_id"], PLAYER1):
        FAIL.append("approve: player 1 did not become a member")
    if await db.is_member(requests[1]["campaign_id"], PLAYER2):
        FAIL.append("deny: player 2 became a member anyway")

    await drive(table, client, PLAYER1, "/whoami")
    await drive(table, client, PLAYER1, "/campaign")
    await drive(table, client, DM, "/roster")

    # -- character creation wizard --------------------------------------
    await drive(table, client, PLAYER1, "/newchar", label="/newchar setup")
    for step in ("Sylra Vane", "Rogue / Thief", "Half-elf / Urchin", "3",
                 "10 16 14 12 13 8", "16 40 30"):
        await drive(table, client, PLAYER1, step, label=f"newchar> {step}")
    campaign_id = requests[0]["campaign_id"]
    character = await db.active_character(campaign_id, PLAYER1)
    if not character:
        FAIL.append("newchar: wizard did not create a character")
        return report()
    if character["name"] != "Sylra Vane" or character["class_name"] != "Rogue":
        FAIL.append(f"newchar: wrong fields {dict(character)['name']}/{character['class_name']}")
    if character["race"] != "Half-elf" or character["background"] != "Urchin":
        FAIL.append(f"newchar: race/background wrong: {character['race']}/{character['background']}")
    if character["max_hp"] != 40 or character["ac"] != 16:
        FAIL.append(f"newchar: ac/hp wrong: {character['ac']}/{character['max_hp']}")
    if character["initiative"] != 3:
        FAIL.append(f"newchar: initiative should be dex mod 3, got {character['initiative']}")

    # -- character commands ---------------------------------------------
    await drive(table, client, PLAYER1, "/newchar")  # already has one
    await drive(table, client, PLAYER1, "/char")
    await drive(table, client, PLAYER1, "/char Sylra")
    await drive(table, client, PLAYER1, "/char Nobody")
    await drive(table, client, PLAYER1, "/hp -12 wolf bite")
    if (await db.get_character(character["id"]))["hp"] != 28:
        FAIL.append("hp: -12 did not apply")
    await drive(table, client, PLAYER1, "/hp -40 huge damage")
    downed = await db.get_character(character["id"])
    if downed["hp"] != 0:
        FAIL.append(f"hp: overkill should clamp to 0, got {downed['hp']}")
    await drive(table, client, PLAYER1, "/hp +15 rest")
    await drive(table, client, PLAYER1, "/hp +999 overheal")
    if (await db.get_character(character["id"]))["hp"] > 40:
        FAIL.append("hp: healing should cap at max HP")
    await drive(table, client, PLAYER1, "/sethp 22 40")
    await drive(table, client, PLAYER1, "/levelup")
    await drive(table, client, PLAYER1, "/level 5")
    await drive(table, client, PLAYER1, "/level 99")
    await drive(table, client, PLAYER1, "/xp 6500")
    await drive(table, client, PLAYER1, "/set ac 18")
    await drive(table, client, PLAYER1, "/set dex 18")
    await drive(table, client, PLAYER1, "/set nonsense 4")
    await drive(table, client, PLAYER1, "/note loves <b>the sea</b> & rum")
    await drive(table, client, PLAYER1, "/char")
    await drive(table, client, PLAYER1, "/party")
    await drive(table, client, PLAYER1, "/switch")
    await drive(table, client, PLAYER1, "/init")

    if (await db.get_character(character["id"]))["level"] != 5:
        FAIL.append("level: expected level 5")

    # -- encounters: hidden HP ------------------------------------------
    await drive(table, client, DM, "/encounters")
    await drive(table, client, DM, "/newencounter")
    enc = await drive(table, client, DM, "/newencounter Ambush at the ford | hidden")
    if "NOT" not in (enc.replies[0]["text"] if enc.replies else ""):
        FAIL.append("encounter: 'hidden' keyword did not switch the HP mode")
    stored = (await db.list_encounters(campaign_id))[0]
    if stored["hp_mode"] != "hidden":
        FAIL.append(f"encounter: expected hidden mode, got {stored['hp_mode']}")

    await drive(table, client, PLAYER1, "/newencounter Sneaky one")  # not the DM
    await drive(table, client, DM, "/addmonster goblin 3")
    await drive(table, client, DM, "/addmonster ogre")
    await drive(table, client, DM, "/addmonster ogre")
    await drive(table, client, DM, "/addmonster adult-red-dragn")
    await drive(table, client, DM, "/addmonster goblin x5")
    await drive(table, client, DM, "/enc")
    await drive(table, client, PLAYER1, "/enc")  # plans stay private

    monsters = await db.encounter_monsters(stored["id"])
    if len(monsters) != 5:
        FAIL.append(f"encounter: expected 5 monster entries, got {len(monsters)}")

    # tailoring
    await drive(table, client, DM, "/ms")
    await drive(table, client, DM, "/ms 1")
    await drive(table, client, DM, "/ms 1 ac 16")
    await drive(table, client, DM, "/ms 1 hp 20")
    await drive(table, client, DM, "/ms 1 dmg 2d6+3")
    await drive(table, client, DM, "/ms 1 atk +6")
    await drive(table, client, DM, "/ms 1 count 4")
    await drive(table, client, DM, "/ms 2 name Dire Ogre")
    await drive(table, client, DM, "/ms 2 note breathes fire")
    await drive(table, client, DM, "/ms 3 hide")
    await drive(table, client, DM, "/ms 3 show")
    await drive(table, client, DM, "/ms 9 ac 5")
    await drive(table, client, DM, "/ms 1 hp")
    await drive(table, client, DM, "/ms 1 nonsense 5")
    await drive(table, client, DM, "/ms 1 dmg notdice")
    await drive(table, client, DM, "/ms 1 ac 999")  # clamped
    await drive(table, client, DM, "/hpmode")
    await drive(table, client, DM, "/hpmode visible")
    await drive(table, client, DM, "/hpmode hidden")
    await drive(table, client, DM, "/hpmode sideways")

    by_slot = {m["slot"]: m for m in await db.encounter_monsters(stored["id"])}
    goblin = by_slot[1]
    if goblin["name"] != "Goblin":
        FAIL.append(f"encounter: slot 1 should be the goblin, got {goblin['name']}")
    if (goblin["ac"], goblin["max_hp"], goblin["damage"], goblin["attack_bonus"],
            goblin["count"]) != (40, 20, "2d6+3", 6, 4):
        FAIL.append(f"encounter: tailoring not applied: ac={goblin['ac']} hp={goblin['max_hp']} "
                    f"dmg={goblin['damage']} atk={goblin['attack_bonus']} count={goblin['count']}")
    if by_slot[2]["name"] != "Dire Ogre" or by_slot[2]["notes"] != "breathes fire":
        FAIL.append("encounter: rename/note not saved")

    # invoke it
    await drive(table, client, PLAYER1, "/fight")  # no fight yet
    await drive(table, client, DM, "/fight Ambush at the ford")
    run = await db.active_run(campaign_id)
    if not run:
        FAIL.append("encounter: /fight did not start a run")
    else:
        units = await db.run_units(run["id"])
        # counts: 4 goblins + 2 ogres + 5 goblins + 1 dragon
        if len(units) != 12:
            FAIL.append(f"encounter: expected 12 units from counts, got {len(units)}")
        # the tailored stats must carry into the live units
        if units[0]["ac"] != 40 or units[0]["max_hp"] != 20:
            FAIL.append(f"encounter: live unit did not inherit tailored stats "
                        f"(ac={units[0]['ac']} hp={units[0]['max_hp']})")

    # player view must not leak HP numbers
    player_view = await drive(table, client, PLAYER1, "/fight")
    text = player_view.replies[0]["text"] if player_view.replies else ""
    if "/20" in text or "/7" in text:
        FAIL.append(f"encounter: PLAYER VIEW LEAKED HP: {text[:200]}")
    if "untouched" not in text.lower():
        FAIL.append("encounter: hidden view before damage should say untouched")

    # damage attributed to whoever dealt it
    await drive(table, client, PLAYER1, "/hit 1 12")
    await drive(table, client, DM, "/hit goblin 3")
    await drive(table, client, PLAYER1, "/hit 4 2d6+3")
    await drive(table, client, PLAYER1, "/hit 99 5")
    await drive(table, client, PLAYER1, "/hit 1 notanumber")
    await drive(table, client, PLAYER1, "/hit")
    after = await drive(table, client, PLAYER1, "/fight")
    after_text = after.replies[0]["text"] if after.replies else ""
    if "P2002" not in after_text or "P1001" not in after_text:
        FAIL.append(f"encounter: hidden view should credit each player: {after_text[:300]}")

    # DM sees everything
    dm_view = await drive(table, client, DM, "/fight")
    dm_text = dm_view.replies[0]["text"] if dm_view.replies else ""
    if "/20" not in dm_text:
        FAIL.append(f"encounter: DM view should show HP: {dm_text[:250]}")
    if "cannot see" not in dm_text.lower():
        FAIL.append("encounter: DM view should note that players cannot see the numbers")

    # kill everything, then end the fight
    for _ in range(6):
        await drive(table, client, DM, "/hit 1 99")
    await drive(table, client, DM, "/hit 2 99")
    await drive(table, client, DM, "/hit 3 99")
    await drive(table, client, DM, "/hit 4 99")
    await drive(table, client, DM, "/hit 5 99")
    await drive(table, client, DM, "/endfight")
    if await db.active_run(campaign_id):
        FAIL.append("encounter: /endfight left a run active")
    await drive(table, client, DM, "/endfight")  # nothing running
    await drive(table, client, PLAYER1, "/hit 1 5")  # no fight

    # visible mode shows HP to players
    await drive(table, client, DM, "/newencounter Open brawl | visible")
    await drive(table, client, DM, "/addmonster goblin 2")
    await drive(table, client, DM, "/fight Open brawl")
    visible = await drive(table, client, PLAYER1, "/fight")
    vtext = visible.replies[0]["text"] if visible.replies else ""
    if "/7" not in vtext:
        FAIL.append(f"encounter: visible mode should show player HP: {vtext[:250]}")
    await drive(table, client, DM, "/endfight")

    await drive(table, client, DM, "/delenc Ambush at the ford")

    # -- no-HP monsters, reveals and death alerts ------------------------
    client.sent.clear()
    await drive(table, client, DM, "/newencounter Haunted crypt | hidden")
    await drive(table, client, DM, "/addmonster skeleton 2")
    await drive(table, client, DM, "/addmonster wraith")
    await drive(table, client, DM, "/ms 2 nohp")
    await drive(table, client, DM, "/ms 2 hide")
    await drive(table, client, DM, "/ms 2 hpback")
    await drive(table, client, DM, "/ms 2 nohp")
    nohp = (await db.encounter_monsters((await db.list_encounters(campaign_id))[0]["id"]))[1]
    if not nohp["no_hp"]:
        FAIL.append("encounter: /ms 2 nohp did not set the flag")
    client.sent.clear()

    start = await drive(table, client, DM, "/fight Haunted crypt")
    rollout_text = "\n".join(r["text"] or "" for r in start.replies) + "\n".join(
        m for _, m in client.sent
    )
    if "lurk" in rollout_text.lower() or "more" in rollout_text.lower():
        FAIL.append(f"encounter: rollout advertises hidden monsters: {rollout_text[:250]}")
    if "Wraith" in rollout_text:
        FAIL.append("encounter: a hidden monster appeared in the party rollout")
    client.sent.clear()

    # a reveal mid-fight must be announced
    await drive(table, client, DM, "/ms 2 show")
    reveals = [m for _, m in client.sent if "reveals" in m.lower()]
    if not reveals:
        FAIL.append("encounter: /ms N show did not announce the reveal")
    else:
        check_html("reveal announce", reveals[0])
    client.sent.clear()

    # a normal monster at 0 HP alerts everyone (one message per member)
    run_id = (await db.active_run(campaign_id))["id"]
    first_skeleton = await db.find_unit(run_id, "Skeleton #1")
    await drive(table, client, PLAYER1, "/hit skeleton 30")
    death_msgs = {m for _, m in client.sent if "goes down" in m.lower()}
    if len(death_msgs) != 1:
        FAIL.append(f"encounter: expected 1 distinct death alert, got {len(death_msgs)}")
    else:
        check_html("death alert", death_msgs.pop())
        if "Skeleton #1" not in _last_client_sent(client, "goes down"):
            FAIL.append("encounter: death alert names the wrong unit")
    recipients = {ent for ent, m in client.sent if "goes down" in m.lower()}
    if len(recipients) < 2:
        FAIL.append(f"encounter: death alert only reached {recipients}, expected the whole party")
    skeleton = await db.get_unit(first_skeleton["id"])
    if skeleton["status"] != "dead":
        FAIL.append("encounter: skeleton should be dead")
    # a positional selector skips the dead unit
    survivor = await db.find_unit(run_id, "1")
    if survivor["label"] != "Skeleton #2":
        FAIL.append(f"encounter: selector should skip the dead, got {survivor['label']}")
    client.sent.clear()

    # a no-HP monster absorbs damage and stays up
    await drive(table, client, PLAYER1, "/hit wraith 500")
    wraith = await db.find_unit((await db.active_run(campaign_id))["id"], "wraith")
    if wraith["status"] == "dead":
        FAIL.append("encounter: a no-HP monster must not die from damage")
    totals = await db.unit_damage_totals((await db.active_run(campaign_id))["id"])
    if totals.get(wraith["id"], 0) != 500:
        FAIL.append(f"encounter: no-HP damage not accumulated: {totals.get(wraith['id'])}")
    if client.sent:
        FAIL.append(f"encounter: no-HP monster wrongly announced a death: {client.sent}")

    # the DM drops it with /kill, which does alert the party
    await drive(table, client, DM, "/kill wraith")
    wraith = await db.get_unit(wraith["id"])
    if wraith["status"] != "dead":
        FAIL.append("encounter: /kill did not drop the no-HP monster")
    kills = [m for _, m in client.sent if "goes down" in m.lower()]
    if not kills:
        FAIL.append("encounter: /kill did not announce the death to the party")
    else:
        check_html("kill announce", kills[0])
    await drive(table, client, DM, "/kill wraith")  # already dead
    await drive(table, client, PLAYER1, "/kill wraith")  # not the DM
    await drive(table, client, DM, "/kill")  # no selector
    await drive(table, client, DM, "/kill nothing-here")
    await drive(table, client, DM, "/endfight")

    # -- boss crown, target numbers, fight-end announcement --------------
    client.sent.clear()
    await drive(table, client, DM, "/newencounter Dragon lair | visible")
    await drive(table, client, DM, "/addmonster goblin 2")
    await drive(table, client, DM, "/addmonster ogre")
    await drive(table, client, DM, "/ms 2 boss")
    dragons = await db.encounter_monsters((await db.list_encounters(campaign_id))[0]["id"])
    if not dragons[1]["is_boss"] or dragons[0]["is_boss"]:
        FAIL.append("encounter: /ms N boss set the wrong rows")

    start = await drive(table, client, DM, "/fight Dragon lair")
    start_text = "\n".join(r["text"] or "" for r in start.replies)
    if "\U0001f451" not in start_text:
        FAIL.append("encounter: boss crown missing from the party rollout")
    if "3. " not in start_text or "1. " not in start_text:
        FAIL.append(f"encounter: rollout is not numbered: {start_text[:220]}")

    listed = await drive(table, client, PLAYER1, "/fight")
    listed_text = listed.replies[0]["text"] if listed.replies else ""
    # the crown must reach players, and numbers must be the usable ones
    if "\U0001f451" not in listed_text:
        FAIL.append("encounter: boss crown missing from the player view")
    if listed_text.find("1. Goblin #1") < 0 or listed_text.find("3. Ogre") < 0:
        FAIL.append(f"encounter: player view not numbered: {listed_text[:220]}")
    elif listed_text.index("1. Goblin #1") > listed_text.index("3. Ogre"):
        FAIL.append("encounter: combatants are not listed in target order")

    # the number a player sees is the one /hit resolves to
    await drive(table, client, PLAYER1, "/hit 2 20")
    run_now = await db.active_run(campaign_id)
    goblin2 = await db.find_unit(run_now["id"], "Goblin #2")
    if goblin2["status"] != "dead":
        FAIL.append("encounter: /hit 2 did not target the second goblin")
    # ...and the survivors close up the numbering
    after = await drive(table, client, PLAYER1, "/fight")
    after_text = after.replies[0]["text"] if after.replies else ""
    if "2. Ogre" not in after_text or "2. Goblin #1" in after_text:
        FAIL.append(f"encounter: numbers did not close up: {after_text[:220]}")
    renumbered = await db.find_unit(run_now["id"], "1")
    if renumbered["label"] != "Goblin #1":
        FAIL.append(f"encounter: /hit 1 should now be {renumbered['label']}")
    await drive(table, client, PLAYER1, "/hit 3 5")  # out of range now

    # promoting a boss mid-fight reaches the live units
    await drive(table, client, DM, "/ms 1 boss")
    promoted = await drive(table, client, PLAYER1, "/fight")
    if promoted.replies[0]["text"].count("\U0001f451") < 3:
        FAIL.append("encounter: mid-fight boss promotion not shown to players")

    # the fight ending is announced to everyone who was playing
    client.sent.clear()
    await drive(table, client, DM, "/endfight")
    ending = [m for _, m in client.sent if "fight is over" in m.lower()]
    if not ending:
        FAIL.append("encounter: /endfight did not announce the end to the party")
    else:
        check_html("fight over announce", ending[0])
        # the DM ran the command and gets their own reply, so only players
        # should have been messaged
        if PLAYER1 not in {ent for ent, _ in client.sent}:
            FAIL.append("encounter: end-of-fight notice did not reach the players")
        if DM in {ent for ent, _ in client.sent}:
            FAIL.append("encounter: end-of-fight notice should not double-notify the DM")

    # hidden monsters cannot be probed by number
    await drive(table, client, DM, "/newencounter Ambush | hidden")
    await drive(table, client, DM, "/addmonster goblin")
    await drive(table, client, DM, "/ms 1 hide")
    await drive(table, client, DM, "/fight Ambush")
    probe = await drive(table, client, PLAYER1, "/hit 1 5")
    probe_text = probe.replies[0]["text"] if probe.replies else ""
    if "Goblin" in probe_text:
        FAIL.append(f"encounter: a player revealed a hidden monster by number: {probe_text}")
    dm_probe = await drive(table, client, DM, "/hit 1 5")
    if "Goblin" not in (dm_probe.replies[0]["text"] if dm_probe.replies else ""):
        FAIL.append("encounter: the DM should still be able to hit a hidden monster")
    await drive(table, client, DM, "/endfight")

    # -- sessions --------------------------------------------------------
    await drive(table, client, PLAYER1, "/startsession")  # not the DM
    event = await drive(table, client, DM, "/startsession Into the Marsh")
    await drive(table, client, DM, "/startsession")  # already running
    await drive(table, client, PLAYER1, "/checkin")
    await drive(table, client, DM, "/checkin")
    await drive(table, client, PLAYER1, "/who")
    await drive(table, client, DM, "/who")
    await drive(table, client, DM, "/announce Bring dice")
    await drive(table, client, PLAYER1, "/announce nope")
    await drive(table, client, DM, "/session")
    await drive(table, client, DM, "/endsession The dragon fled.")
    await drive(table, client, DM, "/endsession")  # nothing running
    await drive(table, client, PLAYER1, "/leave")
    await drive(table, client, DM, "/leave")

    # -- dice ------------------------------------------------------------
    for expression in ("2d6+3", "d20", "4d6kh3", "adv", "dis", "1d20+7",
                       "4d6r<=1", "2d6!>=6", "2d20kl1", "10d100", "nonsense",
                       "2d6kh9", "0d6", ""):
        await drive(table, client, PLAYER1, f"/roll {expression}".strip())
    await drive(table, client, PLAYER1, "/adv +4")
    await drive(table, client, PLAYER1, "/dis")

    # -- SRD -------------------------------------------------------------
    for command in ("/monster goblin", "/monster ancient red drgn", "/spell fireball",
                    "/item adamantine armor", "/equipment longsword",
                    "/rule advantage and disadvantage", "/class wizard",
                    "/race elf", "/subrace high elf", "/condition blinded",
                    "/skill stealth", "/prof daggers", "/damagetype fire",
                    "/align chaotic good",
                    "/monster zzzzzznotathing",
                    "/spell", "/search dragon", "/search", "/randmonster 1/4",
                    "/randspell", "/race"):
        await drive(table, client, PLAYER1, command)

    # -- callback paging --------------------------------------------------
    # SRD buttons address entries by short token, not by their long slug.
    fireball = entry_token("spells", "fireball")
    dragon = entry_token("monsters", "adult-black-dragon")
    longrest = entry_token("rules", "long-rest")
    if entry_from_token(fireball) != ("spells", "fireball"):
        FAIL.append("callback: entry_token does not round-trip")
    await open_callback(client, real, f"srdmenu:{fireball}", PLAYER1)
    await open_callback(client, real, f"srd:{dragon}:0", PLAYER1)
    await open_callback(client, real, f"srd:{longrest}:1", PLAYER1)
    await open_callback(client, real, "srdmenu:t999999", PLAYER1)  # unknown token
    await open_callback(client, real, "browse:spells:0", PLAYER1)
    await open_callback(client, real, "browse:monsters:2", PLAYER1)
    await open_callback(client, real, f"char:party:{campaign_id}", DM)
    await open_callback(client, real, f"char:view:{character['id']}", PLAYER1)
    await open_callback(client, real, "camp:menu", DM)
    await open_callback(client, real, "menu:srd", PLAYER1)
    await open_callback(client, real, "menu:help:character", PLAYER1)
    # The whole dice pad, including the random-monster button that used to
    # crash on CallbackQuery events.
    await open_callback(client, real, "dice:open", PLAYER1)
    await open_callback(client, real, "dice:d20", PLAYER1)
    await open_callback(client, real, "dice:d6", PLAYER1)
    await open_callback(client, real, "dice:op:+", PLAYER1)
    await open_callback(client, real, "dice:adv", PLAYER1)
    await open_callback(client, real, "dice:dis", PLAYER1)
    await open_callback(client, real, "dice:kh3", PLAYER1)
    await open_callback(client, real, "dice:clr", PLAYER1)
    await open_callback(client, real, "dice:go:d20", PLAYER1)
    await open_callback(client, real, "dice:done", PLAYER1)
    await open_callback(client, real, "dice:encounter", PLAYER1)
    # The encounter must arrive as a new message, not overwrite the roll.
    if not client.sent:
        FAIL.append("dice:encounter did not send a random monster")
    else:
        check_html("dice:encounter", client.sent[-1][1])

    # -- errors log ------------------------------------------------------
    await drive(table, client, PLAYER1, "/errors")
    # Force a real failure through the guard, then confirm it is logged and
    # that the failure was reported rather than silently swallowed.
    character_mod.BROKEN = True
    await drive(table, client, PLAYER1, "/char", expect_error=True)
    if not character_mod.BROKEN:
        FAIL.append("errors: could not arm the deliberate failure")
    logged = recent_errors(max_entries=4)
    if not logged:
        FAIL.append("errors: nothing written to the error log after a crash")
    elif "deliberate" not in "\n".join(logged):
        FAIL.append(f"errors: crash not found in log: {logged[:1]}")
    character_mod.BROKEN = False
    await drive(table, client, PLAYER1, "/errors")

    # -- escaping check ---------------------------------------------------
    await drive(table, client, DM, "/newcampaign <b>Bold</b> & <i>Bad</i>")
    await drive(table, client, DM, "/campaigns")
    await drive(table, client, DM, "/select")

    return report()


class FakeCallback:
    """Stands in for a Telethon CallbackQuery event.

    Deliberately has no ``.out`` attribute - the real CallbackQuery.Event does
    not either, and only NewMessage.Event carries it.
    """

    def __init__(self, client, sender_id, data, chat_id=1):
        self.client = client
        self.sender_id = sender_id
        self.data = data.encode()
        self.chat_id = chat_id
        self.is_private = True
        self.name = f"user{sender_id}"
        self.sender = SimpleNamespace(
            id=sender_id, username=self.name, first_name=f"P{sender_id}", last_name=None
        )
        self.answers: list = []
        self.edits: list[dict] = []

    async def answer(self, text="", alert=False):
        self.answers.append(text)

    async def reply(self, text=None, **kwargs):
        self.edits.append({"text": text, "buttons": kwargs.get("buttons")})
        return SimpleNamespace()

    async def edit(self, text=None, **kwargs):
        self.edits.append({"text": text, "buttons": kwargs.get("buttons")})

    async def get_chat_id(self):
        return self.chat_id


async def open_callback(client, real, data, user_id, expect_edit=True):
    callback = find_callback_handler(ENTRIES, data)
    if callback is None:
        FAIL.append(f"callback {data}: no handler matched")
        return
    event = FakeCallback(client, user_id, data)
    try:
        await callback(event)
    except Exception as exc:
        FAIL.append(f"callback {data}: {type(exc).__name__}: {exc}")
        return
    for record in ERRORS.take():
        detail = record.exc_info[1] if record.exc_info else record.getMessage()
        FAIL.append(f"callback {data}: logged an error: {type(detail).__name__}: {detail}")
    for edit in event.edits:
        check_html(f"callback {data}", edit["text"] or "")
    # A callback that neither edits nor answers has done nothing visible.
    if expect_edit and not event.edits and not event.answers:
        FAIL.append(f"callback {data}: handler did nothing")


async def resolve_join(client, real, request_id, decision, dm_id):
    data = f"join:{request_id}:{decision}"
    callback = find_callback_handler(ENTRIES, data)
    if callback is None:
        FAIL.append(f"callback {data}: no handler matched")
        return
    event = FakeCallback(client, dm_id, data)
    await callback(event)
    for edit in event.edits:
        check_html(f"join:{decision}", edit["text"] or "")


def _last_client_sent(client, needle: str) -> str:
    """Most recent outbound message containing `needle`."""
    for _, message in reversed(client.sent):
        if needle.lower() in message.lower():
            return message
    return ""


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
    sys.exit(asyncio.run(main()))
