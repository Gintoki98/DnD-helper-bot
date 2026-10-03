"""Campaigns, the DM's join approvals, and session management."""

from __future__ import annotations

import asyncio

from telethon import events

from .. import keyboards as kb, services
from ..common import (
    NoCampaign,
    NotAMember,
    command_argument,
    display_name,
    dm_campaign,
    duration,
    ensure_member,
    member_campaign,
    plain_name,
    relative,
    resolve_campaign,
    safe,
)
from ..services import campaign_summary, session_started_text
from ..storage import db


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


async def _approve_join(event, request, campaign, who: str) -> None:
    """Record an approval: update the roster, the prompt, and the applicant."""
    await services.approve_join(request, campaign, event.sender_id)
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
        pass  # applicant unreachable; they can still /join later


async def _decline_join(event, request, campaign, who: str) -> None:
    """Record a rejection: update the prompt and tell the applicant."""
    await services.deny_join(request, event.sender_id)
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
        pass  # applicant unreachable


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
        try:
            campaign = await services.create_campaign(event.sender_id, name, description)
        except services.Refused as exc:
            await event.reply(str(exc), parse_mode="html")
            return

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
            names = services.campaigns_pick_text(mine)
            await event.reply(f"You are already in:\n\n{names}", parse_mode="html")
            return

        try:
            outcome = await services.request_join(event.sender_id, term)
        except NoCampaign as exc:
            await event.reply(str(exc), parse_mode="html")
            return

        row = outcome.campaign
        if outcome.already_member:
            await event.reply(
                f"You are already in <b>{safe(row['name'])}</b>.",
                buttons=kb.campaign_keyboard(
                    row["id"], await db.is_dm(row["id"], event.sender_id), False
                ),
                parse_mode="html",
            )
            return

        if outcome.is_new:
            await tell_dm(client, row, outcome.request, display_name(event.sender))
        await event.reply(
            f"\U0001f4e4 Requested to join <b>{safe(row['name'])}</b>.\n"
            "The DM has been notified - I will message you the moment you are in.",
            parse_mode="html",
        )

    @client.on(events.NewMessage(pattern=r"^/pending(?:@[\w_]+)?$"))
    async def pending_requests(event: events.NewMessage.Event) -> None:
        campaign = await dm_campaign(event)
        if campaign is None:
            return

        requests = await db.pending_requests(campaign["id"])
        if not requests:
            await event.reply("No one is waiting to join.", parse_mode="html")
            return

        buttons = [kb.join_request_keyboard(request["id"]) for request in requests]
        await event.reply(
            await services.pending_requests_text(campaign, requests),
            buttons=buttons,
            parse_mode="html",
        )

    @client.on(events.NewMessage(pattern=r"^/(?:campaign|camp)(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def campaign_panel(event: events.NewMessage.Event) -> None:
        campaign = await member_campaign(event, command_argument(event))
        if campaign is None:
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
        await event.reply(
            await services.campaigns_list_text(rows),
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
            try:
                match = await services.select_campaign(event.sender_id, term)
            except services.Refused as exc:
                await event.reply(str(exc), parse_mode="html")
                return
            await event.reply(
                f"Working on <b>{safe(match['name'])}</b> now.",
                buttons=kb.campaign_keyboard(
                    match["id"], await db.is_dm(match["id"], event.sender_id), False
                ),
                parse_mode="html",
            )
            return
        await event.reply(
            f"<b>Pick your active campaign</b>\n\n{services.campaigns_pick_text(rows)}",
            buttons=kb.campaigns_list_keyboard(rows),
            parse_mode="html",
        )

    @client.on(events.NewMessage(pattern=r"^/roster(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def roster_command(event: events.NewMessage.Event) -> None:
        campaign = await member_campaign(event, command_argument(event))
        if campaign is None:
            return

        members = await db.roster(campaign["id"])
        if not members:
            await event.reply(f"Nobody has joined <b>{safe(campaign['name'])}</b> yet.", parse_mode="html")
            return
        await event.reply(
            await services.roster_text(campaign, members),
            buttons=kb.roster_keyboard(members, campaign["id"]),
            parse_mode="html",
        )

    @client.on(events.NewMessage(pattern=r"^/leave(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def leave_campaign(event: events.NewMessage.Event) -> None:
        term = command_argument(event)
        try:
            campaign = await resolve_campaign(event, term)
            await services.leave_campaign(event.sender_id, campaign)
        except (NoCampaign, services.Refused) as exc:
            await event.reply(str(exc), parse_mode="html")
            return
        await event.reply(
            f"You left <b>{safe(campaign['name'])}</b>. Your characters stay saved in case you return.",
            parse_mode="html",
        )

    # ------------------------------------------------------------------
    # sessions
    # ------------------------------------------------------------------
    @client.on(events.NewMessage(pattern=r"^/startsession(?:@[\w_]+)?(?:\s+(.*))?$"))
    async def start_session(event: events.NewMessage.Event) -> None:
        campaign = await dm_campaign(event)
        if campaign is None:
            return

        title = command_argument(event)
        try:
            await services.start_session(campaign, event.sender_id, title)
        except services.Refused as exc:
            await event.reply(str(exc), parse_mode="html")
            return

        party = await db.party(campaign["id"])
        await event.reply(
            session_started_text(campaign, title, party),
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
        campaign = await dm_campaign(event)
        if campaign is None:
            return

        notes = command_argument(event)
        try:
            active, elapsed = await services.end_session(campaign, notes)
        except services.Refused as exc:
            await event.reply(str(exc), parse_mode="html")
            return

        length = duration(elapsed)
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
        campaign = await member_campaign(event)
        if campaign is None:
            return

        history = await db.recent_sessions(campaign["id"], 6)
        if not history:
            await event.reply(
                f"<b>{safe(campaign['name'])}</b>\n\nNo sessions yet. The DM starts one with "
                "<code>/startsession</code>.",
                parse_mode="html",
            )
            return

        await event.reply(services.session_history_text(campaign, history), parse_mode="html")

    @client.on(events.NewMessage(pattern=r"^/who(?:@[\w_]+)?$"))
    async def who_is_here(event: events.NewMessage.Event) -> None:
        campaign = await member_campaign(event)
        if campaign is None:
            return

        active = await db.active_session(campaign["id"])
        if not active:
            roster = await db.roster(campaign["id"])
            await event.reply(
                services.roster_brief_text(campaign, roster), parse_mode="html"
            )
            return

        await db.mark_attendance(active["id"], event.sender_id, "online")
        present = await db.attendance(active["id"])
        await event.reply(services.at_table_text(present), parse_mode="html")

    @client.on(events.NewMessage(pattern=r"^/checkin(?:@[\w_]+)?$"))
    async def checkin(event: events.NewMessage.Event) -> None:
        try:
            campaign = await resolve_campaign(event)
            membership = await ensure_member(campaign, event.sender_id)
            await services.checkin(event.sender_id, campaign)
        except (NoCampaign, NotAMember, services.Refused) as exc:
            await event.reply(str(exc), parse_mode="html")
            return

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
        campaign = await dm_campaign(event)
        if campaign is None:
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
        try:
            request, campaign, applicant = await services.join_request_context(
                int(request_id), event.sender_id
            )
        except services.Refused as exc:
            await event.answer(str(exc), alert=True)
            return

        who = plain_name(applicant)

        if decision == "y":
            await _approve_join(event, request, campaign, who)
        else:
            await _decline_join(event, request, campaign, who)

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
