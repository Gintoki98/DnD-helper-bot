"""Campaigns, the DM's join approvals, and session management."""

from __future__ import annotations

import asyncio
import time

from telethon import events

from .. import config, keyboards as kb
from ..common import (
    NoCampaign,
    NotAMember,
    NotTheDM,
    command_argument,
    display_name,
    duration,
    ensure_dm,
    ensure_member,
    plain_name,
    relative,
    resolve_campaign,
    safe,
)
from ..storage import db


def campaign_summary(campaign, is_dm: bool, has_session: bool) -> str:
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


async def broadcast(client, campaign_id: int, text: str, exclude: int | None = None) -> int:
    """Privately message everyone in a campaign's roster. Returns the count sent."""
    sent = 0
    for member in await db.roster(campaign_id):
        if exclude and member["user_id"] == exclude:
            continue
        try:
            await client.send_message(member["user_id"], text, parse_mode="html")
            sent += 1
        except Exception:
            continue  # blocked bot or deleted account
        await asyncio.sleep(0.4)  # stay friendly to the flood limits
    return sent


async def tell_dm(client, campaign, request, applicant_name: str) -> None:
    """Send the DM the approve/deny prompt for a pending join request."""
    text = (
        "\U0001f4e5 <b>Join request</b>\n\n"
        f"{applicant_name} wants to join <b>{safe(campaign['name'])}</b>."
        f"\n\n<code>/join {campaign['invite_code']}</code>"
    )
    try:
        await client.send_message(
            campaign["dm_id"],
            text,
            buttons=kb.join_request_keyboard(request["id"]),
            parse_mode="html",
        )
    except Exception:
        pass  # DM unreachable; /pending still lists it


def register(client) -> None:
    # ------------------------------------------------------------------
    # campaigns
    # ------------------------------------------------------------------
    @client.on(events.NewMessage(pattern=r"^/newcampaign(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def new_campaign(event: events.NewMessage.Event) -> None:
        raw = command_argument(event)
        if not raw:
            await event.reply(
                "Give the campaign a name:\n<code>/newcampaign The Amber Court</code>\n"
                "Or with a blurb:\n<code>/newcampaign The Amber Court | A haunted road north</code>",
                parse_mode="html",
            )
            return

        name, _, description = raw.partition("|")
        name, description = name.strip(), description.strip()
        if not name:
            await event.reply("The name cannot be empty.", parse_mode="html")
            return
        if len(name) > 80:
            await event.reply("That name is too long - keep it under 80 characters.")
            return

        existing = await db.campaigns_for_user(event.sender_id)
        if any(row["name"].lower() == name.lower() for row in existing):
            await event.reply(
                f"You already have a campaign called <b>{safe(name)}</b>.", parse_mode="html"
            )
            return

        campaign = await db.create_campaign(
            name=name,
            dm_id=event.sender_id,
            system=config.DEFAULT_SYSTEM,
            description=description,
        )
        await db.set_active_campaign(event.sender_id, campaign["id"])

        await event.reply(
            campaign_summary(campaign, True, False)
            + "\n\nShare the code so players can request to join:\n"
            f"<code>/join {campaign['invite_code']}</code>\n\n"
            "You approve each request, then start a session with <code>/startsession</code>.",
            buttons=kb.campaign_keyboard(campaign["id"], True, False),
            parse_mode="html",
        )

    @client.on(events.NewMessage(pattern=r"^/join(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def join_campaign(event: events.NewMessage.Event) -> None:
        term = command_argument(event)
        if not term:
            mine = await db.campaigns_for_user(event.sender_id)
            if not mine:
                await event.reply(
                    "Send the invite code the DM gave you:\n<code>/join ABC123</code>",
                    parse_mode="html",
                )
                return
            names = "\n".join(
                f"• <b>{safe(row['name'])}</b> <code>{row['invite_code']}</code>" for row in mine
            )
            await event.reply(f"You are already in:\n\n{names}", parse_mode="html")
            return

        row = await db.get_campaign_by_code(term)
        if row is None:
            matches = await db.find_campaigns(term)
            row = matches[0] if matches else None
        if row is None:
            await event.reply(
                f"No campaign matches <code>{term}</code>.\n"
                "Ask the DM for the 6-character invite code.",
                parse_mode="html",
            )
            return

        request, is_new = await db.create_join_request(row["id"], event.sender_id)
        if request is None:
            await db.set_active_campaign(event.sender_id, row["id"])
            await event.reply(
                f"You are already in <b>{safe(row['name'])}</b>.",
                buttons=kb.campaign_keyboard(
                    row["id"], await db.is_dm(row["id"], event.sender_id), False
                ),
                parse_mode="html",
            )
            return

        if is_new:
            await tell_dm(client, row, request, display_name(event.sender))
        await event.reply(
            f"\U0001f4e4 Requested to join <b>{safe(row['name'])}</b>.\n"
            "The DM has been notified - I will message you the moment you are in.",
            parse_mode="html",
        )

    @client.on(events.NewMessage(pattern=r"^/pending(?:@[\w_]+)?$"))
    async def pending_requests(event: events.NewMessage.Event) -> None:
        try:
            campaign = await resolve_campaign(event)
            await ensure_dm(campaign, event.sender_id)
        except NoCampaign as exc:
            await event.reply(str(exc), parse_mode="html")
            return
        except (NotAMember, NotTheDM) as exc:
            await event.reply(str(exc))
            return

        requests = await db.pending_requests(campaign["id"])
        if not requests:
            await event.reply("No one is waiting to join.", parse_mode="html")
            return

        lines = [f"<b>\U0001f6e1 {len(requests)} pending for {safe(campaign['name'])}</b>", ""]
        buttons = []
        for request in requests:
            user = await db.get_user(request["user_id"])
            lines.append(f"• {display_name(user)} <i>{relative(request['created_at'])}</i>")
            buttons.append(kb.join_request_keyboard(request["id"]))
        await event.reply("\n".join(lines), buttons=buttons, parse_mode="html")

    @client.on(events.NewMessage(pattern=r"^/(?:campaign|camp)(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def campaign_panel(event: events.NewMessage.Event) -> None:
        term = command_argument(event)
        try:
            campaign = await resolve_campaign(event, term)
            await ensure_member(campaign, event.sender_id)
        except (NoCampaign, NotAMember) as exc:
            await event.reply(str(exc), parse_mode="html")
            return

        is_dm = await db.is_dm(campaign["id"], event.sender_id)
        active = await db.active_session(campaign["id"])
        await event.reply(
            campaign_summary(campaign, is_dm, bool(active)),
            buttons=kb.campaign_keyboard(campaign["id"], is_dm, bool(active)),
            parse_mode="html",
        )

    @client.on(events.NewMessage(pattern=r"^/campaigns(?:@[\w_]+)?$"))
    async def list_campaigns(event: events.NewMessage.Event) -> None:
        rows = await db.campaigns_for_user(event.sender_id)
        if not rows:
            await event.reply(
                "You are not in any campaign yet.\n\n"
                "<code>/newcampaign My Campaign</code> to start one, "
                "or <code>/join ABC123</code> with a friend's code.",
                parse_mode="html",
            )
            return
        lines = ["<b>Your campaigns</b>", ""]
        for row in rows:
            active = await db.active_session(row["id"])
            role = "DM" if row["role"] == "dm" else "player"
            mark = " \U0001f5c3\ufe0f" if active else ""
            lines.append(
                f"• <b>{safe(row['name'])}</b> <i>{role}{mark}</i> "
                f"<code>{row['invite_code']}</code>"
            )
        await event.reply(
            "\n".join(lines),
            buttons=kb.campaigns_list_keyboard(rows),
            parse_mode="html",
        )

    @client.on(events.NewMessage(pattern=r"^/select(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def select_campaign(event: events.NewMessage.Event) -> None:
        rows = await db.campaigns_for_user(event.sender_id)
        if not rows:
            await event.reply("You are not in a campaign yet.", parse_mode="html")
            return
        term = command_argument(event)
        if term:
            match = await db.get_campaign_by_code(term)
            if match is None:
                found = await db.find_campaigns(term)
                match = found[0] if found else None
            if match is None:
                await event.reply(f"No campaign called <code>{term}</code>.", parse_mode="html")
                return
            await db.set_active_campaign(event.sender_id, match["id"])
            await event.reply(
                f"Working on <b>{match['name']}</b> now.",
                buttons=kb.campaign_keyboard(
                    match["id"], await db.is_dm(match["id"], event.sender_id), False
                ),
                parse_mode="html",
            )
            return
        lines = ["<b>Pick your active campaign</b>", ""]
        for row in rows:
            lines.append(f"• <b>{safe(row['name'])}</b> <code>{row['invite_code']}</code>")
        await event.reply(
            "\n".join(lines),
            buttons=kb.campaigns_list_keyboard(rows),
            parse_mode="html",
        )

    @client.on(events.NewMessage(pattern=r"^/roster(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def roster_command(event: events.NewMessage.Event) -> None:
        term = command_argument(event)
        try:
            campaign = await resolve_campaign(event, term)
            await ensure_member(campaign, event.sender_id)
        except (NoCampaign, NotAMember) as exc:
            await event.reply(str(exc), parse_mode="html")
            return

        members = await db.roster(campaign["id"])
        if not members:
            await event.reply(f"Nobody has joined <b>{safe(campaign['name'])}</b> yet.", parse_mode="html")
            return
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
        await event.reply(
            "\n".join(lines),
            buttons=kb.roster_keyboard(members, campaign["id"]),
            parse_mode="html",
        )

    @client.on(events.NewMessage(pattern=r"^/leave(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def leave_campaign(event: events.NewMessage.Event) -> None:
        term = command_argument(event)
        try:
            campaign = await resolve_campaign(event, term)
        except NoCampaign as exc:
            await event.reply(str(exc), parse_mode="html")
            return

        if await db.is_dm(campaign["id"], event.sender_id):
            if await db.active_session(campaign["id"]):
                await event.reply(
                    "End the running session first with <code>/endsession</code>.",
                    parse_mode="html",
                )
                return
            await event.reply(
                f"You are the DM of <b>{safe(campaign['name'])}</b> and cannot leave it. "
                "Ask an admin if the campaign is over.",
                parse_mode="html",
            )
            return

        await db.remove_member(campaign["id"], event.sender_id)
        await event.reply(
            f"You left <b>{safe(campaign['name'])}</b>. Your characters stay saved in case you return.",
            parse_mode="html",
        )

    # ------------------------------------------------------------------
    # sessions
    # ------------------------------------------------------------------
    @client.on(events.NewMessage(pattern=r"^/startsession(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def start_session(event: events.NewMessage.Event) -> None:
        try:
            campaign = await resolve_campaign(event)
            await ensure_dm(campaign, event.sender_id)
        except NoCampaign as exc:
            await event.reply(str(exc), parse_mode="html")
            return
        except (NotAMember, NotTheDM) as exc:
            await event.reply(str(exc))
            return

        if await db.active_session(campaign["id"]):
            await event.reply(
                "A session is already running. End it with <code>/endsession</code> first.",
                parse_mode="html",
            )
            return

        title = command_argument(event)
        await db.start_session(campaign["id"], event.sender_id, title)
        await db.touch_campaign(campaign["id"])

        party = await db.party(campaign["id"])
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

        await event.reply(
            "\n".join(lines),
            buttons=kb.campaign_keyboard(campaign["id"], True, True),
            parse_mode="html",
        )
        await broadcast(
            client,
            campaign["id"],
            f"\U0001f5c3\ufe0f <b>{safe(campaign['name'])}</b> is live. Players: "
            f"<code>/join {campaign['invite_code']}</code>",
            exclude=event.sender_id,
        )

    @client.on(events.NewMessage(pattern=r"^/endsession(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def end_session(event: events.NewMessage.Event) -> None:
        try:
            campaign = await resolve_campaign(event)
            await ensure_dm(campaign, event.sender_id)
        except NoCampaign as exc:
            await event.reply(str(exc), parse_mode="html")
            return
        except (NotAMember, NotTheDM) as exc:
            await event.reply(str(exc))
            return

        active = await db.active_session(campaign["id"])
        if not active:
            await event.reply("No session is running.", parse_mode="html")
            return

        notes = command_argument(event)
        length = duration(time.time() - active["started_at"])
        await db.end_session(active["id"], notes)
        await broadcast(
            client,
            campaign["id"],
            f"\U0001f6d1 <b>{safe(campaign['name'])}</b> session ended after {length}. Rest well.",
            exclude=event.sender_id,
        )
        await event.reply(
            f"\U0001f6d1 Session <b>#{active['id']}</b> ended after {length}."
            + (f"\n\n<i>Notes: {safe(notes)}</i>" if notes else ""),
            buttons=kb.campaign_keyboard(campaign["id"], True, False),
            parse_mode="html",
        )

    @client.on(events.NewMessage(pattern=r"^/session(?:@[\w_]+)?$"))
    async def session_status(event: events.NewMessage.Event) -> None:
        try:
            campaign = await resolve_campaign(event)
            await ensure_member(campaign, event.sender_id)
        except (NoCampaign, NotAMember) as exc:
            await event.reply(str(exc), parse_mode="html")
            return

        history = await db.recent_sessions(campaign["id"], 6)
        if not history:
            await event.reply(
                f"<b>{safe(campaign['name'])}</b>\n\nNo sessions yet. The DM starts one with "
                "<code>/startsession</code>.",
                parse_mode="html",
            )
            return

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
        await event.reply("\n".join(lines), parse_mode="html")

    @client.on(events.NewMessage(pattern=r"^/who(?:@[\w_]+)?$"))
    async def who_is_here(event: events.NewMessage.Event) -> None:
        try:
            campaign = await resolve_campaign(event)
            await ensure_member(campaign, event.sender_id)
        except (NoCampaign, NotAMember) as exc:
            await event.reply(str(exc), parse_mode="html")
            return

        active = await db.active_session(campaign["id"])
        if not active:
            roster = await db.roster(campaign["id"])
            lines = [
                f"<b>{safe(campaign['name'])}</b> \u2014 roster ({len(roster)})",
                "",
            ]
            lines += [
                f"• {display_name(m)}{' <i>(DM)</i>' if m['role'] == 'dm' else ''}"
                for m in roster
            ]
            lines += ["", "<i>No session running. Players sit in during one with /checkin.</i>"]
            await event.reply("\n".join(lines), parse_mode="html")
            return

        await db.mark_attendance(active["id"], event.sender_id, "online")
        present = await db.attendance(active["id"])
        lines = [
            f"<b>\U0001f5c3\ufe0f At the table</b> <i>({len(present)} checked in)</i>",
            "",
        ]
        lines += [f"• {display_name(row)}" for row in present]
        lines += ["", "<i>You are seated. The DM sees everyone who checked in.</i>"]
        await event.reply("\n".join(lines), parse_mode="html")

    @client.on(events.NewMessage(pattern=r"^/checkin(?:@[\w_]+)?$"))
    async def checkin(event: events.NewMessage.Event) -> None:
        try:
            campaign = await resolve_campaign(event)
            membership = await ensure_member(campaign, event.sender_id)
        except (NoCampaign, NotAMember) as exc:
            await event.reply(str(exc), parse_mode="html")
            return

        active = await db.active_session(campaign["id"])
        if not active:
            await event.reply("No session is running right now.", parse_mode="html")
            return
        await db.mark_attendance(active["id"], event.sender_id, "online")
        if membership["role"] != "dm":
            await broadcast(
                client,
                campaign["id"],
                f"\U0001f44b {plain_name(event.sender)} checked in.",
                exclude=event.sender_id,
            )
        await event.reply("\u2705 Checked in. Have fun.", parse_mode="html")

    @client.on(events.NewMessage(pattern=r"^/announce(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def announce(event: events.NewMessage.Event) -> None:
        text = command_argument(event)
        try:
            campaign = await resolve_campaign(event)
            await ensure_dm(campaign, event.sender_id)
        except NoCampaign as exc:
            await event.reply(str(exc), parse_mode="html")
            return
        except (NotAMember, NotTheDM) as exc:
            await event.reply(str(exc))
            return

        if not text:
            await event.reply(
                "What should I announce? <code>/announce Session is on Friday</code>",
                parse_mode="html",
            )
            return
        sent = await broadcast(
            client,
            campaign["id"],
            f"\U0001f4e3 <b>{safe(campaign['name'])}</b>\n\n{safe(text)}",
            exclude=event.sender_id,
        )
        await event.reply(f"\u2705 Announced to <b>{sent}</b> player(s).", parse_mode="html")

    # ------------------------------------------------------------------
    # callbacks
    # ------------------------------------------------------------------
    @client.on(events.CallbackQuery(pattern=r"^join:"))
    async def resolve_join(event: events.CallbackQuery.Event) -> None:
        _, request_id, decision = event.data.decode().split(":")
        request = await db.get_request(int(request_id))
        if request is None or request["status"] != "pending":
            await event.answer("That request has already been handled.", alert=True)
            return

        campaign = await db.get_campaign(request["campaign_id"])
        if campaign is None:
            await event.answer("That campaign no longer exists.", alert=True)
            return
        if campaign["dm_id"] != event.sender_id:
            await event.answer("Only the DM can approve joins.", alert=True)
            return

        applicant = await db.get_user(request["user_id"])
        who = plain_name(applicant)

        if decision == "y":
            await db.resolve_request(request["id"], "approved", event.sender_id)
            await db.add_member(campaign["id"], request["user_id"])
            await db.set_active_campaign(request["user_id"], campaign["id"])
            await event.edit(
                f"\u2705 <b>{who}</b> joined <b>{safe(campaign['name'])}</b>.", parse_mode="html"
            )
            await event.answer("Approved")
            try:
                await event.client.send_message(
                    request["user_id"],
                    f"\U0001f389 You are in! <b>{safe(campaign['name'])}</b> was approved by the DM.\n\n"
                    f"Invite code: <code>{campaign['invite_code']}</code>\n"
                    "Start a character with <code>/newchar</code>.",
                    buttons=kb.campaign_keyboard(campaign["id"], False, False),
                    parse_mode="html",
                )
            except Exception:
                pass
        else:
            await db.resolve_request(request["id"], "rejected", event.sender_id)
            await event.edit(
                f"\u274c Request from <b>{who}</b> declined.", parse_mode="html"
            )
            await event.answer("Denied")
            try:
                await event.client.send_message(
                    request["user_id"],
                    f"Your request to join <b>{safe(campaign['name'])}</b> was declined by the DM.",
                    parse_mode="html",
                )
            except Exception:
                pass

    @client.on(events.CallbackQuery(pattern=r"^camp:"))
    async def campaign_buttons(event: events.CallbackQuery.Event) -> None:
        parts = event.data.decode().split(":")
        action = parts[1]

        if action == "menu":
            rows = await db.campaigns_for_user(event.sender_id)
            if rows:
                await event.edit(
                    "<b>Your campaigns</b>\n\nTap one to open it.",
                    buttons=kb.campaigns_list_keyboard(rows),
                    parse_mode="html",
                )
            else:
                await event.edit(
                    "You have no campaigns yet.\n"
                    "<code>/newcampaign Name</code> to start one.",
                    parse_mode="html",
                )
            return

        if action == "new":
            await event.answer("Use /newcampaign Name")
            return

        # camp:<campaign_id> opens a campaign directly.
        if action.isdigit():
            campaign = await db.get_campaign(int(action))
            if campaign is None:
                await event.answer("That campaign is gone.", alert=True)
                return
            if not await db.is_member(campaign["id"], event.sender_id):
                await event.answer("You are not in that campaign.", alert=True)
                return
            is_dm = await db.is_dm(campaign["id"], event.sender_id)
            active = await db.active_session(campaign["id"])
            await event.edit(
                campaign_summary(campaign, is_dm, bool(active)),
                buttons=kb.campaign_keyboard(campaign["id"], is_dm, bool(active)),
                parse_mode="html",
            )
            return

        campaign_id = int(parts[2]) if len(parts) > 2 else 0
        campaign = await db.get_campaign(campaign_id)
        if campaign is None:
            await event.answer("That campaign is gone.", alert=True)
            return
        is_member = await db.is_member(campaign_id, event.sender_id)

        if action == "roster":
            members = await db.roster(campaign_id)
            lines = [
                f"<b>\U0001f91d {safe(campaign['name'])}</b> <i>({len(members)} members)</i>",
                "",
            ]
            for member in members:
                tag = " <i>DM</i>" if member["role"] == "dm" else ""
                lines.append(
                    f"• {display_name(member)}{tag} \u2014 since {relative(member['joined_at'])}"
                )
            await event.edit(
                "\n".join(lines),
                buttons=kb.roster_keyboard(members, campaign_id) if is_member else None,
                parse_mode="html",
            )
            return

        if action == "pending":
            if campaign["dm_id"] != event.sender_id:
                await event.answer("Only the DM can see that.", alert=True)
                return
            requests = await db.pending_requests(campaign_id)
            if not requests:
                await event.answer("No pending requests.", alert=True)
                return
            lines = ["<b>\U0001f6e1 Pending joins</b>", ""]
            buttons = []
            for request in requests:
                user = await db.get_user(request["user_id"])
                lines.append(f"• {display_name(user)} \u2014 {relative(request['created_at'])}")
                buttons.append(kb.join_request_keyboard(request["id"]))
            await event.edit("\n".join(lines), buttons=buttons, parse_mode="html")
            return

        if action == "invite":
            await event.answer(
                f"Invite code: {campaign['invite_code']}\n"
                f"They send: /join {campaign['invite_code']}",
                alert=True,
            )
            return

        if action in {"start", "end"}:
            if campaign["dm_id"] != event.sender_id:
                await event.answer("Only the DM can do that.", alert=True)
                return
            await event.answer(
                f"Use /{'endsession' if action == 'end' else 'startsession'} so it is recorded."
            )
            return

        await event.answer("Nothing to do here.", alert=True)
