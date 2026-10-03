"""Campaign, session and character flows shared by the handlers and the MCP server.

Everything here records state and enforces the rules; nothing here replies,
edits a message or sends a button. The handlers render outcomes as Telegram
HTML with keyboards, the MCP server as Markdown - same rules, two
presentations. Player-facing messages keep the same wording the bot has always
had, Telegram HTML included; the MCP layer strips the tags before speaking.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from . import config
from .common import NoCampaign, display_name, duration, relative, safe
from .storage import db


class Refused(Exception):
    """A shared flow refused to act; the message is written for the player."""


# -- campaigns -------------------------------------------------------------


@dataclass
class JoinOutcome:
    """What happened when someone asked to join a campaign."""

    campaign: Any
    request: Any | None  # None when they were already a member
    is_new: bool

    @property
    def already_member(self) -> bool:
        return self.request is None


async def create_campaign(user_id: int, name: str, description: str = "") -> Any:
    """Create a campaign, make its creator the DM, and select it for them."""
    if not name:
        raise Refused("The name cannot be empty.")
    if len(name) > 80:
        raise Refused("That name is too long - keep it under 80 characters.")
    existing = await db.campaigns_for_user(user_id)
    if any(row["name"].lower() == name.lower() for row in existing):
        raise Refused(f"You already have a campaign called <b>{safe(name)}</b>.")
    campaign = await db.create_campaign(
        name=name, dm_id=user_id, system=config.DEFAULT_SYSTEM, description=description
    )
    await db.set_active_campaign(user_id, campaign["id"])
    return campaign


async def request_join(user_id: int, term: str) -> JoinOutcome:
    """Ask to join by invite code or name; already being in doubles as select."""
    row = await db.lookup_campaign(term)
    if row is None:
        raise NoCampaign(
            f"No campaign matches <code>{term}</code>.\n"
            "Ask the DM for the 6-character invite code."
        )
    request, is_new = await db.create_join_request(row["id"], user_id)
    if request is None:  # already a member: point them at this campaign
        await db.set_active_campaign(user_id, row["id"])
    return JoinOutcome(campaign=row, request=request, is_new=is_new)


async def join_request_context(request_id: int, actor_id: int) -> tuple[Any, Any, Any]:
    """The (request, campaign, applicant) behind a pending join request.

    Only the DM who owns the campaign may decide it; every other outcome is a
    refusal carrying the message meant for whoever asked.
    """
    request = await db.get_request(request_id)
    if request is None or request["status"] != "pending":
        raise Refused("That request has already been handled.")
    campaign = await db.get_campaign(request["campaign_id"])
    if campaign is None:
        raise Refused("That campaign no longer exists.")
    if campaign["dm_id"] != actor_id:
        raise Refused("Only the DM can approve joins.")
    applicant = await db.get_user(request["user_id"])
    return request, campaign, applicant


async def approve_join(request, campaign, actor_id: int) -> None:
    """Grant a pending request: roster, then the player's active campaign."""
    await db.resolve_request(request["id"], "approved", actor_id)
    await db.add_member(campaign["id"], request["user_id"])
    await db.set_active_campaign(request["user_id"], campaign["id"])


async def deny_join(request, actor_id: int) -> None:
    await db.resolve_request(request["id"], "rejected", actor_id)


async def leave_campaign(user_id: int, campaign) -> None:
    """Leave a campaign; the DM never can."""
    if await db.is_dm(campaign["id"], user_id):
        if await db.active_session(campaign["id"]):
            raise Refused("End the running session first with <code>/endsession</code>.")
        raise Refused(
            f"You are the DM of <b>{safe(campaign['name'])}</b> and cannot leave it. "
            "Ask an admin if the campaign is over."
        )
    await db.remove_member(campaign["id"], user_id)


async def select_campaign(user_id: int, term: str) -> Any:
    """Make ``term`` this user's active campaign; returns the row.

    Same checks, same words and same order as ``/select``: being in a
    campaign at all first, then whether the name or code is known.
    """
    rows = await db.campaigns_for_user(user_id)
    if not rows:
        raise Refused("You are not in a campaign yet.")
    match = await db.lookup_campaign(term)
    if match is None:
        raise Refused(f"No campaign called <code>{safe(term)}</code>.")
    await db.set_active_campaign(user_id, match["id"])
    return match


# -- sessions --------------------------------------------------------------


async def start_session(campaign, actor_id: int, title: str = "") -> Any:
    """Open a session, refusing while one is already running."""
    if await db.active_session(campaign["id"]):
        raise Refused(
            "A session is already running. End it with <code>/endsession</code> first."
        )
    session = await db.start_session(campaign["id"], actor_id, title)
    await db.touch_campaign(campaign["id"])
    return session


async def end_session(campaign, notes: str = "") -> tuple[Any, float]:
    """Close the running session. Returns ``(session, elapsed seconds)``."""
    active = await db.active_session(campaign["id"])
    if not active:
        raise Refused("No session is running.")
    elapsed = time.time() - active["started_at"]
    await db.end_session(active["id"], notes)
    return active, elapsed


async def checkin(user_id: int, campaign) -> Any:
    """Seat a member at the running session; returns it.

    Raises ``Refused`` with the player's words when nothing is running -
    attendance is only recorded for a live session.
    """
    active = await db.active_session(campaign["id"])
    if not active:
        raise Refused("No session is running right now.")
    await db.mark_attendance(active["id"], user_id, "online")
    return active


# -- characters -------------------------------------------------------------


async def character_for(user_id: int, campaign: Any, name: str = "") -> Any:
    """The character a call refers to: an explicit name, else the active sheet.

    The bot's ``load_character`` and every MCP character tool share this: an
    explicit name wins, then the player's active sheet, then their only one.
    Raises ``LookupError`` carrying the words the player already knows.
    """
    if name:
        matches = await db.find_character(campaign["id"], name)
        if not matches:
            raise LookupError(
                f"No character called <b>{safe(name)}</b> in {safe(campaign['name'])}."
            )
        return matches[0]

    character = await db.active_character(campaign["id"], user_id)
    if character is None:
        mine = await db.list_characters(campaign["id"], user_id)
        if not mine:
            raise LookupError(
                "You have no character in this campaign yet.\n"
                "Make one with <code>/newchar</code>."
            )
        return mine[0]
    return character


async def switch_to(
    user_id: int, campaign: Any, term: str = ""
) -> tuple[Any | None, str]:
    """Switch the player's active character. Returns ``(character, html)``.

    ``character`` is the sheet that is now active, or ``None`` when nothing
    changed - in which case ``html`` is the whole reply: the pick list, "you
    only have one", or "you have no character called that". Wording and the
    order of the checks are the ones ``/switch`` has always had.
    """
    mine = await db.list_characters(campaign["id"], user_id)
    if not mine:
        return None, "You have no characters here. Try <code>/newchar</code>."
    if len(mine) == 1:
        return None, f"You only have one: <b>{safe(mine[0]['name'])}</b>."
    if not term:
        lines = ["<b>Your characters</b>", ""]
        for character in mine:
            active = " \u2705" if character["is_active"] else ""
            lines.append(
                f"• <b>{safe(character['name'])}</b> <i>{safe(character['class_name'])}</i> "
                f"L{character['level']} {character['hp']}/{character['max_hp']} HP{active}"
            )
        return None, "\n".join(lines) + "\n\n<i>Send /switch Name to change.</i>"

    matches = await db.find_character(campaign["id"], term)
    owned = [c for c in matches if c["user_id"] == user_id]
    if not owned:
        return None, f"You have no character called <b>{safe(term)}</b>."
    await db.set_active_character(owned[0]["id"])
    return owned[0], f"\u2705 <b>{safe(owned[0]['name'])}</b> is now your active character."


# -- words both sides send ------------------------------------------------
#
# The HTML behind a campaign or session reply: no keyboards, no broadcasts
# and no side effects, so the handlers add buttons on top and the MCP server
# converts the result with ``render.to_markdown``.


def campaign_summary(campaign, is_dm: bool, has_session: bool) -> str:
    """A campaign as it describes itself: role, session state, invite code."""
    role = "Dungeon Master" if is_dm else "Player"
    session = "\U0001f5c3\ufe0f <b>session live</b>" if has_session else "\U0001f4a4 no session running"
    lines = [
        f"<b>\U0001f3dd {safe(campaign['name'])}</b>",
        f"<i>{role} \u2022 {session}</i>",
        "",
        f"<b>Invite code:</b> <code>{campaign['invite_code']}</code>",
    ]
    if campaign["description"]:
        lines += ["", campaign["description"]]
    return "\n".join(lines)


def session_started_text(campaign, title: str, party: list) -> str:
    """The session-open confirmation: heading, party snapshot, next moves."""
    heading = f"<b>\U0001f5c3\ufe0f Session started</b>\n<i>{safe(campaign['name'])}"
    if title:
        heading += f" \u2014 {safe(title)}"
    heading += "</i>"

    lines = [heading, "", f"Players tracked: <b>{len(party)}</b>"]
    if party:
        lines.append("")
        lines += [
            f"• <b>{safe(p['name'])}</b> <i>{safe(p['class_name'])}</i> {p['hp']}/{p['max_hp']} HP"
            for p in party
        ]
    lines += [
        "",
        "<i>/checkin when you arrive \u2022 /who to see the table \u2022 "
        "/init to roll initiative \u2022 /endsession when you are done</i>",
    ]
    return "\n".join(lines)


def campaigns_pick_text(rows) -> str:
    """The bare name-and-code list behind ``/join`` and ``/select``."""
    return "\n".join(
        f"• <b>{safe(row['name'])}</b> <code>{row['invite_code']}</code>" for row in rows
    )


async def campaigns_list_text(rows) -> str:
    """The campaign list: role, live-session marker and invite code."""
    lines = ["<b>Your campaigns</b>", ""]
    for row in rows:
        active = await db.active_session(row["id"])
        role = "DM" if row["role"] == "dm" else "player"
        mark = " \U0001f5c3\ufe0f" if active else ""
        lines.append(
            f"• <b>{safe(row['name'])}</b> <i>{role}{mark}</i> "
            f"<code>{row['invite_code']}</code>"
        )
    return "\n".join(lines)


async def pending_requests_text(campaign, requests, with_ids: bool = False) -> str:
    """Who is waiting to join, and for how long.

    ``with_ids`` appends the request id, which the bot hides behind its
    Approve/Deny buttons and the MCP server needs to hand back to
    ``resolve_join``.
    """
    lines = [f"<b>\U0001f6e1 {len(requests)} pending for {safe(campaign['name'])}</b>", ""]
    for request in requests:
        user = await db.get_user(request["user_id"])
        suffix = f" \u2014 <code>#{request['id']}</code>" if with_ids else ""
        lines.append(
            f"• {display_name(user)} <i>{relative(request['created_at'])}</i>{suffix}"
        )
    return "\n".join(lines)


async def roster_text(campaign, members) -> str:
    """The roster, with each member's active character beside their name."""
    lines = [
        f"<b>\U0001f91d {safe(campaign['name'])}</b> <i>({len(members)} members)</i>",
        "",
    ]
    for member in members:
        tag = " <i>DM</i>" if member["role"] == "dm" else ""
        character = await db.active_character(campaign["id"], member["user_id"])
        sheet = (
            f" \u2014 {safe(character['name'])} L{character['level']} "
            f"{character['hp']}/{character['max_hp']} HP"
            if character
            else " \u2014 <i>no character</i>"
        )
        lines.append(f"\u2022 {display_name(member)}{tag}{sheet}")
    return "\n".join(lines)


def roster_brief_text(campaign, roster) -> str:
    """The roster as it looks when no session is running."""
    lines = [f"<b>{safe(campaign['name'])}</b> \u2014 roster ({len(roster)})", ""]
    lines += [
        f"• {display_name(m)}{' <i>(DM)</i>' if m['role'] == 'dm' else ''}"
        for m in roster
    ]
    lines += ["", "<i>No session running. Players sit in during one with /checkin.</i>"]
    return "\n".join(lines)


def at_table_text(present) -> str:
    """Who is seated at a live session."""
    lines = [
        f"<b>\U0001f5c3\ufe0f At the table</b> <i>({len(present)} checked in)</i>",
        "",
    ]
    lines += [f"• {display_name(row)}" for row in present]
    lines += ["", "<i>You are seated. The DM sees everyone who checked in.</i>"]
    return "\n".join(lines)


def session_history_text(campaign, history) -> str:
    """Recent sessions: status, length and title of each."""
    lines = [f"<b>\U0001f5c3\ufe0f {safe(campaign['name'])}</b>", ""]
    for row in history:
        live = row["status"] == "active"
        status = "\U0001f7e2 <b>live</b>" if live else "\u26ab ended"
        end = row["ended_at"] if row["ended_at"] else time.time()
        started = f"started {relative(row['started_at'])}" if live else ""
        title = f" \u2014 {row['title']}" if row["title"] else ""
        lines.append(
            f"• #{row['id']}{safe(title)} \u2014 {status}, {duration(end - row['started_at'])}"
            + (f" ({started})" if started else "")
        )
    return "\n".join(lines)
