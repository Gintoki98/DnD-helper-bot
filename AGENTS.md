# AGENTS.md

Telegram D&D 5e helper bot: Telethon + SQLite (`aiosqlite`) + the public
dnd5eapi SRD API. One package (`dndbot/`), entry point `bot.py`, Python 3.12,
async throughout. `README.md` is accurate and doubles as the user manual.

## Commands

```bash
# setup (no lockfile; deps live in requirements.txt)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# run the bot — needs .env (API_ID, API_HASH, BOT_TOKEN; see .env.example)
.venv/bin/python bot.py

# run the MCP server (stdio; .env only needs MCP_USER_ID, no Telegram creds)
.venv/bin/python mcp_server.py

# tests — plain scripts, NOT pytest (pytest is not installed, no config exists)
.venv/bin/python tests/test_units.py   # fast, offline: dice, paging, escaping
.venv/bin/python tests/test_flow.py    # full handler flow; needs SRD API
.venv/bin/python tests/test_mcp.py     # MCP registry + 34 tools; needs SRD API
```

Each script prints `ALL GREEN` and `sys.exit(0)` on success, else list
`FAIL ...` lines and exit 1. There is no lint, formatter, typechecker, or CI
config in this repo — don't invent commands for them.

## Testing quirks

- `test_flow.py` sets `DB_PATH` and `LOG_DIR` to temp dirs via `os.environ`
  **before** importing `dndbot`: `dndbot/config.py` reads the environment at
  import time and creates `dndbot/data/`, `.cache/` and `logs/` as import side
  effects. Any new test must set env vars before importing `dndbot`.
- Handler exceptions never escape: `_guard` in `dndbot/__main__.py` patches
  `add_event_handler`, logs the traceback to `logs/errors.log`, and replies
  politely. A crash therefore looks like a normal reply — the flow test only
  catches it by watching the log (`ErrorCapture`). Always run `test_flow.py`
  after touching handlers.
- `test_flow.py` SRD checks reach https://www.dnd5eapi.co unless `.cache/` is
  warm (`SRD_CACHE_TTL`, default 1 week). With the API unreachable and a cold
  cache, `/randmonster`, `/randspell` and the dice-pad `encounter` button fail.
- The fake `CallbackQuery` in tests deliberately has no `.out` attribute
  (matching real Telethon) — never read `event.out` in button handlers.
- `character_mod.BROKEN = True` is a test-only flag that forces a handler
  crash for the error-log assertion. Don't "fix" it.
- The flow test closes `db` and `srd` in a `finally`: aiosqlite's worker
  thread otherwise hangs the interpreter. `test_mcp.py` does the same, and
  also forces `MCP_USER_ID=""` (empty → no default identity) and a fixed
  `ADMIN_ID` so both identity branches are assertable whatever `.env` holds.

## Wiring

- Startup order in `dndbot/__main__.py:main()` — `setup_logging` →
  `config.check` → `build_client` → `register_handlers` → login →
  `publish_commands` → `db.connect` → `srd.start/warm`.
- Handlers live in `dndbot/handlers/*.py`, each exposing `register(client)`.
  A new command needs three edits: its handler module, `register_handlers()`
  in `dndbot/__main__.py`, and the `BOT_COMMANDS` list in the same file
  (published to Telegram's slash menu). Also document it in `README.md`,
  `/help` text (`handlers/core.py`) and, when relevant, `/tutorial`.
- Commands use `events.NewMessage(pattern=...)`; buttons use
  `events.CallbackQuery(pattern=r"^prefix:")`. Payload prefixes are listed in
  the `dndbot/keyboards.py` module docstring.
- Telegram caps callback data at 64 bytes, so SRD entries are addressed by
  short tokens (`entry_token` / `entry_from_token` in `dndbot/keyboards.py`).
  The token map is in-memory and resets on restart.

## MCP wiring

- Entry point `mcp_server.py` → `dndbot/mcp.build_server()`: the lifespan
  (`dndbot/mcp/runtime.py`) runs `config.check(telegram=False)` →
  `db.connect` → `srd.start/warm` → `expire_stale_requests`, and unwinds
  `srd.close` + `db.close` at shutdown — `main()` minus Telethon.
- Adding a tool needs three edits: the function in the right
  `dndbot/mcp/*.py` module (module-level, `-> str`, docstring says what it
  does **and** names the `Telegram:` command it mirrors), that module's
  `register()` tuple, and the tool table in `README.md`. `register()` does
  `mcp.add_tool(fn, description=cleandoc(fn.__doc__))` — the SDK reads
  `__doc__` exactly as it sits in the source, indentation included.
- Never import `dndbot/handlers/*` from `dndbot/mcp/*` (it pulls Telethon).
  Shared logic lives in `common.py`, `sheet.py` and `services.py`; shared
  wording lives in `services.py` too, in its `# -- words both sides send`
  section. Guidance messages that name *actions* are written per surface
  (commands in handlers, tool names in MCP).
- Identity is `identity.actor` (explicit `user_id`, else `MCP_USER_ID`);
  the campaign is `inputs.campaign` / `inputs.member` / `inputs.dm`, each
  raising `ToolError` with the bot's own wording.
- Output rules: `render.to_markdown` for HTML the bot also renders,
  `render.strip` for `ToolError` messages (plain text), `render.esc_md` for
  player names inside strings the MCP builds itself. Never return an error
  as a value — `raise ToolError`, or the model reads it as success.
- Nothing here broadcasts: sessions, approvals and joins made through the
  MCP are not announced in Telegram (that needs the Telethon client).

## Conventions & gotchas

- `client.parse_mode = "html"` globally: every reply is Telegram HTML. Escape
  player-supplied text with `safe` (`dndbot/common.py`) or `esc`
  (`dndbot/formatting.py`), keep replies ≤ 4096 chars, tags balanced, no bare
  `&`. `test_flow.py` enforces all of this on every reply and button edit.
- Schema is `CREATE TABLE IF NOT EXISTS` executed at `db.connect()` — there is
  no migration system, so altering an existing table's columns will not update
  an already-created `dndbot/data/dndbot.db`.
- Changing the bot token? Delete `dnd_helper.session` (Telethon session) or
  the old login is reused.
- Broadcasts `await asyncio.sleep(0.4)` between messages to stay under flood
  limits — keep that pace in any new mass-messaging path.
- The SRD API is free and rate-limited: go through the `.cache/` layer in
  `dndbot/srd.py`, never fetch raw in handlers.
