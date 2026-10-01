#!/usr/bin/env python3
"""Start the D&D helper bot.

    python bot.py

Credentials come from .env (see .env.example).
"""

import asyncio
import sys

from dndbot.__main__ import main
from dndbot.config import ConfigError


def run() -> int:
    try:
        return asyncio.run(main())
    except ConfigError as exc:
        print(f"\nConfiguration problem: {exc}\n")
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130


if __name__ == "__main__":
    sys.exit(run())
