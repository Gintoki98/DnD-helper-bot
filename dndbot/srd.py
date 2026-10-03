"""Client for the dnd5eapi SRD (5.2.1 / 2014 content).

The public API is a free, rate-limited read-only source, so this client keeps
two caches:

* **index cache** - the ``name -> slug`` listing per category, kept in memory
  and refreshed at most once per :data:`INDEX_TTL`;
* **detail cache** - every fetched entry is written to ``.cache/`` as JSON and
  reused for :data:`config.SRD_CACHE_TTL` seconds.

Only two endpoints are needed: the list endpoint for a category and the detail
URL that each list entry advertises.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

import aiohttp

from . import config

INDEX_TTL = 60 * 60 * 12
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
MAX_RETRIES = 3


@dataclass(frozen=True)
class Category:
    key: str
    path: str
    label: str
    emoji: str


CATEGORIES: dict[str, Category] = {
    "monsters": Category("monsters", "monsters", "Monsters", "\U0001f409"),
    "spells": Category("spells", "spells", "Spells", "\U0001f52e"),
    "magicitems": Category("magicitems", "magic-items", "Magic items", "\U0001f4b0"),
    "equipment": Category("equipment", "equipment", "Equipment", "\U0001f6e1"),
    "rules": Category("rules", "rules", "Rules", "⚖"),
    "classes": Category("classes", "classes", "Classes", "\U0001f977"),
    "races": Category("races", "races", "Races", "\U0001f9d9"),
    "subraces": Category("subraces", "subraces", "Subraces", "\U0001f9d9"),
    "conditions": Category("conditions", "conditions", "Conditions", "\U0001f912"),
    "proficiencies": Category("proficiencies", "proficiencies", "Proficiencies", "\U0001f91d"),
    "skills": Category("skills", "skills", "Skills", "\U0001f31f"),
    "damagetypes": Category("damagetypes", "damage-types", "Damage types", "\U0001f4a3"),
    "alignments": Category("alignments", "alignments", "Alignments", "\u272d"),
}

# Free-text users type; the command layer normalises them before lookup.
CATEGORY_ALIASES: dict[str, str] = {
    "monster": "monsters", "mob": "monsters", "beast": "monsters", "creature": "monsters",
    "bestiary": "monsters", "foe": "monsters", "enemy": "monsters", "npc": "monsters",
    "spell": "spells", "magic": "spells",
    "item": "magicitems", "magicitem": "magicitems", "magic_item": "magicitems",
    "loot": "magicitems", "gear": "equipment", "weapon": "equipment",
    "armor": "equipment", "armour": "equipment",     "equipment": "equipment",
    "rule": "rules", "rules": "rules", "help": "rules", "faq": "rules",
    "class": "classes", "race": "races", "subrace": "subraces",
    "condition": "conditions", "status": "conditions",
    "skill": "skills", "prof": "proficiencies", "proficiency": "proficiencies",
    "damagetype": "damagetypes", "alignment": "alignments",
}

# What /search fans out to, most-searched categories first.
SEARCH_CATEGORIES: tuple[str, ...] = (
    "spells", "monsters", "magicitems", "equipment",
    "rules", "classes", "races", "conditions",
)

# Entries shown per page in the browser: the bot's pager and srd_list agree.
BROWSE_PAGE = 8


class SRDError(RuntimeError):
    """Raised when the SRD API cannot be reached or returns nonsense."""


def resolve_category(name: str) -> Category | None:
    key = (name or "").strip().lower().lstrip("/")
    if not key:
        return None
    key = CATEGORY_ALIASES.get(key, key)
    return CATEGORIES.get(key)


def slugify(text: str) -> str:
    text = re.sub(r"[’'`]", "", (text or "").lower())
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


@dataclass
class Entry:
    """One row from a category listing."""

    index: str
    name: str
    url: str
    category: str
    level: Any = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def path(self) -> str:
        return f"{self.category}/{self.index}"


class SRDClient:
    def __init__(
        self,
        base: str | None = None,
        version: str | None = None,
        cache_dir: Path | None = None,
    ) -> None:
        self.base = (base or config.SRD_API_BASE).rstrip("/")
        self.version = version or config.SRD_API_VERSION
        self.cache_dir = Path(cache_dir or config.CACHE_DIR)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._session: aiohttp.ClientSession | None = None
        self._indexes: dict[str, tuple[float, list[Entry]]] = {}
        self._details: dict[str, tuple[float, dict]] = {}
        self._ratings: tuple[float, dict[str, float]] | None = None
        self._lock = asyncio.Lock()
        self._ready: set[str] = set()

    # -- lifecycle ------------------------------------------------------
    async def start(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=REQUEST_TIMEOUT,
                headers={"User-Agent": "dnd-helper-bot/1.0 (SRD reader)"},
            )

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    # -- http -----------------------------------------------------------
    async def _retry(self, once: Callable[[], Awaitable[Any]]) -> Any:
        """Run ``once`` - one HTTP attempt - under the client's retry policy."""
        last: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return await once()
            except (aiohttp.ClientError, asyncio.TimeoutError, SRDError) as exc:
                last = exc
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(1.5 * attempt)
        raise SRDError(f"Could not reach the SRD API: {last}")

    async def _get(self, url: str) -> Any:
        if self._session is None:
            await self.start()
        assert self._session is not None

        async def once() -> Any:
            async with self._session.get(url) as response:
                if response.status == 404:
                    raise SRDError(f"The SRD API has no entry at {url}")
                if response.status == 429 or response.status >= 500:
                    raise SRDError(f"SRD API is unhappy ({response.status})")
                response.raise_for_status()
                return await response.json(content_type=None)

        return await self._retry(once)

    async def _post_json(self, url: str, payload: Any) -> Any:
        """POST a JSON body and decode the reply, under the same retry policy."""
        if self._session is None:
            await self.start()
        assert self._session is not None

        async def once() -> Any:
            async with self._session.post(url, json=payload) as response:
                if response.status == 404:
                    raise SRDError(f"The SRD API has no endpoint at {url}")
                if response.status == 429 or response.status >= 500:
                    raise SRDError(f"SRD API is unhappy ({response.status})")
                response.raise_for_status()
                return await response.json(content_type=None)

        return await self._retry(once)

    def _api(self, path: str) -> str:
        return f"{self.base}/api/{self.version}/{path.lstrip('/')}"

    def _abs(self, url: str) -> str:
        return self.base + url if url.startswith("/") else f"{self.base}/{url}"

    # -- disk cache -----------------------------------------------------
    def _cache_file(self, category: str, index: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", index) or "entry"
        return self.cache_dir / category / f"{safe}.json"

    def _read_cache(self, path: Path, ttl: int) -> Any | None:
        if not path.exists():
            return None
        if ttl and time.time() - path.stat().st_mtime > ttl:
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _write_cache(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            pass

    # -- indexes --------------------------------------------------------
    async def index(self, category: str | Category) -> list[Entry]:
        """All entries of a category, cached in memory and on disk."""
        key = category.key if isinstance(category, Category) else category
        cat = category if isinstance(category, Category) else CATEGORIES.get(key)
        if cat is None:
            raise SRDError(f"Unknown SRD category {key!r}")

        cached = self._indexes.get(cat.key)
        if cached and time.time() - cached[0] < INDEX_TTL:
            return cached[1]

        disk = self.cache_dir / f"__index_{cat.key}.json"
        payload = self._read_cache(disk, INDEX_TTL)
        if payload is None:
            data = await self._get(self._api(f"{cat.path}/?limit=1000"))
            payload = data.get("results", [])
            self._write_cache(disk, payload)

        entries = [
            Entry(
                index=item.get("index", ""),
                name=item.get("name", ""),
                url=item.get("url", f"/api/{self.version}/{cat.path}/{item.get('index','')}"),
                category=cat.key,
                level=item.get("level"),
                extra={
                    k: v
                    for k, v in item.items()
                    if k not in {"index", "name", "url", "level"}
                },
            )
            for item in payload
            if item.get("index")
        ]
        self._indexes[cat.key] = (time.time(), entries)
        return entries

    async def warm(self, categories: Iterable[str] = ("monsters", "spells", "magicitems")) -> set[str]:
        """Preload a few indexes at start-up; returns the ones that loaded."""
        loaded: set[str] = set()
        for key in categories:
            try:
                await self.index(key)
                loaded.add(key)
            except SRDError:
                continue
        return loaded

    # -- searching ------------------------------------------------------
    async def search(self, category: str, term: str, limit: int = 10) -> list[Entry]:
        """Ranked search across a category. Returns [] when nothing is close."""
        entries = await self.index(category)
        if not term:
            return entries[:limit]

        needle = term.strip().lower()
        slug = slugify(needle)
        names = [e.name.lower() for e in entries]

        exact = [e for e, n in zip(entries, names) if n == needle]
        slugged = [e for e in entries if e.index == slug]
        prefixed = [e for e, n in zip(entries, names) if n.startswith(needle)]
        contained = [e for e, n in zip(entries, names) if needle in n]
        fuzzy = [
            entries[names.index(close)]
            for close in difflib.get_close_matches(needle, names, n=limit * 2, cutoff=0.6)
        ]

        seen: set[str] = set()
        ranked: list[Entry] = []
        for bucket in (exact, slugged, prefixed, contained, fuzzy):
            for entry in bucket:
                if entry.index not in seen:
                    seen.add(entry.index)
                    ranked.append(entry)
        return ranked[:limit]

    async def resolve(self, category: str, term: str) -> tuple[Entry | None, list[Entry]]:
        """Best match for a term, plus alternatives when nothing matches well."""
        matches = await self.search(category, term, limit=6)
        if not matches:
            return None, []
        needle = term.strip().lower()
        slug = slugify(needle)
        for entry in matches:
            if entry.index == slug or entry.name.lower() == needle:
                return entry, [e for e in matches if e.index != entry.index]
        return matches[0], matches[1:]

    # -- details --------------------------------------------------------
    async def detail(self, entry: Entry | str, category: str | None = None) -> dict:
        """Full record for an entry, served from cache when possible."""
        if isinstance(entry, str):
            if not category:
                raise SRDError("detail() needs a category when given a bare index")
            entry = Entry(index=entry, name=entry, url="", category=category)

        key = entry.path
        cached = self._details.get(key)
        if cached and time.time() - cached[0] < config.SRD_CACHE_TTL:
            return cached[1]

        path = self._cache_file(entry.category, entry.index)
        payload = self._read_cache(path, config.SRD_CACHE_TTL)
        if payload is None:
            url = self._abs(entry.url) if entry.url else self._api(
                f"{CATEGORIES[entry.category].path}/{entry.index}/"
            )
            payload = await self._get(url)
            self._write_cache(path, payload)

        self._details[key] = (time.time(), payload)
        return payload

    async def get(self, category: str, term: str) -> tuple[Entry, dict]:
        """Search then fetch - the main read path used by the commands."""
        entry, _alternatives = await self.resolve(category, term)
        if entry is None:
            raise SRDError(f"No {CATEGORIES[category].label.lower()} matching {term!r}")
        return entry, await self.detail(entry)

    async def follow(self, url: str) -> Any:
        """Fetch a nested sub-resource a record points at, e.g. a class's levels.

        Some records only carry a URL (``class_levels``) rather than the data.
        """
        if not url:
            return None
        key = f"sub:{url}"
        cached = self._details.get(key)
        if cached and time.time() - cached[0] < config.SRD_CACHE_TTL:
            return cached[1]

        parts = [p for p in url.rstrip("/").split("/") if p]
        path = self.cache_dir / "__sub" / (("_".join(parts[-3:]) or "root") + ".json")
        payload = self._read_cache(path, config.SRD_CACHE_TTL)
        if payload is None:
            payload = await self._get(self._abs(url))
            self._write_cache(path, payload)
        self._details[key] = (time.time(), payload)
        return payload

    async def with_class_levels(self, data: dict) -> dict:
        """Inline a class's level table so the formatter can read it."""
        levels = data.get("class_levels")
        if isinstance(levels, str) and levels:
            try:
                data = {**data, "class_levels": await self.follow(levels)}
            except SRDError:
                data = {**data, "class_levels": []}
        return data

    async def random_entry(
        self, category: str, level: int | None = None
    ) -> tuple[Entry, dict]:
        """A random entry, optionally filtered by spell level."""
        entries = await self.index(category)
        pool = [e for e in entries if level is None or e.level == level] or entries
        if not pool:
            raise SRDError("Nothing to pick from in that category")
        pick = pool[random.randrange(len(pool))]
        return pick, await self.detail(pick)

    async def challenge_ratings(self) -> dict[str, float]:
        """``index -> challenge rating`` for the whole bestiary, cached like an index.

        The REST listing hides ``challenge_rating`` - only spell levels ride on
        the index - but the same API's GraphQL endpoint hands back every
        monster's rating in one small response, so this costs one round-trip
        instead of a detail fetch per monster.
        """
        if self._ratings and time.time() - self._ratings[0] < INDEX_TTL:
            return self._ratings[1]

        payload = await self._post_json(
            f"{self.base}/graphql",
            {"query": "{ monsters(limit: 1000) { index challenge_rating } }"},
        )
        if payload.get("errors"):
            raise SRDError(f"The SRD GraphQL API said: {payload['errors']}")
        rows = (payload.get("data") or {}).get("monsters") or []

        ratings: dict[str, float] = {}
        for row in rows:
            try:
                ratings[row["index"]] = float(row["challenge_rating"])
            except (KeyError, TypeError, ValueError):
                continue
        if not ratings:
            raise SRDError("The SRD GraphQL API returned no monsters")
        self._ratings = (time.time(), ratings)
        return ratings

    async def random_by_cr(self, low: float, high: float) -> tuple[Entry, dict]:
        """A monster whose challenge rating sits in ``[low, high]``, inclusive."""
        entries = await self.index("monsters")
        ratings = await self.challenge_ratings()
        pool = [
            entry
            for entry in entries
            if entry.index in ratings and low <= ratings[entry.index] <= high
        ]
        if not pool:
            span = f"{low:g}-{high:g}" if low != high else f"{low:g}"
            raise SRDError(f"No monsters with CR {span} in the SRD. Try a wider range.")
        pick = pool[random.randrange(len(pool))]
        return pick, await self.detail(pick)

    async def search_all(
        self, term: str, limit: int = 12, per_category: int = 4
    ) -> list[tuple[str, Entry]]:
        """The best hits across every category players actually search.

        Categories are tried in :data:`SEARCH_CATEGORIES` order, a failing
        one is skipped, and ``limit`` caps the total so a broad word cannot
        flood the list.
        """
        hits: list[tuple[str, Entry]] = []
        for key in SEARCH_CATEGORIES:
            try:
                results = await self.search(key, term, limit=per_category)
            except SRDError:
                continue
            for entry in results:
                if len(hits) >= limit:
                    return hits
                hits.append((key, entry))
        return hits


srd = SRDClient()
