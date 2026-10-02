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

Start with **`/tutorial`** — an eight-page walkthrough covering dice, setting
up as DM, joining a game, character sheets, SRD lookups, encounters and
groups. Page through it with the ◀ ▶ buttons, or jump straight to one:

```
/tutorial          # page 1
/tutorial dice     # or: setup, join, character, srd, encounters, groups
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

### Encounters (DM only)

Prepare a fight ahead of time, tailor it to your table, then drop it into the
session.

```
/newencounter Ambush at the ford | hidden
/addmonster goblin 3
/addmonster ogre
/ms 1 ac 16          # and hp, dmg, atk, count, name, note
/ms 1 hp 30
/ms 1 dmg 2d6+4
/ms 2 note breathes fire
/ms 3 hide           # a surprise the table cannot see
/ms 4 nohp           # damage piles up, only /kill brings it down
/ms 5 boss           # crown on its name, everyone sees it
/fight Ambush at the ford
```

During the fight — by anyone at the table:

```
/hit 1 2d6+3         # roll and damage the first goblin; I remember who did it
/hit ogre 11         # name a combatant instead of numbering it
/heal 2 10
/kill wraith         # DM only: drop it outright, whatever its HP
/fight               # current state
/endfight
```

**What the party is told**

* **Death alerts** — when a combatant falls, everyone is messaged, whoever
  landed the blow.
* **The end is announced** — `/endfight` messages every player with the
  name, how long it ran, how many were left and total damage dealt.
* **Bosses get a crown** — `/ms 5 boss` puts 👑 next to the name in every
  list, for the DM and the table alike. Toggle it mid-fight.
* **Every combatant is numbered.** The number in front of the name is
  exactly what `/hit N` targets, and it is the order of the *standing*
  monsters, so the list closes up as things die. Dead ones are marked
  *no longer a valid target*.
* **Hidden monsters cannot be probed.** A player who guesses the number
  of a hidden monster gets *no combatant matching*, so the ambush stays an
  ambush.
* **Hidden monsters are simply absent.** The party is never told how many are
  lurking, because that would give the ambush away.
* **Reveals are announced.** `/ms 3 show` mid-fight reveals the unit and tells
  everyone it has revealed itself.
* **No-HP monsters** (`/ms N nohp`) show only accumulated damage — no bar, no
  numbers, to anyone. They cannot be dropped by damage; they die when the DM
  uses `/kill`, which announces it like any other death. Good for wraiths, or
  anything that should only fall when the story says so.

**Hit points come in two modes, chosen when the encounter is created:**

| Mode | Players see |
| --- | --- |
| `visible` (default) | full HP bars, live, as damage lands |
| `hidden` | **no HP numbers at all** — only the damage dealt and by whom |

In hidden mode a player sees:

```
Goblin #1 • AC 15
    15 damage — Sylra 12 • Bront 3
Goblin #2 • AC 15
    untouched
```

while the DM sees the real numbers and a reminder that players cannot.
Visibility cannot change mid-fight. `/hpmode hidden` or `/hpmode visible`
switches it between fights.

A `count` of 3 gives three independently-tracked units (`Goblin #1..#3`), so
killing one does not damage the others. Editing a template never disturbs a
fight already running — the live fight works on its own copy of the stats.

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
`/help srd`, `/help encounter` for one area. `/whoami` shows your id and
your characters. Type `/` in the chat for the full menu (60 commands).

## Layout

```
bot.py                  entry point
dndbot/
  __main__.py           client setup, token login, handler registration, guards
  config.py             .env loading and validation
  logs.py               rotating file logging, reading recent errors
  storage.py            SQLite schema and queries (aiosqlite)
  dice.py               dice notation parser and roller
  srd.py                SRD API client, fuzzy search, disk cache
  formatting.py         SRD JSON -> paginated Telegram HTML
  keyboards.py          inline keyboard builders, SRD callback tokens
  common.py             name/time helpers, campaign resolution, HTML escaping
  handlers/
    core.py             /start, /help, menus
    tutorial.py         /tutorial walkthrough (8 pages)
    dice.py             dice commands and the dice pad
    campaign.py         campaigns, join approvals, sessions
    character.py        character creation and sheets
    encounter.py       encounter templates, stat editing, live fights
    srd_lookup.py       SRD commands, search and browser
tests/
  test_units.py         dice parser, paging, escaping (no network)
  test_flow.py          end-to-end handler flow with fake events
```

## Tests

```bash
.venv/bin/python tests/test_units.py   # fast, no network
.venv/bin/python tests/test_flow.py    # full flow; hits the SRD API
```

`test_flow.py` drives the real handlers with fake Telegram events and asserts:

* every reply is valid, well-formed HTML within Telegram's 4096-character limit;
* every command and button actually sends something;
* no handler logged an error while handling it — the bot's guard swallows
  exceptions and replies politely, so a crash is otherwise invisible;
* a deliberately broken handler is reported *and* written to the error log.

The fake `CallbackQuery` deliberately has no `.out` attribute, matching real
Telethon, so code that probes `event.out` in a button handler is caught.

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
