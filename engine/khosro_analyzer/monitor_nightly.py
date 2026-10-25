"""Compatibility entry point. The active monitor is bot.py and resolves OPEN trades on 1m candles."""
import asyncio
from bot import main_async

if __name__ == "__main__":
    asyncio.run(main_async())
