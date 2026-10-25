"""database.py — فقط دادهٔ بازار (کندل یک‌دقیقه‌ای) برای رصد و بک‌تست.

سیگنال‌ها در CSV هستند (signal_store.py)، نه اینجا.
DB file: data/titan_signal.db  →  جدول market_bars
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import (
    Column, Integer, String, Float, DateTime, create_engine, UniqueConstraint, text,
)
from sqlalchemy.orm import declarative_base, sessionmaker

logger = logging.getLogger(__name__)

_raw = (os.getenv("TITAN_DATABASE_URL") or "").strip()
DB_URL = _raw if _raw else "sqlite:///data/titan_signal.db"

os.makedirs("data", exist_ok=True)

connect_args = {"check_same_thread": False} if DB_URL.startswith("sqlite") else {}
engine = create_engine(DB_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class MarketBar(Base):
    """OHLCV bar — primarily 1-minute for monitoring / backtest."""
    __tablename__ = "market_bars"
    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "ts_unix", name="uq_bar"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(32), nullable=False, index=True)
    timeframe = Column(String(8), nullable=False, default="1m", index=True)
    ts_unix = Column(Integer, nullable=False, index=True)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, default=0.0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


def get_session():
    return SessionLocal()


def init_db():
    """Create market_bars table (and ignore legacy signal tables if present)."""
    Base.metadata.create_all(engine)
    # best-effort: ensure folder exists
    if DB_URL.startswith("sqlite:///"):
        path = DB_URL.replace("sqlite:///", "", 1)
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
    logger.info("Market DB initialized: %s (table=market_bars)", DB_URL)


def save_candles(symbol: str, timeframe: str, candles: list) -> int:
    """Upsert list of candles [{t,o,h,l,c,v}, ...]. Returns inserted/updated count."""
    if not candles:
        return 0
    session = get_session()
    n = 0
    try:
        for c in candles:
            ts = int(c.get("t") or c.get("ts") or 0)
            if not ts:
                continue
            existing = (
                session.query(MarketBar)
                .filter_by(symbol=symbol, timeframe=timeframe, ts_unix=ts)
                .first()
            )
            o = float(c.get("o") or c.get("open") or 0)
            h = float(c.get("h") or c.get("high") or 0)
            l = float(c.get("l") or c.get("low") or 0)
            cl = float(c.get("c") or c.get("close") or 0)
            v = float(c.get("v") or c.get("volume") or 0)
            if existing:
                existing.open, existing.high, existing.low = o, h, l
                existing.close, existing.volume = cl, v
            else:
                session.add(MarketBar(
                    symbol=symbol, timeframe=timeframe, ts_unix=ts,
                    open=o, high=h, low=l, close=cl, volume=v,
                ))
            n += 1
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error("save_candles error %s %s: %s", symbol, timeframe, e)
        return 0
    finally:
        session.close()
    return n


def get_candles(
    symbol: str,
    timeframe: str = "1m",
    start_unix: Optional[int] = None,
    end_unix: Optional[int] = None,
    limit: int = 1000,
) -> List[dict]:
    session = get_session()
    try:
        q = session.query(MarketBar).filter(
            MarketBar.symbol == symbol,
            MarketBar.timeframe == timeframe,
        )
        if start_unix:
            q = q.filter(MarketBar.ts_unix >= int(start_unix))
        if end_unix:
            q = q.filter(MarketBar.ts_unix <= int(end_unix))
        rows = q.order_by(MarketBar.ts_unix.asc()).limit(limit).all()
        return [
            {"t": r.ts_unix, "o": r.open, "h": r.high, "l": r.low, "c": r.close, "v": r.volume}
            for r in rows
        ]
    finally:
        session.close()


# --- compatibility shims so old imports don't crash ---
def save_daily_summary(*args, **kwargs):
    logger.debug("save_daily_summary skipped (signals are CSV-only)")


def save_signal(*args, **kwargs):
    from .signal_store import save_signal as csv_save
    return csv_save(**kwargs)


def get_open_signals(days=7):
    from .signal_store import get_open_signals as csv_opens
    return csv_opens(days=days)


def has_open_signal(*args, **kwargs):
    from .signal_store import has_open_signal as csv_has
    return csv_has(*args, **kwargs)


def update_signal_exit(*args, **kwargs):
    from .signal_store import update_signal_exit as csv_exit
    return csv_exit(*args, **kwargs)


class Signal:
    """Deprecated placeholder — signals live in CSV."""
    pass
