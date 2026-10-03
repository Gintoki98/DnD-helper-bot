"""/tutorial - a paginated, task-oriented walkthrough of the bot."""

from __future__ import annotations

from telethon import Button, events

from ..common import command_argument, safe, send_view
from ..help import ALIASES, PAGE_KEYS, TUTORIAL_PAGES


def keyboard(page: int = 0) -> list:
    """Pager for the tutorial, plus a way back to the main menu."""
    total = len(TUTORIAL_PAGES)
    nav = []
    if page > 0:
        nav.append(Button.inline("\u25c0 Back", f"tut:{page - 1}"))
    nav.append(Button.inline(f"{page + 1}/{total}", f"tut:{page}"))
    if page + 1 < total:
        nav.append(Button.inline("Next \u25b6", f"tut:{page + 1}"))
    return [nav, [Button.inline("\U0001f3e0 Menu", "menu:home")]]


async def send(event, page: int = 0, replace: bool | None = None) -> None:
    """Send one tutorial page. Bodies are hand-written valid Telegram HTML.

    Paging buttons replace the message in place; ``/tutorial`` sends a new one.
    """
    page = max(0, min(page, len(TUTORIAL_PAGES) - 1))
    await send_view(event, TUTORIAL_PAGES[page][1], keyboard(page), replace=replace)


def register(client) -> None:
    @client.on(events.NewMessage(pattern=r"^/tutorial(?:@[\w_]+)?(?:\s+(\w+))?$"))
    async def tutorial(event: events.NewMessage.Event) -> None:
        topic = command_argument(event).lower()
        if not topic:
            await send(event, 0)
            return
        topic = ALIASES.get(topic, topic)
        if topic in PAGE_KEYS:
            await send(event, PAGE_KEYS.index(topic))
            return
        await event.reply(
            f"No tutorial page called <b>{safe(topic)}</b>.\n"
            f"Try one of: <code>{'</code>, <code>'.join(PAGE_KEYS)}</code>\n"
            "or send <code>/tutorial</code> and page through with the arrows.",
            parse_mode="html",
        )

    @client.on(events.CallbackQuery(pattern=r"^tut:"))
    async def tutorial_pages(event: events.CallbackQuery.Event) -> None:
        page = int(event.data.decode().split(":")[1])
        page = max(0, min(page, len(TUTORIAL_PAGES) - 1))
        await event.answer(f"Page {page + 1}/{len(TUTORIAL_PAGES)}")
        await send(event, page)
