"""The bot's own documentation, as Telegram HTML.

The /help pages and the /tutorial walkthrough live here rather than in the
handlers, so the Telegram bot and the MCP server read the same text - one
of them renders it with keyboards, the other converts it with
dndbot.mcp.render.
"""

from __future__ import annotations

HELP_DICE = (
    "<b>\U0001f3b2 Dice</b>\n"
    "<code>/roll 2d6+3</code> \u2014 any notation\n"
    "<code>/roll 4d6kh3</code> \u2014 roll four, keep the best three\n"
    "<code>/adv</code> / <code>/dis</code> \u2014 advantage roll (add your own modifier)\n"
    "<code>/init</code> \u2014 initiative for the whole party\n"
    "<i>Also: 2d20kl1 (keep lowest), 4d6r&lt;=1 (reroll ones), 2d6!&gt;=6 (exploding).</i>"
)

HELP_CAMPAIGN = (
    "<b>\U0001f3dd Campaigns</b>\n"
    "<code>/newcampaign Name | blurb</code> \u2014 become the DM\n"
    "<code>/join ABC123</code> \u2014 ask to join (the DM approves)\n"
    "<code>/campaigns</code> \u2014 everything you are in\n"
    "<code>/select</code> \u2014 switch your active campaign\n"
    "<code>/roster</code> \u2014 who is in the party\n"
    "<code>/leave</code> \u2014 walk away"
)

HELP_SESSION = (
    "<b>\U0001f5c3\ufe0f Sessions</b>\n"
    "<code>/startsession</code> \u2014 DM only\n"
    "<code>/checkin</code> \u2014 sit at the table\n"
    "<code>/who</code> \u2014 who is here\n"
    "<code>/endsession [notes]</code> \u2014 DM only, records the length"
)

HELP_CHARACTER = (
    "<b>\U0001f9d9 Characters</b>\n"
    "<code>/newchar</code> \u2014 guided creation, six questions\n"
    "<code>/char</code> \u2014 your sheet (or <code>/char Name</code>)\n"
    "<code>/hp -7</code> / <code>/hp +3</code> \u2014 damage and healing\n"
    "<code>/sethp 38</code> / <code>/sethp 38 52</code> \u2014 set current / max\n"
    "<code>/levelup</code> / <code>/level 5</code> \u2014 levels\n"
    "<code>/xp 1250</code> \u2014 experience\n"
    "<code>/set ac 16</code> \u2014 or speed, init, gold, str\u2026cha\n"
    "<code>/note text</code> \u2014 sticky note on the sheet\n"
    "<code>/party</code> \u2014 the whole party's vitals\n"
    "<code>/switch Name</code> \u2014 change active character"
)

HELP_SRD = (
    "<b>\U0001f5c2\ufe0f SRD lookup</b>\n"
    "<code>/monster goblin</code> \u2014 full stat block\n"
    "<code>/spell fireball</code> \u2014 casting, damage, classes\n"
    "<code>/item adamantine armor</code> \u2014 magic items\n"
    "<code>/equipment longsword</code> \u2014 mundane gear\n"
    "<code>/rule long rest</code> \u2014 rules reference\n"
    "<code>/class wizard</code> \u2022 <code>/race elf</code> \u2022 <code>/condition blinded</code>\n"
    "<code>/search anything</code> \u2014 search everything at once\n"
    "<code>/randmonster 2</code> \u2014 random monster by CR (or <code>/randmonster 1/4</code>)\n"
    "<code>/randspell</code> \u2014 random spell\n"
    "<i>Fuzzy matching: <code>/monster ancient red drgn</code> finds the red dragon.</i>"
)

HELP_ENCOUNTERS = (
    "<b>\U0001f91d Encounters (DM only)</b>\n"
    "<code>/newencounter Name | hidden</code> \u2014 prepare a fight\n"
    "<code>/addmonster goblin 3</code> \u2014 pull monsters from the SRD\n"
    "<code>/ms 1 ac 16</code> \u2014 tailor stats (also hp, dmg, atk, count, hide)\n"
    "<code>/encounters</code> \u2022 <code>/enc Name</code> \u2022 "
    "<code>/hpmode hidden</code>\n"
    "\n"
    "<b>In session</b>\n"
    "<code>/fight Name</code> \u2014 the DM starts it, everyone is notified\n"
    "<code>/hit 1 2d6+3</code> \u2014 damage (rolls dice, remembers who did it)\n"
    "<code>/kill 2</code> \u2014 DM only, drops a combatant outright\n"
    "<code>/heal 1 10</code> \u2022 <code>/fight</code> \u2022 <code>/endfight</code>\n"
    "<i>With <b>hidden</b> HP, players see the damage they dealt but never the "
    "monster's numbers. <code>/ms N hide</code> keeps one out of the list entirely; "
    "<code>/ms N nohp</code> makes one fall only to /kill. Deaths and reveals are "
    "announced to the party.</i>"
)

WELCOME = (
    "\U0001f3df\ufe0f <b>D&amp;D Helper</b>\n\n"
    "Dice, campaign sessions, character sheets and the whole SRD at your table.\n\n"
    "Start here: <code>/newcampaign Your Campaign</code> and share the invite code, "
    "or tap a button below.\n\n"
    "<i>New? Send /tutorial for a guided walkthrough.</i>"
)

# Topic -> page for /help, including the short aliases a player may type.
HELP_PAGES: dict[str, str] = {
    "dice": HELP_DICE,
    "campaign": HELP_CAMPAIGN,
    "campaigns": HELP_CAMPAIGN,
    "session": HELP_SESSION,
    "sessions": HELP_SESSION,
    "char": HELP_CHARACTER,
    "character": HELP_CHARACTER,
    "characters": HELP_CHARACTER,
    "srd": HELP_SRD,
    "encounter": HELP_ENCOUNTERS,
    "encounters": HELP_ENCOUNTERS,
}

# The names worth quoting in prose (the aliases above are conveniences).
HELP_TOPICS: tuple[str, ...] = (
    "dice",
    "campaign",
    "session",
    "character",
    "srd",
    "encounter",
)

TUTORIAL_PAGES: list[tuple[str, str]] = [
    (
        "start",
        "\U0001f409 <b>Welcome to the D&amp;D helper</b>\n\n"
        "I run your table: dice, campaign sessions, character sheets and the "
        "whole SRD.\n\n"
        "<b>The four things I do</b>\n"
        "\U0001f3b2 Roll dice, any notation you like\n"
        "\U0001f3dd Campaigns and sessions with DM-approved joins\n"
        "\U0001f9d9 Track characters and hit points\n"
        "\U0001f5c2\ufe0f Look up any monster, spell, item or rule\n\n"
        "<b>Where to go next</b>\n"
        "If you are the DM, read <b>Setting up</b> first.\n"
        "If you were invited to a game, read <b>Joining a game</b>.\n\n"
        "<i>Tap the arrows below, or send /tutorial again to restart.</i>",
    ),
    (
        "setup",
        "\U0001f3f0 <b>Setting up (DM only)</b>\n\n"
        "<b>1.</b> Create your campaign\n"
        "<code>/newcampaign The Amber Court</code>\n"
        "Add a blurb after a <code>|</code> if you like - it shows on the "
        "campaign card.\n\n"
        "<b>2.</b> Copy the 6-character invite code I give you and send it to "
        "your players.\n\n"
        "<b>3.</b> Everyone sends <code>/join ABC123</code>. You get an "
        "<b>Approve / Deny</b> button for each request - you control the "
        "roster.\n\n"
        "<b>4.</b> On game night:\n"
        "<code>/startsession Into the Marsh</code>\n"
        "Players send <code>/checkin</code> to sit at the table.\n\n"
        "<b>5.</b> When you are finished:\n"
        "<code>/endsession The dragon fled.</code>\n"
        "I record how long it ran and note what happened.\n\n"
        "<i>Try it now: /newcampaign Test Campaign</i>",
    ),
    (
        "join",
        "\U0001f4e5 <b>Joining a game</b>\n\n"
        "<b>1.</b> Your DM sends you a 6-character code, like <code>ABC123</code>.\n\n"
        "<b>2.</b> Send <code>/join ABC123</code>. That sends the DM a request.\n\n"
        "<b>3.</b> The DM taps <b>Approve</b>. I tell you the moment you are in "
        "- no need to ask again.\n\n"
        "<b>4.</b> Make your character: <code>/newchar</code>\n\n"
        "<b>5.</b> On game night send <code>/checkin</code> so the DM knows you "
        "are there.\n\n"
        "<b>Handy commands</b>\n"
        "<code>/who</code> - who is at the table\n"
        "<code>/party</code> - everyone's hit points at a glance\n"
        "<code>/init</code> - roll initiative for the party\n"
        "<code>/leave</code> - leave the game\n\n"
        "<i>Group chat? All commands work there too. Invite me to the group.</i>",
    ),
    (
        "dice",
        "\U0001f3b2 <b>Rolling dice</b>\n\n"
        "<b>Type it</b>\n"
        "<code>/roll 2d6+3</code> - two d6 plus three\n"
        "<code>/roll d20</code> - one d20\n"
        "<code>/roll 1d10+1d4+2</code> - mix dice freely\n"
        "\n"
        "<b>The fancy ones</b>\n"
        "<code>/roll 4d6kh3</code> - roll four d6, keep the best three\n"
        "<code>/roll 2d20kl1</code> - keep the lowest\n"
        "<code>/roll 4d6r&lt;=1</code> - reroll every 1 until it isn't one\n"
        "<code>/roll 2d6!&gt;=6</code> - exploding dice\n"
        "\n"
        "<b>Advantage</b>\n"
        "<code>/adv</code> or <code>/adv +4</code>\n"
        "<code>/dis</code> or <code>/dis +4</code>\n"
        "\n"
        "<b>The dice pad</b>\n"
        "Send <code>/roll</code> with no argument and I show buttons - tap dice "
        "and operators to build the sum, then hit Roll.\n\n"
        "I show every individual die, mark the discarded ones, and call out "
        "natural 20s and 1s.",
    ),
    (
        "character",
        "\U0001f9d9 <b>Character sheets</b>\n\n"
        "<b>Making one</b>\n"
        "<code>/newchar</code> asks six questions:\n"
        "name \u2192 class and subclass \u2192 race and background \u2192 level "
        "\u2192 ability scores \u2192 AC, HP and speed.\n"
        "\n"
        "<b>Reading it</b>\n"
        "<code>/char</code> - your sheet, with an HP bar and ability modifiers\n"
        "<code>/party</code> - the whole party\n"
        "<code>/char Sylra</code> - someone else's sheet\n"
        "\n"
        "<b>Keeping it current</b>\n"
        "<code>/hp -7</code> - take damage (temp HP and going down are handled)\n"
        "<code>/hp +3 rest</code> - heal, with an optional reason\n"
        "<code>/sethp 38 52</code> - set current and max\n"
        "<code>/levelup</code> - next level\n"
        "<code>/xp 1250</code> - set experience\n"
        "<code>/set ac 16</code> - also speed, init, gold, str, dex, con, int, "
        "wis, cha\n"
        "<code>/note my favourite brew</code> - a note on the sheet\n"
        "<code>/switch Name</code> - if you play more than one\n"
        "\n"
        "<i>Initiative bonuses come from your DEX automatically.</i>",
    ),
    (
        "srd",
        "\U0001f4da <b>Looking things up</b>\n\n"
        "Everything in the SRD 5.2.1 (2014) - 334 monsters, 319 spells, "
        "362 magic items and more.\n"
        "\n"
        "<b>By name</b>\n"
        "<code>/monster goblin</code> \u2014 full stat block\n"
        "<code>/spell fireball</code> \u2014 casting time, damage, classes\n"
        "<code>/item adamantine armor</code>\n"
        "<code>/equipment longsword</code>\n"
        "<code>/rule long rest</code>\n"
        "<code>/class wizard</code> \u2022 <code>/race elf</code> \u2022 "
        "<code>/condition blinded</code>\n"
        "\n"
        "<b>Not sure of the name?</b>\n"
        "<code>/search green dragon</code> searches everything at once\n"
        "<code>/randmonster 2</code> - random monster of that CR\n"
        "<code>/randmonster 1/4</code> - a range\n"
        "<code>/randspell</code>\n"
        "\n"
        "<b>Typos are fine</b>\n"
        "I match loosely, so <code>/monster ancient red drgn</code> still finds "
        "the red dragon.\n\n"
        "Long entries are split across pages with \u25c0 \u25b6 buttons.\n"
        "You can also browse by tapping <b>SRD</b> in /start.",
    ),
    (
        "encounters",
        "\U0001f91d <b>Encounters (DM only)</b>\n\n"
        "Prepare a fight ahead of time, then drop it into the session.\n\n"
        "<b>1.</b> Create it\n"
        "<code>/newencounter Ambush at the ford | hidden</code>\n"
        "By default players see hit points. Add <b>hidden</b> and they will "
        "not \u2014 they will only see the damage dealt and by whom.\n\n"
        "<b>2.</b> Fill it with monsters (from the SRD)\n"
        "<code>/addmonster goblin 3</code>\n"
        "<code>/addmonster ogre</code>\n"
        "<code>/addmonster goblin x5</code>\n\n"
        "<b>3.</b> Tailor the stats \u2014 the fight is yours, not the manual\n"
        "<code>/ms 1 ac 16</code> \u2022 <code>/ms 1 hp 30</code> \u2022 "
        "<code>/ms 1 dmg 2d6+4</code> \u2022 <code>/ms 1 atk +5</code>\n"
        "<code>/ms 2 name Dire Ogre</code> \u2022 <code>/ms 2 note breathes fire</code>\n"
        "<code>/ms 3 hide</code> \u2014 a surprise the table cannot see\n"
        "<code>/ms 4 nohp</code> \u2014 damage piles up but it will not fall\n"
        "<code>/ms 5 boss</code> \u2014 crown on its name, everyone sees it\n\n"
        "<b>4.</b> Start it in the session\n"
        "<code>/fight Ambush at the ford</code>\n"
        "I announce it to everyone and track each combatant separately.\n\n"
        "<b>5.</b> During the fight\n"
        "<code>/hit 1 2d6+3</code> \u2014 roll and damage the first goblin\n"
        "<code>/hit ogre 11</code> \u2014 name one instead\n"
        "<code>/kill wraith</code> \u2014 DM only, drops it outright\n"
        "<code>/heal 2 10</code> \u2022 <code>/fight</code> \u2014 check the state\n"
        "<code>/endfight</code>\n\n"
        "<b>What the table is told</b>\n"
        "\u2022 When something dies I message everyone \u2014 whoever landed it.\n"
        "\u2022 Hidden monsters are simply not listed; they never see a count,\n"
        "  and cannot be hit by number until revealed.\n"
        "\u2022 Every name carries the number to use: <code>/hit 1 ...</code>.\n"
        "\u2022 Numbers are the standing order, so they close up as things die.\n"
        "\u2022 When the fight ends I tell everyone how it went.\n"
        "\u2022 <code>/ms 3 show</code> mid-fight reveals one and I announce it.\n"
        "\u2022 A no-HP monster shows only the damage dealt. Players get no\n"
        "  hint of its condition until you <code>/kill</code> it.\n\n"
        "<i>Editing the encounter never disturbs a fight already running.</i>\n\n"
        "<b>Other commands</b>\n"
        "<code>/encounters</code> \u2014 everything you have prepared\n"
        "<code>/enc Name</code> \u2014 open one\n"
        "<code>/hpmode hidden</code> \u2014 switch visibility between fights\n"
        "<code>/delenc Name</code> \u2014 remove it",
    ),
    (
        "groups",
        "\U0001f465 <b>Groups and housekeeping</b>\n\n"
        "<b>Using me in a group</b>\n"
        "Add me to the group chat. If the group name contains your campaign "
        "name, I use it automatically - otherwise send the campaign name once "
        "to select it.\n"
        "\n"
        "<b>Multiple games</b>\n"
        "<code>/campaigns</code> - everything you are in\n"
        "<code>/select</code> - switch which one you are working in\n"
        "\n"
        "<b>The DM's tools</b>\n"
        "<code>/roster</code> - players with their characters and HP\n"
        "<code>/pending</code> - who is waiting for approval\n"
        "<code>/announce Bring dice</code> - private message to the whole table\n"
        "<code>/session</code> - recent sessions and their length\n"
        "\n"
        "<b>Commands list</b>\n"
        "Type <code>/</code> in the chat to see everything. "
        "<code>/help dice</code>, <code>/help character</code>, "
        "<code>/help srd</code> and friends.\n\n"
        "<b>That's it</b>\n"
        "Send <code>/start</code> any time for the menus, or "
        "<code>/tutorial</code> to read this again.",
    ),
]

PAGE_KEYS = [key for key, _ in TUTORIAL_PAGES]
ALIASES = {"dm": "setup", "campaign": "setup", "character": "character",
           "characters": "character", "dice": "dice", "srd": "srd", "join": "join",
           "groups": "groups", "help": "start", "fight": "encounters",
           "combat": "encounters", "monsters": "encounters"}
