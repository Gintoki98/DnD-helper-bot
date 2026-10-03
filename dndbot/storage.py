"""SQLite persistence for campaigns, sessions and characters.

Every method is a coroutine backed by ``aiosqlite`` so the Telethon event loop
stays responsive.  A single connection is shared and guarded by a lock, which
is plenty for a table-sized group chat.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from typing import Any, Sequence

import aiosqlite

from . import config

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,
    username    TEXT,
    first_name  TEXT,
    last_name   TEXT,
    active_campaign_id INTEGER,
    created_at  REAL NOT NULL,
    last_seen   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS campaigns (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL,
    invite_code  TEXT    NOT NULL UNIQUE,
    system       TEXT    NOT NULL DEFAULT '5e',
    description  TEXT    NOT NULL DEFAULT '',
    dm_id        INTEGER NOT NULL,
    is_public    INTEGER NOT NULL DEFAULT 0,
    created_at   REAL    NOT NULL,
    touched_at   REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_campaigns_dm ON campaigns(dm_id);
CREATE INDEX IF NOT EXISTS idx_campaigns_name ON campaigns(name COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS memberships (
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL,
    role        TEXT    NOT NULL DEFAULT 'player',
    joined_at   REAL    NOT NULL,
    PRIMARY KEY (campaign_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_memberships_user ON memberships(user_id);

CREATE TABLE IF NOT EXISTS join_requests (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id  INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    user_id      INTEGER NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'pending',
    note         TEXT    NOT NULL DEFAULT '',
    created_at   REAL    NOT NULL,
    resolved_at  REAL,
    resolved_by  INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_open_request
    ON join_requests(campaign_id, user_id) WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id  INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    started_by   INTEGER NOT NULL,
    started_at   REAL    NOT NULL,
    ended_at     REAL,
    status       TEXT    NOT NULL DEFAULT 'active',
    title        TEXT    NOT NULL DEFAULT '',
    notes        TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sessions_campaign ON sessions(campaign_id, status);

CREATE TABLE IF NOT EXISTS session_attendance (
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL,
    joined_at  REAL    NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'online',
    PRIMARY KEY (session_id, user_id)
);

CREATE TABLE IF NOT EXISTS characters (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id  INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    user_id      INTEGER NOT NULL,
    name         TEXT    NOT NULL,
    class_name   TEXT    NOT NULL DEFAULT '',
    race         TEXT    NOT NULL DEFAULT '',
    background   TEXT    NOT NULL DEFAULT '',
    subclass     TEXT    NOT NULL DEFAULT '',
    alignment    TEXT    NOT NULL DEFAULT '',
    level        INTEGER NOT NULL DEFAULT 1,
    xp           INTEGER NOT NULL DEFAULT 0,
    hp           INTEGER NOT NULL DEFAULT 1,
    max_hp       INTEGER NOT NULL DEFAULT 1,
    temp_hp      INTEGER NOT NULL DEFAULT 0,
    ac           INTEGER NOT NULL DEFAULT 10,
    initiative   INTEGER NOT NULL DEFAULT 0,
    speed        INTEGER NOT NULL DEFAULT 30,
    str          INTEGER NOT NULL DEFAULT 10,
    dex          INTEGER NOT NULL DEFAULT 10,
    con          INTEGER NOT NULL DEFAULT 10,
    wis          INTEGER NOT NULL DEFAULT 10,
    intl         INTEGER NOT NULL DEFAULT 10,
    cha          INTEGER NOT NULL DEFAULT 10,
    inspiration  INTEGER NOT NULL DEFAULT 0,
    gold         INTEGER NOT NULL DEFAULT 0,
    notes        TEXT    NOT NULL DEFAULT '',
    is_active    INTEGER NOT NULL DEFAULT 1,
    created_at   REAL    NOT NULL,
    updated_at   REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_characters_owner ON characters(campaign_id, user_id);

CREATE TABLE IF NOT EXISTS character_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    kind         TEXT    NOT NULL,
    detail       TEXT    NOT NULL DEFAULT '',
    created_by   INTEGER NOT NULL,
    created_at   REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_character ON character_events(character_id, id);

-- Encounters are built as a reusable template before play, then invoked in a
-- session. HP visibility is fixed when the template is created.
CREATE TABLE IF NOT EXISTS encounters (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    created_by  INTEGER NOT NULL,
    name        TEXT    NOT NULL,
    notes       TEXT    NOT NULL DEFAULT '',
    hp_mode     TEXT    NOT NULL DEFAULT 'visible',  -- 'visible' | 'hidden'
    status      TEXT    NOT NULL DEFAULT 'draft',    -- 'draft' | 'ready'
    created_at  REAL    NOT NULL,
    updated_at  REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_encounters_campaign ON encounters(campaign_id, updated_at);

CREATE TABLE IF NOT EXISTS encounter_monsters (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    encounter_id INTEGER NOT NULL REFERENCES encounters(id) ON DELETE CASCADE,
    slot         INTEGER NOT NULL DEFAULT 0,
    name         TEXT    NOT NULL,
    srd_index    TEXT    NOT NULL DEFAULT '',
    count        INTEGER NOT NULL DEFAULT 1,
    ac           INTEGER NOT NULL DEFAULT 10,
    max_hp       INTEGER NOT NULL DEFAULT 1,
    speed        INTEGER NOT NULL DEFAULT 30,
    cr           TEXT    NOT NULL DEFAULT '',
    size         TEXT    NOT NULL DEFAULT '',
    mtype        TEXT    NOT NULL DEFAULT '',
    alignment    TEXT    NOT NULL DEFAULT '',
    str          INTEGER NOT NULL DEFAULT 10,
    dex          INTEGER NOT NULL DEFAULT 10,
    con          INTEGER NOT NULL DEFAULT 10,
    intl         INTEGER NOT NULL DEFAULT 10,
    wis          INTEGER NOT NULL DEFAULT 10,
    cha          INTEGER NOT NULL DEFAULT 10,
    attack_bonus INTEGER NOT NULL DEFAULT 0,
    damage       TEXT    NOT NULL DEFAULT '1d6',
    no_hp        INTEGER NOT NULL DEFAULT 0,
    is_boss      INTEGER NOT NULL DEFAULT 0,
    resistances  TEXT    NOT NULL DEFAULT '',
    immunities   TEXT    NOT NULL DEFAULT '',
    condition_immunity TEXT NOT NULL DEFAULT '',
    hidden       INTEGER NOT NULL DEFAULT 0,
    notes        TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_enc_monsters ON encounter_monsters(encounter_id, slot);

CREATE TABLE IF NOT EXISTS encounter_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    encounter_id INTEGER NOT NULL REFERENCES encounters(id) ON DELETE CASCADE,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    session_id  INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
    started_by  INTEGER NOT NULL,
    started_at  REAL    NOT NULL,
    ended_at    REAL,
    status      TEXT    NOT NULL DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS idx_runs_status ON encounter_runs(campaign_id, status);

CREATE TABLE IF NOT EXISTS encounter_units (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       INTEGER NOT NULL REFERENCES encounter_runs(id) ON DELETE CASCADE,
    monster_id   INTEGER NOT NULL REFERENCES encounter_monsters(id) ON DELETE CASCADE,
    label        TEXT    NOT NULL,
    ac           INTEGER NOT NULL DEFAULT 10,
    hp           INTEGER NOT NULL DEFAULT 1,
    max_hp       INTEGER NOT NULL DEFAULT 1,
    attack_bonus INTEGER NOT NULL DEFAULT 0,
    damage       TEXT    NOT NULL DEFAULT '',
    hidden       INTEGER NOT NULL DEFAULT 0,
    no_hp        INTEGER NOT NULL DEFAULT 0,
    is_boss      INTEGER NOT NULL DEFAULT 0,
    status       TEXT    NOT NULL DEFAULT 'alive',
    sort_order   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_units_run ON encounter_units(run_id, sort_order);

CREATE TABLE IF NOT EXISTS encounter_damage (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES encounter_runs(id) ON DELETE CASCADE,
    unit_id     INTEGER NOT NULL REFERENCES encounter_units(id) ON DELETE CASCADE,
    actor_id    INTEGER,
    amount      INTEGER NOT NULL,
    detail      TEXT    NOT NULL DEFAULT '',
    remaining   INTEGER NOT NULL DEFAULT 0,
    created_at  REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_damage_unit ON encounter_damage(unit_id, id);
"""

# Columns added after a table first shipped. CREATE TABLE IF NOT EXISTS will
# not add these to a table that already exists, so they go on via ALTER TABLE.
MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("encounter_monsters", "no_hp", "INTEGER NOT NULL DEFAULT 0"),
    ("encounter_units", "no_hp", "INTEGER NOT NULL DEFAULT 0"),
    ("encounter_monsters", "is_boss", "INTEGER NOT NULL DEFAULT 0"),
    ("encounter_units", "is_boss", "INTEGER NOT NULL DEFAULT 0"),
)

def ability_modifier(score: int) -> int:
    """5e ability modifier: floor((score - 10) / 2)."""
    return (score - 10) // 2


def new_invite_code(length: int = 6) -> str:
    """Unambiguous code: no 0/O/1/I, so players can read it aloud."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


# Columns and fallback values for a new character row. ``create_character``
# fills anything the caller leaves out, so the wizard only sends what it knows.
CHARACTER_DEFAULTS: dict[str, Any] = {
    "campaign_id": 0,
    "user_id": 0,
    "name": "Unnamed",
    "class_name": "",
    "race": "",
    "background": "",
    "subclass": "",
    "alignment": "",
    "level": 1,
    "xp": 0,
    "hp": 1,
    "max_hp": 1,
    "temp_hp": 0,
    "ac": 10,
    "initiative": 0,
    "speed": 30,
    "str": 10,
    "dex": 10,
    "con": 10,
    "intl": 10,
    "wis": 10,
    "cha": 10,
    "inspiration": 0,
    "gold": 0,
    "notes": "",
}

# The columns ``update_character`` will accept; the bookkeeping ones stay read-only.
UPDATABLE_CHARACTER_FIELDS = (frozenset(CHARACTER_DEFAULTS) | {"is_active"}) - {
    "campaign_id",
    "user_id",
}


class Database:
    def __init__(self, path: Any = None) -> None:
        self.path = str(path or config.DB_PATH)
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    # -- lifecycle ------------------------------------------------------
    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._migrate()
        await self._db.commit()

    async def _migrate(self) -> None:
        """Add any columns a previous version of the schema did not have."""
        for table, column, definition in MIGRATIONS:
            try:
                async with self._db.execute(f"PRAGMA table_info({table})") as cur:
                    existing = {row["name"] for row in await cur.fetchall()}
            except Exception:
                continue
            if not existing or column in existing:
                continue  # table not present yet, or already migrated
            try:
                await self._db.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                )
                logging.getLogger("dndbot").info(
                    "migrated %s: added column %s", table, column
                )
            except Exception:
                logging.getLogger("dndbot").warning(
                    "could not add %s.%s", table, column, exc_info=True
                )

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Database.connect() has not been awaited")
        return self._db

    async def _fetchone(self, sql: str, params: Sequence[Any] = ()) -> Any:
        async with self._lock:
            async with self.db.execute(sql, params) as cur:
                return await cur.fetchone()

    async def _fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[Any]:
        async with self._lock:
            async with self.db.execute(sql, params) as cur:
                return list(await cur.fetchall())

    async def _write(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Run a write statement and return the number of affected rows."""
        async with self._lock:
            cur = await self.db.execute(sql, params)
            await self.db.commit()
            return cur.rowcount

    async def _insert(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Run an INSERT and return the new row id."""
        async with self._lock:
            cur = await self.db.execute(sql, params)
            await self.db.commit()
            return int(cur.lastrowid)

    # -- users ----------------------------------------------------------
    async def upsert_user(
        self,
        user_id: int,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> None:
        now = time.time()
        await self._write(
            """
            INSERT INTO users (id, username, first_name, last_name, created_at, last_seen)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                username   = COALESCE(excluded.username, users.username),
                first_name = COALESCE(excluded.first_name, users.first_name),
                last_name  = COALESCE(excluded.last_name, users.last_name),
                last_seen  = excluded.last_seen
            """,
            (user_id, username, first_name, last_name, now, now),
        )

    async def get_user(self, user_id: int) -> Any:
        return await self._fetchone("SELECT * FROM users WHERE id = ?", (user_id,))

    async def find_user(self, term: str) -> list[Any]:
        """Telegram users whose username or name contains ``term``, newest first.

        A leading ``@`` is ignored, because that is how a handle is usually
        written. Only people who have already spoken to the bot are known.
        """
        like = f"%{term.strip().lstrip('@')}%"
        return await self._fetchall(
            "SELECT * FROM users WHERE username LIKE ? OR first_name LIKE ? "
            "OR last_name LIKE ? ORDER BY last_seen DESC LIMIT 10",
            (like, like, like),
        )

    async def set_active_campaign(self, user_id: int, campaign_id: int | None) -> None:
        """Remember which campaign a player is currently working in."""
        await self._write(
            "UPDATE users SET active_campaign_id = ? WHERE id = ?", (campaign_id, user_id)
        )

    async def active_campaign_id(self, user_id: int) -> int | None:
        row = await self._fetchone(
            "SELECT active_campaign_id FROM users WHERE id = ?", (user_id,)
        )
        return int(row["active_campaign_id"]) if row and row["active_campaign_id"] else None

    # -- campaigns ------------------------------------------------------
    async def create_campaign(
        self,
        name: str,
        dm_id: int,
        system: str = "5e",
        description: str = "",
        is_public: bool = False,
    ) -> Any:
        async with self._lock:
            for _ in range(8):
                code = new_invite_code()
                try:
                    cur = await self.db.execute(
                        """
                        INSERT INTO campaigns
                            (name, invite_code, system, description, dm_id, is_public,
                             created_at, touched_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            name, code, system, description, dm_id, int(is_public),
                            time.time(), time.time(),
                        ),
                    )
                    await self.db.commit()
                    campaign_id = cur.lastrowid
                    break
                except aiosqlite.IntegrityError:
                    continue
            else:  # pragma: no cover - practically unreachable
                raise RuntimeError("Could not allocate a unique invite code")

            await self.db.execute(
                "INSERT OR REPLACE INTO memberships (campaign_id, user_id, role, joined_at)"
                " VALUES (?, ?, 'dm', ?)",
                (campaign_id, dm_id, time.time()),
            )
            await self.db.commit()
        return await self.get_campaign(campaign_id)

    async def get_campaign(self, campaign_id: int) -> Any:
        return await self._fetchone("SELECT * FROM campaigns WHERE id = ?", (campaign_id,))

    async def get_campaign_by_code(self, code: str) -> Any:
        return await self._fetchone(
            "SELECT * FROM campaigns WHERE invite_code = ? COLLATE NOCASE", (code.strip(),)
        )

    async def lookup_campaign(self, term: str) -> Any:
        """The campaign a player typed: exact invite code first, else best name match.

        Returns ``None`` when nothing matches, so callers can reply once.
        """
        row = await self.get_campaign_by_code(term)
        if row is not None:
            return row
        matches = await self.find_campaigns(term)
        return matches[0] if matches else None

    async def find_campaigns(self, term: str) -> list[Any]:
        """Campaigns whose name contains the term, best matches first."""
        like = f"%{term}%"
        return await self._fetchall(
            """
            SELECT c.* FROM campaigns c
            LEFT JOIN memberships m
                   ON m.campaign_id = c.id AND m.user_id = ?
            WHERE c.name LIKE ? COLLATE NOCASE OR c.invite_code = ? COLLATE NOCASE
            ORDER BY (m.user_id IS NOT NULL) DESC,
                     (c.name LIKE ? COLLATE NOCASE) DESC,
                     length(c.name)
            LIMIT 20
            """,
            (term, like, term.strip(), f"{term}%"),
        )

    async def campaigns_for_user(self, user_id: int) -> list[Any]:
        return await self._fetchall(
            """
            SELECT c.*, m.role, c.dm_id
            FROM campaigns c
            JOIN memberships m ON m.campaign_id = c.id
            WHERE m.user_id = ?
            ORDER BY c.touched_at DESC
            """,
            (user_id,),
        )

    async def touch_campaign(self, campaign_id: int) -> None:
        """Mark a campaign as recently used so it sorts first in /campaigns."""
        await self._write(
            "UPDATE campaigns SET touched_at = ? WHERE id = ?", (time.time(), campaign_id)
        )

    # -- membership -----------------------------------------------------
    async def membership(self, campaign_id: int, user_id: int) -> Any:
        return await self._fetchone(
            "SELECT * FROM memberships WHERE campaign_id = ? AND user_id = ?",
            (campaign_id, user_id),
        )

    async def is_dm(self, campaign_id: int, user_id: int) -> bool:
        row = await self.membership(campaign_id, user_id)
        return bool(row and row["role"] == "dm")

    async def is_member(self, campaign_id: int, user_id: int) -> bool:
        return bool(await self.membership(campaign_id, user_id))

    async def add_member(self, campaign_id: int, user_id: int, role: str = "player") -> None:
        await self._write(
            "INSERT OR REPLACE INTO memberships (campaign_id, user_id, role, joined_at)"
            " VALUES (?, ?, ?, ?)",
            (campaign_id, user_id, role, time.time()),
        )
        await self.touch_campaign(campaign_id)

    async def remove_member(self, campaign_id: int, user_id: int) -> None:
        await self._write(
            "DELETE FROM memberships WHERE campaign_id = ? AND user_id = ?",
            (campaign_id, user_id),
        )

    async def roster(self, campaign_id: int) -> list[Any]:
        return await self._fetchall(
            """
            SELECT m.user_id, m.role, m.joined_at, u.username, u.first_name, u.last_name
            FROM memberships m
            LEFT JOIN users u ON u.id = m.user_id
            WHERE m.campaign_id = ?
            ORDER BY (m.role = 'dm') DESC, m.joined_at
            """,
            (campaign_id,),
        )

    # -- join requests --------------------------------------------------
    async def create_join_request(
        self, campaign_id: int, user_id: int, note: str = ""
    ) -> tuple[Any | None, bool]:
        """Create a pending request. Returns (request, is_new)."""
        if await self.membership(campaign_id, user_id):
            return None, False
        existing = await self._fetchone(
            "SELECT * FROM join_requests WHERE campaign_id = ? AND user_id = ?"
            " AND status = 'pending'",
            (campaign_id, user_id),
        )
        if existing:
            return existing, False
        cur = await self._insert(
            "INSERT INTO join_requests (campaign_id, user_id, status, note, created_at)"
            " VALUES (?, ?, 'pending', ?, ?)",
            (campaign_id, user_id, note, time.time()),
        )
        return await self._fetchone("SELECT * FROM join_requests WHERE id = ?", (cur,)), True

    async def get_request(self, request_id: int) -> Any:
        return await self._fetchone("SELECT * FROM join_requests WHERE id = ?", (request_id,))

    async def pending_requests(self, campaign_id: int) -> list[Any]:
        return await self._fetchall(
            "SELECT * FROM join_requests WHERE campaign_id = ? AND status = 'pending'"
            " ORDER BY created_at",
            (campaign_id,),
        )

    async def resolve_request(
        self, request_id: int, status: str, resolved_by: int
    ) -> None:
        await self._write(
            "UPDATE join_requests SET status = ?, resolved_at = ?, resolved_by = ?"
            " WHERE id = ?",
            (status, time.time(), resolved_by, request_id),
        )

    async def expire_stale_requests(self, ttl: int) -> int:
        cur = await self._write(
            "UPDATE join_requests SET status = 'expired' WHERE status = 'pending'"
            " AND created_at < ?",
            (time.time() - ttl,),
        )
        return cur

    # -- sessions -------------------------------------------------------
    async def start_session(
        self, campaign_id: int, started_by: int, title: str = ""
    ) -> Any:
        async with self._lock:
            await self.db.execute(
                "UPDATE sessions SET status = 'ended', ended_at = ?"
                " WHERE campaign_id = ? AND status = 'active'",
                (time.time(), campaign_id),
            )
            cur = await self.db.execute(
                "INSERT INTO sessions (campaign_id, started_by, started_at, status, title)"
                " VALUES (?, ?, ?, 'active', ?)",
                (campaign_id, started_by, time.time(), title),
            )
            await self.db.commit()
            session_id = cur.lastrowid
            await self.db.execute(
                "INSERT OR REPLACE INTO session_attendance (session_id, user_id, joined_at,"
                " status) VALUES (?, ?, ?, 'online')",
                (session_id, started_by, time.time()),
            )
            await self.db.commit()
        return await self.get_session(session_id)

    async def get_session(self, session_id: int) -> Any:
        return await self._fetchone("SELECT * FROM sessions WHERE id = ?", (session_id,))

    async def active_session(self, campaign_id: int) -> Any:
        return await self._fetchone(
            "SELECT * FROM sessions WHERE campaign_id = ? AND status = 'active'"
            " ORDER BY started_at DESC LIMIT 1",
            (campaign_id,),
        )

    async def end_session(self, session_id: int, notes: str = "") -> None:
        await self._write(
            "UPDATE sessions SET status = 'ended', ended_at = ?, notes = ? WHERE id = ?",
            (time.time(), notes, session_id),
        )

    async def recent_sessions(self, campaign_id: int, limit: int = 5) -> list[Any]:
        return await self._fetchall(
            "SELECT * FROM sessions WHERE campaign_id = ? ORDER BY started_at DESC LIMIT ?",
            (campaign_id, limit),
        )

    async def mark_attendance(self, session_id: int, user_id: int, status: str) -> None:
        await self._write(
            "INSERT OR REPLACE INTO session_attendance (session_id, user_id, joined_at, status)"
            " VALUES (?, ?, ?, ?)",
            (session_id, user_id, time.time(), status),
        )

    async def attendance(self, session_id: int) -> list[Any]:
        return await self._fetchall(
            """
            SELECT a.*, u.username, u.first_name, u.last_name
            FROM session_attendance a
            LEFT JOIN users u ON u.id = a.user_id
            WHERE a.session_id = ?
            ORDER BY a.joined_at
            """,
            (session_id,),
        )

    # -- characters -----------------------------------------------------
    async def create_character(self, **fields: Any) -> Any:
        now = time.time()
        defaults = dict(CHARACTER_DEFAULTS)
        defaults.update({k: v for k, v in fields.items() if v is not None})
        columns = ", ".join(defaults) + ", created_at, updated_at"
        placeholders = ", ".join("?" * (len(defaults) + 2))
        values = list(defaults.values()) + [now, now]
        char_id = await self._insert(
            f"INSERT INTO characters ({columns}) VALUES ({placeholders})", values
        )
        await self._write(
            "UPDATE characters SET is_active = 0 WHERE campaign_id = ? AND user_id = ?"
            " AND is_active = 1 AND id != ?",
            (defaults["campaign_id"], defaults["user_id"], char_id),
        )
        return await self.get_character(char_id)

    async def get_character(self, character_id: int) -> Any:
        return await self._fetchone("SELECT * FROM characters WHERE id = ?", (character_id,))

    async def active_character(self, campaign_id: int, user_id: int) -> Any:
        return await self._fetchone(
            "SELECT * FROM characters WHERE campaign_id = ? AND user_id = ? AND is_active = 1",
            (campaign_id, user_id),
        )

    async def list_characters(self, campaign_id: int, user_id: int) -> list[Any]:
        return await self._fetchall(
            "SELECT * FROM characters WHERE campaign_id = ? AND user_id = ?"
            " ORDER BY is_active DESC, updated_at DESC",
            (campaign_id, user_id),
        )

    async def find_character(self, campaign_id: int, term: str) -> list[Any]:
        like = f"%{term}%"
        return await self._fetchall(
            "SELECT * FROM characters WHERE campaign_id = ? AND (name LIKE ? COLLATE NOCASE"
            " OR class_name LIKE ? COLLATE NOCASE) ORDER BY is_active DESC, updated_at DESC"
            " LIMIT 10",
            (campaign_id, like, like),
        )

    async def party(self, campaign_id: int) -> list[Any]:
        return await self._fetchall(
            "SELECT * FROM characters WHERE campaign_id = ? AND is_active = 1"
            " ORDER BY name COLLATE NOCASE",
            (campaign_id,),
        )

    async def update_character(self, character_id: int, **fields: Any) -> Any:
        if not fields:
            return await self.get_character(character_id)
        clean = {k: v for k, v in fields.items() if k in UPDATABLE_CHARACTER_FIELDS}
        if not clean:
            return await self.get_character(character_id)
        assignments = ", ".join(f"{key} = ?" for key in clean)
        await self._write(
            f"UPDATE characters SET {assignments}, updated_at = ? WHERE id = ?",
            list(clean.values()) + [time.time(), character_id],
        )
        return await self.get_character(character_id)

    async def set_active_character(self, character_id: int) -> Any:
        row = await self.get_character(character_id)
        if row is None:
            return None
        await self._write(
            "UPDATE characters SET is_active = 0 WHERE campaign_id = ? AND user_id = ?",
            (row["campaign_id"], row["user_id"]),
        )
        await self._write("UPDATE characters SET is_active = 1 WHERE id = ?", (character_id,))
        return await self.get_character(character_id)

    async def log_event(
        self,
        character_id: int,
        kind: str,
        detail: str = "",
        created_by: int = 0,
    ) -> None:
        await self._write(
            "INSERT INTO character_events (character_id, kind, detail, created_by, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (character_id, kind, detail, created_by, time.time()),
        )

    async def events(self, character_id: int, limit: int = 10) -> list[Any]:
        return await self._fetchall(
            "SELECT * FROM character_events WHERE character_id = ? ORDER BY id DESC LIMIT ?",
            (character_id, limit),
        )

    # -- encounters ------------------------------------------------------
    async def create_encounter(
        self,
        campaign_id: int,
        created_by: int,
        name: str,
        notes: str = "",
        hp_mode: str = "visible",
    ) -> Any:
        encounter_id = await self._insert(
            "INSERT INTO encounters (campaign_id, created_by, name, notes, hp_mode,"
            " status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'draft', ?, ?)",
            (campaign_id, created_by, name, notes, hp_mode, time.time(), time.time()),
        )
        await self.touch_campaign(campaign_id)
        return await self.get_encounter(encounter_id)

    async def get_encounter(self, encounter_id: int) -> Any:
        return await self._fetchone("SELECT * FROM encounters WHERE id = ?", (encounter_id,))

    async def list_encounters(self, campaign_id: int) -> list[Any]:
        return await self._fetchall(
            "SELECT * FROM encounters WHERE campaign_id = ? ORDER BY updated_at DESC",
            (campaign_id,),
        )

    async def find_encounter(self, campaign_id: int, term: str) -> Any:
        """Match an encounter by id, exact name, prefix or substring."""
        if term.isdigit():
            row = await self._fetchone(
                "SELECT * FROM encounters WHERE campaign_id = ? AND id = ?",
                (campaign_id, int(term)),
            )
            if row is not None:
                return row
        return await self._fetchone(
            "SELECT * FROM encounters WHERE campaign_id = ?"
            " AND name LIKE ? COLLATE NOCASE"
            " ORDER BY (name = ? COLLATE NOCASE) DESC, length(name) LIMIT 1",
            (campaign_id, f"{term}%", term),
        )

    async def update_encounter(self, encounter_id: int, **fields: Any) -> Any:
        allowed = {"name", "notes", "hp_mode", "status"}
        clean = {key: value for key, value in fields.items() if key in allowed}
        if clean:
            assignments = ", ".join(f"{key} = ?" for key in clean)
            await self._write(
                f"UPDATE encounters SET {assignments}, updated_at = ? WHERE id = ?",
                list(clean.values()) + [time.time(), encounter_id],
            )
        return await self.get_encounter(encounter_id)

    async def delete_encounter(self, encounter_id: int) -> None:
        await self._write("DELETE FROM encounters WHERE id = ?", (encounter_id,))

    # -- encounter monsters ---------------------------------------------
    async def add_encounter_monster(self, encounter_id: int, **fields: Any) -> Any:
        row = await self._fetchone(
            "SELECT COALESCE(MAX(slot), 0) + 1 AS next FROM encounter_monsters"
            " WHERE encounter_id = ?",
            (encounter_id,),
        )
        slot = int(row["next"]) if row else 1
        defaults: dict[str, Any] = {
            "slot": slot,
            "name": "Monster",
            "srd_index": "",
            "count": 1,
            "ac": 10,
            "max_hp": 1,
            "speed": 30,
            "cr": "",
            "size": "",
            "mtype": "",
            "alignment": "",
            "str": 10, "dex": 10, "con": 10, "intl": 10, "wis": 10, "cha": 10,
            "attack_bonus": 0,
            "damage": "1d6",
            "no_hp": 0,
            "is_boss": 0,
            "resistances": "",
            "immunities": "",
            "condition_immunity": "",
            "hidden": 0,
            "notes": "",
        }
        defaults.update({key: value for key, value in fields.items() if value is not None})
        columns = ", ".join(defaults)
        placeholders = ", ".join("?" * len(defaults))
        monster_id = await self._insert(
            f"INSERT INTO encounter_monsters (encounter_id, {columns})"
            f" VALUES (?, {placeholders})",
            [encounter_id] + list(defaults.values()),
        )
        return await self._get_encounter_monster(monster_id)

    async def _get_encounter_monster(self, monster_id: int) -> Any:
        return await self._fetchone(
            "SELECT * FROM encounter_monsters WHERE id = ?", (monster_id,)
        )

    async def encounter_monsters(self, encounter_id: int) -> list[Any]:
        return await self._fetchall(
            "SELECT * FROM encounter_monsters WHERE encounter_id = ? ORDER BY slot",
            (encounter_id,),
        )

    async def update_encounter_monster(self, monster_id: int, **fields: Any) -> Any:
        allowed = {
            "name", "count", "ac", "max_hp", "speed", "cr", "size", "mtype",
            "alignment", "str", "dex", "con", "intl", "wis", "cha",
            "attack_bonus", "damage", "resistances", "immunities",
            "condition_immunity", "hidden", "notes", "slot", "no_hp", "is_boss",
        }
        clean = {key: value for key, value in fields.items() if key in allowed}
        if clean:
            assignments = ", ".join(f"{key} = ?" for key in clean)
            await self._write(
                f"UPDATE encounter_monsters SET {assignments} WHERE id = ?",
                list(clean.values()) + [monster_id],
            )
        return await self._get_encounter_monster(monster_id)

    async def delete_encounter_monster(self, monster_id: int) -> None:
        await self._write("DELETE FROM encounter_monsters WHERE id = ?", (monster_id,))

    # -- encounter runs --------------------------------------------------
    async def start_encounter_run(
        self, encounter_id: int, campaign_id: int, started_by: int, session_id: int | None
    ) -> Any:
        monsters = await self.encounter_monsters(encounter_id)
        run_id = await self._insert(
            "INSERT INTO encounter_runs (encounter_id, campaign_id, session_id,"
            " started_by, started_at, status) VALUES (?, ?, ?, ?, ?, 'active')",
            (encounter_id, campaign_id, session_id, started_by, time.time()),
        )

        order = 0
        for monster in monsters:
            # A count of 3 gives three independently-tracked units.
            for copy in range(max(1, int(monster["count"] or 1))):
                order += 1
                label = (
                    monster["name"] if int(monster["count"] or 1) <= 1
                    else f"{monster['name']} #{copy + 1}"
                )
                await self._insert(
                    "INSERT INTO encounter_units (run_id, monster_id, label, ac, hp,"
                    " max_hp, attack_bonus, damage, hidden, no_hp, is_boss, status,"
                    " sort_order) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'alive', ?)",
                    (
                        run_id,
                        monster["id"],
                        label,
                        monster["ac"],
                        monster["max_hp"],
                        monster["max_hp"],
                        monster["attack_bonus"],
                        monster["damage"],
                        monster["hidden"],
                        monster["no_hp"],
                        monster["is_boss"],
                        order,
                    ),
                )
        await self.touch_campaign(campaign_id)
        return await self.get_run(run_id)

    async def get_run(self, run_id: int) -> Any:
        return await self._fetchone("SELECT * FROM encounter_runs WHERE id = ?", (run_id,))

    async def active_run(self, campaign_id: int) -> Any:
        return await self._fetchone(
            "SELECT * FROM encounter_runs WHERE campaign_id = ? AND status = 'active'"
            " ORDER BY started_at DESC LIMIT 1",
            (campaign_id,),
        )

    async def end_run(self, run_id: int) -> None:
        await self._write(
            "UPDATE encounter_runs SET status = 'ended', ended_at = ? WHERE id = ?",
            (time.time(), run_id),
        )

    async def recent_runs(self, campaign_id: int, limit: int = 8) -> list[Any]:
        return await self._fetchall(
            "SELECT r.*, e.name AS encounter_name, e.hp_mode FROM encounter_runs r"
            " JOIN encounters e ON e.id = r.encounter_id"
            " WHERE r.campaign_id = ? ORDER BY r.started_at DESC LIMIT ?",
            (campaign_id, limit),
        )

    async def run_units(self, run_id: int) -> list[Any]:
        return await self._fetchall(
            "SELECT * FROM encounter_units WHERE run_id = ? ORDER BY sort_order",
            (run_id,),
        )

    async def get_unit(self, unit_id: int) -> Any:
        return await self._fetchone("SELECT * FROM encounter_units WHERE id = ?", (unit_id,))

    async def find_unit(self, run_id: int, selector: str) -> Any:
        """Find a unit by 1-based position, exact label, or name fragment."""
        rows = await self.run_units(run_id)
        alive = [row for row in rows if row["status"] == "alive"]
        selector = selector.strip()
        if selector.isdigit():
            position = int(selector)
            if 1 <= position <= len(alive):
                return alive[position - 1]
            if 1 <= position <= len(rows):
                return rows[position - 1]
            return None
        needle = selector.lower()
        for row in alive + [r for r in rows if r["status"] != "alive"]:
            if row["label"].lower() == needle:
                return row
        for row in alive:
            if needle in row["label"].lower():
                return row
        return None

    async def damage_unit(
        self, unit_id: int, amount: int, actor_id: int, detail: str = ""
    ) -> Any:
        """Apply damage to a unit, clamp at zero and mark it dead.

        A ``no_hp`` unit has no hit points to remove: the damage is recorded
        but it stays standing until the DM kills it. That models a monster
        such as a wraith that only goes down on the DM's word.
        """
        unit = await self.get_unit(unit_id)
        if unit is None:
            raise LookupError("That combatant is not in this encounter.")

        remaining = unit["hp"]
        status = unit["status"]
        if unit["no_hp"]:
            remaining = unit["max_hp"]  # untouched; damage is logged only
        else:
            remaining = max(0, unit["hp"] - amount)
            status = "dead" if remaining <= 0 else unit["status"]
            await self._write(
                "UPDATE encounter_units SET hp = ?, status = ? WHERE id = ?",
                (remaining, status, unit_id),
            )

        await self._insert(
            "INSERT INTO encounter_damage (run_id, unit_id, actor_id, amount, detail,"
            " remaining, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (unit["run_id"], unit_id, actor_id, amount, detail, remaining, time.time()),
        )
        return await self.get_unit(unit_id)

    async def kill_unit(self, unit_id: int, actor_id: int, detail: str = "killed by the DM") -> Any:
        """Drop a combatant regardless of its hit points.

        The only way to finish a ``no_hp`` unit.
        """
        unit = await self.get_unit(unit_id)
        if unit is None:
            raise LookupError("That combatant is not in this encounter.")
        if unit["status"] == "dead":
            return unit
        await self._write(
            "UPDATE encounter_units SET status = 'dead', hp = 0 WHERE id = ?", (unit_id,)
        )
        await self._insert(
            "INSERT INTO encounter_damage (run_id, unit_id, actor_id, amount, detail,"
            " remaining, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (unit["run_id"], unit_id, actor_id, 0, detail, 0, time.time()),
        )
        return await self.get_unit(unit_id)

    async def heal_unit(self, unit_id: int, amount: int, actor_id: int, detail: str = "") -> Any:
        unit = await self.get_unit(unit_id)
        if unit is None:
            raise LookupError("That combatant is not in this encounter.")
        healed = min(unit["max_hp"], unit["hp"] + amount)
        status = "alive" if healed > 0 else "dead"
        await self._write(
            "UPDATE encounter_units SET hp = ?, status = ? WHERE id = ?",
            (healed, status, unit_id),
        )
        await self._insert(
            "INSERT INTO encounter_damage (run_id, unit_id, actor_id, amount, detail,"
            " remaining, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (unit["run_id"], unit_id, actor_id, -amount, detail or "healed", healed, time.time()),
        )
        return await self.get_unit(unit_id)

    async def unit_damage(self, unit_id: int, limit: int = 40) -> list[Any]:
        return await self._fetchall(
            "SELECT * FROM encounter_damage WHERE unit_id = ? ORDER BY id DESC LIMIT ?",
            (unit_id, limit),
        )

    async def unit_damage_totals(self, run_id: int) -> dict[int, int]:
        """unit_id -> damage dealt, for a quick summary."""
        rows = await self._fetchall(
            "SELECT unit_id, SUM(amount) AS total FROM encounter_damage"
            " WHERE run_id = ? GROUP BY unit_id",
            (run_id,),
        )
        return {int(row["unit_id"]): int(row["total"] or 0) for row in rows}

    async def set_unit_visibility(self, run_id: int, monster_id: int, hidden: bool) -> int:
        """Hide or reveal every live unit spawned from one template monster.

        Lets the DM reveal an ambush mid-fight with the same ``/ms N show``
        they used while preparing it. Returns how many units changed.
        """
        cur = await self._write(
            "UPDATE encounter_units SET hidden = ? WHERE run_id = ? AND monster_id = ?",
            (int(hidden), run_id, monster_id),
        )
        return cur

    async def set_monster_visibility(self, encounter_id: int, monster_id: int, hidden: bool) -> int:
        cur = await self._write(
            "UPDATE encounter_monsters SET hidden = ? WHERE encounter_id = ? AND id = ?",
            (int(hidden), encounter_id, monster_id),
        )
        return cur

    async def set_unit_no_hp(self, run_id: int, monster_id: int, value: int) -> int:
        """Mirror the no-HP flag onto the live units of a running fight."""
        return await self._write(
            "UPDATE encounter_units SET no_hp = ? WHERE run_id = ? AND monster_id = ?",
            (value, run_id, monster_id),
        )

    async def set_unit_boss(self, run_id: int, monster_id: int, value: int) -> int:
        """Mirror the boss flag onto the live units of a running fight."""
        return await self._write(
            "UPDATE encounter_units SET is_boss = ? WHERE run_id = ? AND monster_id = ?",
            (value, run_id, monster_id),
        )


db = Database()
