"""Server lifecycle: bring up what the tools need, tear it down cleanly.

The mirror of ``dndbot.__main__.main()`` without the Telethon half - no
login, no command publishing, no client. The database and the SRD cache are
shared with the bot (SQLite already runs in WAL mode, same ``.cache/``), so
both processes can run side by side and neither duplicates the other's data.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

from mcp.server import MCPServer

from .. import config
from ..srd import srd
from ..storage import db

log = logging.getLogger("dndbot.mcp")


@dataclass
class AppContext:
    """State handed to every tool through ``Context``.

    Empty on purpose: the tools read the same singletons (``db``, ``srd``) the
    handlers do. The field stays so something can be added later without
    changing the lifespan signature.
    """


@asynccontextmanager
async def lifespan(server: MCPServer) -> AsyncIterator[AppContext]:
    """Open the database and the SRD cache for the life of the server."""
    await db.connect()
    await srd.start()
    warmed = await srd.warm(("monsters", "spells", "magicitems", "rules"))
    log.info("SRD indexes ready: %s", ", ".join(sorted(warmed)) or "none (offline)")

    expired = await db.expire_stale_requests(config.REQUEST_TTL)
    if expired:
        log.info("expired %s stale join request(s)", expired)

    try:
        yield AppContext()
    finally:
        await srd.close()
        await db.close()
