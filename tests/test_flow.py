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
    for topic in ("dice", "campaign", "session", "character", "srd", "nonsense"):
        await drive(table, client, DM, f"/help {topic}")

    # -- tutorial --------------------------------------------------------
    await drive(table, client, PLAYER1, "/tutorial")
    for topic in ("setup", "join", "dice", "character", "srd", "groups", "start",
                  "dm", "campaign", "nonsense", "999999999"):
        await drive(table, client, PLAYER1, f"/tutorial {topic}")
    for page in range(8):  # includes out-of-range pages on purpose
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
