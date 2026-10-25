"""Download and maintain the rolling 1m OHLCV SQLite database.

Usage examples:
  python collect_1m_data.py                 # fill up to retention window + catch up to now
  python collect_1m_data.py --days 12       # ensure ~12 days of history exist
  python collect_1m_data.py --symbol BTC-USDT --days 12
  python collect_1m_data.py --force --days 12   # wipe DB then full backfill
  python collect_1m_data.py --loop --interval 120
"""
from __future__ import annotations

import argparse
import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

from bot import fetch_klines
from candle_store import (
    upsert_candles,
    prune_old_candles,
    enforce_db_size_limit,
    database_stats,
    latest_timestamp,
    earliest_timestamp,
)
from config import SYMBOLS, CANDLE_RETENTION_DAYS, CANDLE_DB_PATH


async def _fetch_range(session, symbol, start, end):
    if start >= end - 60:
        return 0
    candles, ok = await fetch_klines(session, symbol, "1m", start, end)
    if not ok:
        print(f"{symbol}: download failed for range {start}-{end}")
        return 0
    n = upsert_candles({symbol: candles})
    return n


async def sync_symbol(session, symbol, days):
    """Ensure [now-days, now] is covered: backfill missing history AND catch up to now."""
    now = int(datetime.now(timezone.utc).timestamp())
    target_start = now - int(days) * 86400
    earliest = earliest_timestamp(symbol)
    latest = latest_timestamp(symbol)
    total = 0

    # 1) History gap: nothing yet, or data starts too late
    if earliest is None:
        n = await _fetch_range(session, symbol, target_start, now)
        total += n
        print(f"{symbol}: full backfill {n} 1m candles")
        return total

    if earliest > target_start + 120:
        n = await _fetch_range(session, symbol, target_start, earliest - 60)
        total += n
        print(f"{symbol}: history gap filled {n} 1m candles (before {earliest})")

    # 2) Forward catch-up from last known candle
    latest = latest_timestamp(symbol)
    if latest is not None and latest < now - 120:
        n = await _fetch_range(session, symbol, latest + 60, now)
        total += n
        if n:
            print(f"{symbol}: catch-up {n} 1m candles (after {latest})")
    elif total == 0:
        print(f"{symbol}: already covers ~{days}d window, nothing to sync")

    return total


async def sync_all(symbols, days):
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=8)) as session:
        total = 0
        for symbol in symbols:
            try:
                total += await sync_symbol(session, symbol, days)
            except Exception as exc:
                print(f"{symbol}: ERROR {exc}")
    deleted = prune_old_candles()
    guard = enforce_db_size_limit()
    stats = database_stats()
    print(f"Collector cycle complete: synced={total}, pruned={deleted}, "
          f"size={guard['size_mb']}MB, guard={guard['actions'] or 'ok'}, stats={stats}")
    return total


def _wipe_db():
    path = Path(CANDLE_DB_PATH)
    for suffix in ("", "-wal", "-shm", "-journal"):
        p = Path(str(path) + suffix) if suffix else path
        if p.exists():
            p.unlink()
            print(f"removed {p}")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="", help="Optional symbol, e.g. BTC-USDT")
    ap.add_argument("--days", type=int, default=CANDLE_RETENTION_DAYS)
    ap.add_argument("--force", action="store_true", help="Delete market_data.db and full backfill")
    ap.add_argument("--loop", action="store_true", help="Keep running as a separate collector process")
    ap.add_argument("--interval", type=int, default=120, help="Seconds between collector cycles")
    args = ap.parse_args()
    if args.interval < 60:
        ap.error("--interval must be at least 60 seconds")
    if args.force:
        _wipe_db()
    symbols = [args.symbol] if args.symbol else list(SYMBOLS)
    if not args.loop:
        await sync_all(symbols, args.days)
        return
    print(
        f"1m collector started: {len(symbols)} symbols, interval={args.interval}s, "
        f"retention={CANDLE_RETENTION_DAYS}d"
    )
    while True:
        started = time.monotonic()
        try:
            await sync_all(symbols, args.days)
        except Exception as exc:
            print(f"Collector cycle failed: {exc}")
        elapsed = time.monotonic() - started
        await asyncio.sleep(max(1, args.interval - elapsed))


if __name__ == "__main__":
    asyncio.run(main())
