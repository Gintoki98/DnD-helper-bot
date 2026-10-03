"""Campaign and session tools: the DM's panel, without the buttons.

Every decision runs through ``dndbot/services.py``, the same flows the
Telegram handlers call. What differs is presentation (Markdown instead of
HTML) and the absence of Telethon-only side effects: nothing is broadcast,
edited or DMed from here, so a session opened with this tool does not ping
the players - they see it when they ask (/session, /campaign).
"""

from __future__ import annotations

from inspect import cleandoc

from mcp.server.mcpserver.exceptions import ToolError

from .. import services
from ..common import NoCampaign, duration, plain_name
from ..storage import db
from . import identity, inputs
from .render import esc_md, strip, to_markdown


async def list_campaigns(user_id: int | None = None) -> str:
    """Every campaign you belong to, with role, invite code and live marker.
    Telegram: ``/campaigns``."""
    uid = identity.actor(user_id)
    rows = await db.campaigns_for_user(uid)
    if not rows:
        raise ToolError(
            "You are not in any campaign yet. Create one with create_campaign, "
            'or join a friend\'s with join_campaign(code="ABC123").'
        )
    return to_markdown(await services.campaigns_list_text(rows))


async def create_campaign(
    name: str, description: str = "", user_id: int | None = None
) -> str:
    """Start a campaign and become its DM. Telegram: ``/newcampaign Name | Blurb``
    (here name and description are separate arguments).

    The creator is the DM and the campaign becomes their active one; the
    reply carries the invite code players ask for.
    """
    uid = identity.actor(user_id)
    try:
        campaign = await services.create_campaign(
            uid, name.strip(), (description or "").strip()
        )
    except services.Refused as exc:
        raise ToolError(strip(exc)) from exc
    return (
        to_markdown(services.campaign_summary(campaign, True, False))
        + "\n\nShare the code so players can request to join with "
        f'join_campaign(code="{campaign["invite_code"]}") and wait for your '
        "approval.\n\nReview the queue with list_join_requests and decide each "
        "with resolve_join, then open a session with start_session."
    )


async def join_campaign(code: str | None = None, user_id: int | None = None) -> str:
    """Ask to join a campaign by invite code. Telegram: ``/join ABC123``.

    Without ``code`` it lists the campaigns you are already in. An unknown
    code is an error you can correct - ask the DM for the 6-character one.
    """
    uid = identity.actor(user_id)
    term = (code or "").strip()
    if not term:
        mine = await db.campaigns_for_user(uid)
        if not mine:
            raise ToolError(
                'You are in no campaign yet. Send the invite code the DM gave '
                'you: join_campaign(code="ABC123").'
            )
        return "**You are already in:**\n\n" + to_markdown(
            services.campaigns_pick_text(mine)
        )

    try:
        outcome = await services.request_join(uid, term)
    except NoCampaign as exc:
        raise ToolError(strip(exc)) from exc

    row = outcome.campaign
    if outcome.already_member:
        return f"You are already in **{esc_md(row['name'])}**."
    return (
        f"\U0001f4e4 Requested to join **{esc_md(row['name'])}**.\n"
        "The request is pending: the DM reviews it from Telegram (/pending) or "
        "here with list_join_requests, and you are a member once they approve."
    )


async def list_join_requests(
    user_id: int | None = None, campaign: str | None = None
) -> str:
    """Who asked to join, as the DM, with the id ``resolve_join`` expects.
    Telegram: ``/pending`` (the bot shows Approve/Deny buttons instead)."""
    _uid, row = await inputs.dm(user_id, campaign)
    requests = await db.pending_requests(row["id"])
    if not requests:
        return "No one is waiting to join."
    body = to_markdown(
        await services.pending_requests_text(row, requests, with_ids=True)
    )
    return (
        body
        + "\n\nDecide each one with "
        "resolve_join(request_id=<id>, approve=true|false)."
    )


async def resolve_join(
    request_id: int, approve: bool, user_id: int | None = None
) -> str:
    """Approve or deny a pending join request. Telegram: the Approve/Deny
    buttons on ``/pending``.

    Only the DM who owns the campaign can decide; the applicant is only told
    by the Telegram bot, so follow up there if they are waiting.
    """
    uid = identity.actor(user_id)
    try:
        request, campaign, applicant = await services.join_request_context(
            request_id, uid
        )
    except services.Refused as exc:
        raise ToolError(strip(exc)) from exc

    who = plain_name(applicant)
    if approve:
        await services.approve_join(request, campaign, uid)
        return (
            f"\u2705 **{esc_md(who)}** joined **{esc_md(campaign['name'])}**. "
            "They can start a sheet with create_character."
        )
    await services.deny_join(request, uid)
    return f"\u274c Request from **{esc_md(who)}** declined."


async def select_campaign(name: str | None = None, user_id: int | None = None) -> str:
    """Make a campaign your active one - the one the other tools use when
    ``campaign`` is left out. Telegram: ``/select [Name]`` (no name lists
    your campaigns)."""
    uid = identity.actor(user_id)
    rows = await db.campaigns_for_user(uid)
    if not rows:
        raise ToolError(
            "You are not in a campaign yet. Create one with create_campaign or "
            "join with join_campaign."
        )
    if name:
        try:
            match = await services.select_campaign(uid, name)
        except services.Refused as exc:
            raise ToolError(strip(exc)) from exc
        return f"Working on **{esc_md(match['name'])}** now."
    return (
        "**Pick your active campaign**\n\n"
        + to_markdown(services.campaigns_pick_text(rows))
        + "\n\nCall select_campaign(name=...) to switch."
    )


async def get_roster(
    user_id: int | None = None, campaign: str | None = None
) -> str:
    """Everyone in the campaign and the sheet each one is playing.
    Telegram: ``/roster``."""
    _uid, row = await inputs.member(user_id, campaign)
    members = await db.roster(row["id"])
    if not members:
        raise ToolError(f"Nobody has joined {row['name']} yet.")
    return to_markdown(await services.roster_text(row, members))


async def campaign_info(
    user_id: int | None = None, campaign: str | None = None
) -> str:
    """The campaign as it stands: your role, whether a session is live, and
    its invite code. Telegram: ``/campaign``."""
    uid, row = await inputs.member(user_id, campaign)
    is_dm = await db.is_dm(row["id"], uid)
    active = await db.active_session(row["id"])
    return to_markdown(services.campaign_summary(row, is_dm, bool(active)))


async def leave_campaign(
    user_id: int | None = None, campaign: str | None = None
) -> str:
    """Leave a campaign. Telegram: ``/leave`` - the DM never can, and the
    characters you made stay saved in case you come back."""
    uid = identity.actor(user_id)
    row = await inputs.campaign(uid, campaign)
    try:
        await services.leave_campaign(uid, row)
    except services.Refused as exc:
        raise ToolError(strip(exc)) from exc
    return (
        f"You left **{esc_md(row['name'])}**. "
        "Your characters stay saved in case you return."
    )


async def start_session(
    title: str = "", user_id: int | None = None, campaign: str | None = None
) -> str:
    """Open a session for the party. Telegram: ``/startsession Into the Marsh``
    (DM only). One session can run at a time; the players are not pinged from
    here."""
    uid, row = await inputs.dm(user_id, campaign)
    try:
        await services.start_session(row, uid, title)
    except services.Refused as exc:
        raise ToolError(strip(exc)) from exc
    party = await db.party(row["id"])
    return to_markdown(services.session_started_text(row, title, party))


async def end_session(
    notes: str = "", user_id: int | None = None, campaign: str | None = None
) -> str:
    """Close the running session. Telegram: ``/endsession [notes]`` (DM only)."""
    uid, row = await inputs.dm(user_id, campaign)
    try:
        active, elapsed = await services.end_session(row, notes)
    except services.Refused as exc:
        raise ToolError(strip(exc)) from exc
    length = duration(elapsed)
    return (
        f"\U0001f6d1 Session **#{active['id']}** ended after {length}."
        + (f"\n\n*Notes: {esc_md(notes)}*" if notes else "")
    )


async def session_history(
    user_id: int | None = None, campaign: str | None = None
) -> str:
    """The last few sessions: status, length and title. Telegram: ``/session``."""
    _uid, row = await inputs.member(user_id, campaign)
    history = await db.recent_sessions(row["id"], 6)
    if not history:
        raise ToolError(
            f"No sessions yet in {row['name']}. The DM opens one with start_session."
        )
    return to_markdown(services.session_history_text(row, history))


async def checkin(
    user_id: int | None = None, campaign: str | None = None
) -> str:
    """Sit down at the running session so the DM sees you at the table.
    Telegram: ``/checkin`` - there must be a live session."""
    uid, row = await inputs.member(user_id, campaign)
    try:
        await services.checkin(uid, row)
    except services.Refused as exc:
        raise ToolError(strip(exc)) from exc
    return "\u2705 Checked in. Have fun."


async def who_is_here(
    user_id: int | None = None, campaign: str | None = None
) -> str:
    """Who is at the table right now - and your own seat, which this also
    takes. Without a live session it falls back to the roster.
    Telegram: ``/who``."""
    uid, row = await inputs.member(user_id, campaign)
    active = await db.active_session(row["id"])
    if not active:
        roster = await db.roster(row["id"])
        return to_markdown(services.roster_brief_text(row, roster))
    await db.mark_attendance(active["id"], uid, "online")
    present = await db.attendance(active["id"])
    return to_markdown(services.at_table_text(present))


def register(mcp) -> None:
    """Register this module's tools on the server (cleaned docstring first)."""
    for tool in (
        list_campaigns,
        create_campaign,
        join_campaign,
        list_join_requests,
        resolve_join,
        select_campaign,
        get_roster,
        campaign_info,
        leave_campaign,
        start_session,
        end_session,
        session_history,
        checkin,
        who_is_here,
    ):
        mcp.add_tool(tool, description=cleandoc(tool.__doc__ or ""))
