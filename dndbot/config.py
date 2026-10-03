"""Configuration loading from environment / .env."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# dndbot/ - this package
PACKAGE_DIR = Path(__file__).resolve().parent
# the project root, which holds .env, requirements.txt and bot.py
PROJECT_ROOT = PACKAGE_DIR.parent

# Load .env before reading any environment variable, so values in the file win.
load_dotenv(PROJECT_ROOT / ".env")


def _int(name: str, default: int | None = None) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


DATA_DIR = PACKAGE_DIR / "data"
CACHE_DIR = PROJECT_ROOT / ".cache"
SESSION_NAME = "dnd_helper"
SESSION_PATH = PROJECT_ROOT / SESSION_NAME

LOG_DIR = Path(os.getenv("LOG_DIR", str(PROJECT_ROOT / "logs")))
LOG_FILE = LOG_DIR / "bot.log"
# Rotate at 5 MB, keeping three previous files (bot.log.1 .. bot.log.3).
LOG_MAX_BYTES = _int("LOG_MAX_BYTES", 5 * 1024 * 1024) or 5 * 1024 * 1024
LOG_BACKUPS = _int("LOG_BACKUPS", 3) or 3
LOG_LEVEL = (os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO")
if LOG_LEVEL not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
    LOG_LEVEL = "INFO"

API_ID: int | None = _int("API_ID")
API_HASH: str = os.getenv("API_HASH", "").strip()
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID: int | None = _int("ADMIN_ID")
# Default identity for MCP tools that act on behalf of a player. Optional: a
# tool call may always pass an explicit user_id instead.
MCP_USER_ID: int | None = _int("MCP_USER_ID")

SRD_API_BASE: str = os.getenv("SRD_API_BASE", "https://www.dnd5eapi.co").rstrip("/")
SRD_API_VERSION: str = os.getenv("SRD_API_VERSION", "2014").strip()
SRD_CACHE_TTL: int = _int("SRD_CACHE_TTL", 60 * 60 * 24 * 7) or 0

DEFAULT_SYSTEM: str = os.getenv("DEFAULT_SYSTEM", "5e").strip()
REQUEST_TTL: int = _int("REQUEST_TTL", 86400) or 86400

DB_PATH: Path = Path(os.getenv("DB_PATH", str(DATA_DIR / "dndbot.db")))

DATA_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)


class ConfigError(RuntimeError):
    """Raised when required configuration is missing."""


def check(telegram: bool = True) -> None:
    """Fail fast with a friendly message when credentials are absent.

    ``telegram=False`` is the MCP server's path: it shares the database and
    the SRD cache with the bot but signs into Telegram never, so the three
    credentials below do not apply to it.
    """
    if not telegram:
        return
    missing = []
    if not API_ID:
        missing.append("API_ID")
    if not API_HASH:
        missing.append("API_HASH")
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if missing:
        raise ConfigError(
            "Missing " + ", ".join(missing)
            + " in .env - copy .env.example and fill it in."
        )
