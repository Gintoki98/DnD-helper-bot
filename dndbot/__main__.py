"""Bot entry point: build the Telethon client, register handlers, run."""

from __future__ import annotations

import asyncio
import functools
import logging
import sys

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, MessageNotModifiedError

from . import config
from .config import ConfigError
from .handlers import campaign, character, core, dice, srd_lookup, tutorial
from .logs import setup_logging
from .srd import srd
from .storage import db

log = logging.getLogger("dndbot")


class HandlerError(Exception):
    """Raised inside the wrapper to tell a player their command failed."""


def _guard(func):
    """Wrap a handler so a crash replies politely instead of vanishing.

    Every failure also lands in logs/errors.log with its traceback and the
    chat it came from, so you can read it after the fact.
    """

    @functools.wraps(func)
    async def wrapper(event, *args, **kwargs):
        try:
            return await func(event, *args, **kwargs)
        except HandlerError as exc:
            await _safe_reply(event, str(exc))
        except FloodWaitError as exc:
            log.warning(
                "flood wait in %s: %ss", func.__name__, exc.seconds
            )
        except MessageNotModifiedError:
            return None
        except Exception:
            log.error(
                "handler %s failed in chat=%s user=%s",
                func.__name__,
                getattr(event, "chat_id", "?"),
                getattr(event, "sender_id", "?"),
                exc_info=True,
            )
            await _safe_reply(
                event,
                "\U0001f41e Something went wrong on my side. It has been written to "
                "the error log - try again in a moment.",
            )

    return wrapper


async def _safe_reply(event, text: str) -> None:
    try:
        await event.reply(text, parse_mode="html")
    except Exception:
        try:
            await event.reply(text)
        except Exception:
            pass


def _install_safety_nets(client) -> None:
    """Guard every handler and soften the common Telethon runtime errors."""
    original_add = client.add_event_handler

    def guarded_add(callback, event=None, **kwargs):
        return original_add(_guard(callback), event, **kwargs)

    client.add_event_handler = guarded_add

    # Decorators such as @client.on(...) call add_event_handler internally, so
    # patching it above covers every registration path.
    # Pager buttons can ask for the page that is already showing.
    original_edit = events.CallbackQuery.Event.edit

    async def safe_edit(self, *args, **kwargs):
        try:
            return await original_edit(self, *args, **kwargs)
        except MessageNotModifiedError:
            return None

    events.CallbackQuery.Event.edit = safe_edit


def build_client() -> TelegramClient:
    client = TelegramClient(
        str(config.SESSION_PATH),
        config.API_ID,
        config.API_HASH,
    )
    # Telethon takes parse_mode as an attribute, not a constructor argument.
    # Every reply in this bot is authored as Telegram-flavoured HTML.
    client.parse_mode = "html"
    return client


def register_handlers(client) -> None:
    core.register(client)
    dice.register(client)
    campaign.register(client)
    character.register(client)
    srd_lookup.register(client)
    tutorial.register(client)


BOT_COMMANDS = [
    # start / meta
    "start", "help", "tutorial", "whoami", "errors",
    # dice
    "roll", "adv", "dis", "init",
    # campaigns
    "newcampaign", "campaign", "campaigns", "select", "join", "leave", "pending",
    "roster",
    # sessions
    "startsession", "endsession", "session", "checkin", "who", "announce",
    # characters
    "newchar", "char", "party", "switch", "hp", "sethp", "level", "xp", "set", "note",
    # SRD
    "monster", "spell", "item", "equipment", "rule", "class", "race", "subrace",
    "condition", "skill", "prof", "damagetype", "align", "search", "randmonster",
    "randspell",
]


async def publish_commands(client) -> None:
    """Put the command list in Telegram's slash menu for every chat.

    Note the class is ``SetBotCommandsRequest``; older Telethon releases used
    ``SetDefaultBotCommandsRequest``, so fall back if the name is missing.
    """
    from telethon.tl import functions, types

    commands = [
        types.BotCommand(command=command, description=f"/{command}")
        for command in BOT_COMMANDS
    ]
    scope = types.BotCommandScopeDefault()

    attempts = [
        (getattr(functions.bots, "SetBotCommandsRequest", None), commands),
        (getattr(functions.bots, "SetDefaultBotCommandsRequest", None), commands),
        # Very old API hashes: only the scope-less variant exists.
        (getattr(functions.bots, "SetBotCommandsRequest", None), None),
    ]
    for factory, payload in attempts:
        if factory is None:
            continue
        try:
            kwargs = {"scope": scope, "lang_code": "en", "commands": commands}
            if payload is None:
                kwargs.pop("scope")
            await client(factory(**kwargs))
            log.info("published %s bot commands", len(commands))
            return
        except TypeError as exc:
            log.debug("command publish signature mismatch: %s", exc)
        except Exception as exc:
            log.warning("could not publish bot commands: %s", exc)
            return
    log.warning("no usable SetBotCommands API in this Telethon version")


async def main() -> int:
    setup_logging()
    log.info("logging to %s and %s", config.LOG_FILE, config.LOG_DIR / "errors.log")
    config.check()

    client = build_client()
    _install_safety_nets(client)
    register_handlers(client)

    log.info("signing in as a bot…")
    await client.start(bot_token=config.BOT_TOKEN)
    me = await client.get_me()
    if not getattr(me, "bot", False):
        raise ConfigError(
            "That BOT_TOKEN belongs to a user account, not a bot. "
            "Get a token from @BotFather."
        )
    log.info("connected as @%s (id %s)", me.username, me.id)
    await publish_commands(client)

    log.info("database ready at %s", config.DB_PATH)
    await db.connect()

    await srd.start()
    warmed = await srd.warm(("monsters", "spells", "magicitems", "rules"))
    log.info("SRD indexes ready: %s", ", ".join(sorted(warmed)) or "none (offline)")

    expired = await db.expire_stale_requests(config.REQUEST_TTL)
    if expired:
        log.info("expired %s stale join request(s)", expired)

    log.info("running - send /start in Telegram")
    try:
        await client.run_until_disconnected()
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("shutting down")
    except FloodWaitError as exc:
        log.warning("flood wait: sleeping %ss", exc.seconds)
        await asyncio.sleep(exc.seconds)
    finally:
        await srd.close()
        await db.close()
        if client.is_connected():
            await client.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
