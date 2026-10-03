# D&D Helper Bot

A Telegram bot for running a D&D 5e table: dice, campaigns and sessions with
DM-approved joins, character sheets, and the whole SRD for monsters, spells,
items and rules.

Built on [Telethon](https://docs.telethon.dev/) (required) with SQLite
persistence and the public [dnd5eapi](https://www.dnd5eapi.co) SRD API.

---

## Setup

### 1. Get Telegram credentials

1. Message [@BotFather](https://t.me/BotFather) → `/newbot` → keep the token it
   gives you. The bot signs in with this token, so there is no phone-number
   prompt and no interactive login step.
2. Go to <https://my.telegram.org> → **API development tools** and create an
   app. You need both the **API ID** and the **API hash**.

### 2. Configure

```bash
cp .env.example .env
```

Fill in `.env`:

```ini
API_ID=21742289
API_HASH=your_api_hash
BOT_TOKEN=1234567890:AAYourTokenFromBotFather
ADMIN_ID=123456789          # optional, from @userinfobot
MCP_USER_ID=123456789       # optional, default identity for the MCP server
```

`.env` holds secrets — keep it out of version control (it is already in
`.gitignore`).

### 3. Install and run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python bot.py
```

On first run the session is created in `dnd_helper.session` and the bot starts
immediately. Later runs reuse it, so startup is silent. To switch accounts or
bot tokens, delete that file.

On startup it publishes 48 commands to the Telegram slash menu, opens the
database and warms the SRD caches.

---

## Playing

Start with **`/tutorial`** — a seven-page walkthrough covering dice, setting
up as DM, joining a game, character sheets, SRD lookups and groups. Page
through it with the ◀ ▶ buttons, or jump straight to one:

```
/tutorial          # page 1
/tutorial dice     # or: setup, join, character, srd, groups
```

### Dice

| Command | What it does |
| --- | --- |
| `/roll 2d6+3` | any notation, with a full breakdown |
| `/roll 4d6kh3` | roll four d6, keep the highest three |
| `/roll 2d20kl1` | keep the lowest |
| `/roll 4d6r<=1` | reroll each 1 until it isn't one |
| `/roll 2d6!>=6` | exploding dice |
| `/adv`, `/adv +4` | advantage roll, optionally with a modifier |
| `/dis`, `/dis +4` | disadvantage roll |
| `/init` | initiative for the whole party |

`/start` also has an inline **dice pad** — tap dice and operators to build an
expression, then roll. Results get a *Reroll*, *+1* and *Monster* button.

Multiple terms work: `/roll 1d10+1d4+2`. Natural 20s and 1s are called out.

### Campaigns and sessions

```
/newcampaign The Amber Court | A haunted road north
/join ABC123          # asks the DM, who approves or denies
/pending              # DM: who is waiting
/campaigns            # everything you are in
/select               # switch your active campaign
/roster               # the party, with their characters and HP
/leave
```

Then, during play:

```
/startsession Into the Marsh     # DM only
/checkin                         # sit at the table
/who                             # who is here
/announce Bring dice             # DM only, messages everyone
/endsession The dragon fled.     # DM only, records the length
/session                         # recent sessions
```

Join flow: a player sends `/join ABC123`, the DM gets **Approve / Deny**
buttons, and the player is told the moment they are in. Approvals also have a
TTL (`REQUEST_TTL`) so stale requests expire.

### Characters

`/newchar` asks six questions (name, class/subclass, race/background, level,
ability scores, AC/HP/speed) and builds the sheet.

```
/char              # your sheet (or /char Sylra)
/hp -7             # damage, with temp HP and downing handled
/hp +3 rest
/sethp 38 52       # set current / max
/levelup           # or /level 5
/xp 1250
/set ac 16         # also speed, init, gold, level, temp, str…cha
/note text
/party             # everyone's vitals at a glance
/switch Name       # change your active character
```

Ability modifiers, an HP bar, inspiration and a recent-events log are shown.
Combat initiative modifiers are derived from DEX automatically.

### SRD

SRD **5.2.1 (2014)** from dnd5eapi.co, cached to `.cache/` so repeated lookups
are instant and the public API is not hammered.

```
/monster goblin                 # full stat block
/spell fireball
/item adamantine armor
/equipment longsword
/rule long rest
/class wizard      /race elf      /subrace high-elf
/condition blinded /skill stealth  /prof daggers
/damagetype fire  /align chaotic good
/search anything               # search every category at once
/randmonster 2      /randmonster 1/4      /randspell
```

Names are matched fuzzily — `/monster ancient red drgn` finds the red dragon.
Long records (big stat blocks, class tables) are split into pages with
◀ ▶ buttons. `/start` → **SRD** browses every category.

Coverage in this API: 334 monsters, 319 spells, 362 magic items, 237 equipment,
137 rules, 12 classes, races/subraces, conditions, skills, proficiencies,
damage types and alignments.

---

## Commands

`/tutorial` for the guided walkthrough, `/help` for the reference, and
`/help dice`, `/help campaign`, `/help session`, `/help character`,
`/help srd` for one area. `/whoami` shows your id and your characters.
Type `/` in the chat for the full menu.

## MCP server

The same brain over the [Model Context Protocol](https://modelcontextprotocol.io),
for AI clients: **34 tools** that read and write the same database and the
same SRD cache as the bot, over **stdio**. Nothing here talks to Telegram, so
nothing is broadcast — a session you open from a tool does not ping the
players; they see it when they ask. Results are Markdown, and any domain
mistake (no campaign, not the DM, a level that would go down) comes back as a
readable error the model can correct and retry.

```bash
.venv/bin/python mcp_server.py     # stdio transport; config errors go to stderr
```

`MCP_USER_ID` in `.env` is the identity every tool acts for when a call omits
`user_id`; without it, each tool needs an explicit id (`whoami` explains how
to find one). Client configuration:

```json
{
  "mcp": {
    "dndbot": {
      "type": "local",
      "command": [
        "/home/you/DnD-helper-bot/.venv/bin/python",
        "/home/you/DnD-helper-bot/mcp_server.py"
      ]
    }
  }
}
```

Each tool's description states its Telegram syntax, so the two surfaces stay
one vocabulary:

| Tool | Telegram |
| --- | --- |
| **Dice** | |
| `roll` | `/roll 2d6+3`, `/roll adv`, `/adv:+5` |
| `party_initiative` | `/init [bonus]` |
| **SRD** | |
| `srd_lookup` | `/monster`, `/spell`, `/item`, `/rule`, … |
| `srd_search` | `/search fireball` |
| `srd_random` | `/randmonster [cr]`, `/randspell` |
| `srd_list` | the SRD menu (`/start` → SRD) |
| **Identity and meta** | |
| `whoami` | `/whoami` (also resolves names to ids) |
| `get_help` | `/help [dice|campaign|session|character|srd]` |
| `get_tutorial` | `/tutorial [start|setup|join|…]` |
| `recent_errors` | `/errors` (admin only, as `ADMIN_ID`) |
| **Characters** | |
| `create_character` | the six-step `/newchar` wizard in one call (`force` = `/newchar force`) |
| `get_character` | `/char [name]` |
| `list_party` | `/party` |
| `change_hp` | `/hp -7 [reason]` |
| `set_hp` | `/sethp 38 52` |
| `level_up` | `/levelup`, `/level 5` |
| `set_xp` | `/xp 3200` |
| `set_character_field` | `/set ac 16` |
| `add_note` | `/note <text>` |
| `switch_character` | `/switch [Name]` |
| **Campaigns** | |
| `list_campaigns` | `/campaigns` |
| `create_campaign` | `/newcampaign Name \| Blurb` |
| `join_campaign` | `/join ABC123` |
| `list_join_requests` | `/pending` |
| `resolve_join` | the Approve/Deny buttons on `/pending` |
| `select_campaign` | `/select [Name]` |
| `get_roster` | `/roster` |
| `campaign_info` | `/campaign` |
| `leave_campaign` | `/leave` |
| **Sessions** | |
| `start_session` | `/startsession [title]` |
| `end_session` | `/endsession [notes]` |
| `session_history` | `/session` |
| `checkin` | `/checkin` |
| `who_is_here` | `/who` |

## Layout

```
bot.py                  entry point (Telegram)
mcp_server.py           entry point (MCP, stdio)
dndbot/
  __main__.py           client setup, token login, handler registration, guards
  config.py             .env loading and validation
  logs.py               rotating file logging, reading recent errors
  storage.py            SQLite schema and queries (aiosqlite)
  dice.py               dice notation parser and roller
  srd.py                SRD API client, fuzzy search, disk cache, random-by-CR
  formatting.py         SRD JSON -> paginated Telegram HTML
  keyboards.py          inline keyboard builders, SRD callback tokens
  common.py             name/time helpers, campaign resolution, HTML escaping
  help.py               the /help and /tutorial pages
  sheet.py              sheet rules and rendering (HP bar, party, stat block)
  services.py           shared flows and their words (campaigns, sessions)
  handlers/
    core.py             /start, /help, menus
    tutorial.py         /tutorial walkthrough
    dice.py             dice commands and the dice pad
    campaign.py         campaigns, join approvals, sessions
    character.py        character creation and sheets
    srd_lookup.py       SRD commands, search and browser
  mcp/
    __init__.py         build_server(): assembles the 34-tool registry
    runtime.py          lifespan: database and SRD cache, no Telethon
    identity.py         who a tool acts for (user_id, else MCP_USER_ID)
    inputs.py           campaign / member / DM resolution from arguments
    render.py           Telegram HTML -> Markdown, strip, esc_md
    dice.py             roll, party_initiative
    srd.py              lookup, search, random, list
    meta.py             whoami, get_help, get_tutorial, recent_errors
    character.py        the 10 sheet tools
    campaign.py         the 14 campaign and session tools
docs/
  mcp-server-plan.md    MCP plan, tool inventory and phase status
tests/
  test_units.py         dice parser, paging, escaping (no network)
  test_flow.py          end-to-end handler flow with fake events
  test_mcp.py           MCP registry and tools, no transport
```

## Tests

```bash
.venv/bin/python tests/test_units.py   # fast, no network
.venv/bin/python tests/test_flow.py    # full flow; hits the SRD API
.venv/bin/python tests/test_mcp.py     # MCP tools; hits the SRD API
```

`test_flow.py` drives the real handlers with fake Telegram events and asserts:

* every reply is valid, well-formed HTML within Telegram's 4096-character limit;
* every command and button actually sends something;
* no handler logged an error while handling it — the bot's guard swallows
  exceptions and replies politely, so a crash is otherwise invisible;
* a deliberately broken handler is reported *and* written to the error log.

The fake `CallbackQuery` deliberately has no `.out` attribute, matching real
Telethon, so code that probes `event.out` in a button handler is caught.

`test_mcp.py` builds the real registry (`build_server()`) and then calls every
tool as a plain function — no transport, a throwaway database, environment set
before `dndbot` is imported. It asserts the 34-tool inventory and its
descriptions, then walks identity, dice, help, the SRD path, characters,
campaigns and sessions, checking both contracts on the way: domain failures
arrive as `ToolError` in plain text, and results are Markdown with no Telegram
HTML left in them.

## Errors and logs

Everything is logged to `logs/`:

* **`logs/bot.log`** - the full log, rotated at 5 MB with three backups kept
  (`bot.log.1` … `bot.log.3`).
* **`logs/errors.log`** - errors only, with full tracebacks and the chat and
  user each one came from. Small enough to open and read.

If a handler ever fails, the player gets a short apology instead of silence and
the traceback lands in `errors.log`:

```
2026-09-30 19:49:53 ERROR    dndbot   handler tutorial_pages failed in chat=42 user=7
Traceback (most recent call last):
  ...
AttributeError: 'Event' object has no attribute 'out'
```

Send **`/errors`** in Telegram to read the last few errors without touching the
machine.

Logging is configurable in `.env`:

```ini
LOG_LEVEL=INFO          # DEBUG shows Telethon's traffic
LOG_MAX_BYTES=5242880   # rotate at 5 MB
LOG_BACKUPS=3           # keep three previous files
LOG_DIR=logs            # where the files go
```

## Notes

* **Token login**: the bot authenticates with `BOT_TOKEN`. Delete
  `dnd_helper.session` to force a re-login.
* **Groups**: the bot works in groups; if the group title contains a campaign
  name it is used automatically.
* **Escaping**: player-supplied names and notes are HTML-escaped.
* **Rate limits**: announcements and broadcasts sleep between messages.
* **Callback payloads**: SRD slugs are too long for Telegram's 64-byte limit,
  so entries are addressed by short server-side tokens.
* **Attribution**: SRD 5.2.1 © Wizards of the Coast, CC-BY-4.0.
