"""SQLite persistence for campaigns, sessions and characters.

Every method is a coroutine backed by ``aiosqlite`` so the Telethon event loop
stays responsive.  A single connection is shared and guarded by a lock, which
is plenty for a table-sized group chat.
"""

from __future__ import annotations

import asyncio
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
"""


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
        await self._db.commit()

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


db = Database()
