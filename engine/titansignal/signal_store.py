"""signal_store.py — صدور و مدیریت سیگنال‌ها فقط در CSV.

data/signals/YYYY-MM-DD.csv  →  سیگنال‌ها (OPEN/CLOSED، تریل، message_id)
data/titan_signal.db         →  فقط کندل یک‌دقیقه‌ای ارزها (جدا در database.py)
"""
from __future__ import annotations

import csv
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

SIGNALS_DIR = "data/signals"
TEHRAN_TZ = ZoneInfo("Asia/Tehran")

CSV_HEADERS = [
    "signal_id",
    "symbol", "direction", "scenario_id", "scenario_name",
    "entry_price", "stop_loss", "take_profit",
    "issued_at_tehran", "status", "outcome",
    "hit_time_tehran", "hit_price",
    "broker_fee", "final_pnl_usd", "position_size_usd", "return_pct",
    "signal_source",
    "telegram_message_id",
    "initial_sl", "trail_activate", "trail_lock", "sl_pct",
    "leverage", "margin_usd", "position_usd", "max_hold_candles",
]


@dataclass
class OpenSignal:
    """Compatible with trailing.evaluate_signal attribute access."""
    id: str
    symbol: str
    direction: str
    scenario_id: str
    scenario_name: str
    entry_price: float
    stop_loss: float
    initial_sl: float
    issued_at_tehran: str
    telegram_message_id: Optional[int] = None
    trail_activate: float = 0.003
    trail_lock: float = 0.90
    sl_pct: float = 0.04
    leverage: int = 10
    margin_usd: float = 10.0
    position_usd: float = 100.0
    max_hold_candles: int = 72
    position_size_usd: float = 10.0
    status: str = "OPEN"
    outcome: str = ""
    take_profit: float = 0.0

    @property
    def issued_at(self):
        try:
            dt = datetime.strptime(self.issued_at_tehran[:19], "%Y-%m-%d %H:%M:%S")
            return dt.replace(tzinfo=TEHRAN_TZ).astimezone(timezone.utc)
        except Exception:
            return datetime.now(timezone.utc)

    @property
    def issued_at_unix(self) -> int:
        """UTC unix timestamp for trailing candle window (required by trailing.py)."""
        try:
            return int(self.issued_at.timestamp())
        except Exception:
            return int(datetime.now(timezone.utc).timestamp())


def ensure_dir():
    os.makedirs(SIGNALS_DIR, exist_ok=True)


def tehran_date_str(dt=None) -> str:
    now = datetime.now(TEHRAN_TZ) if dt is None else dt.astimezone(TEHRAN_TZ)
    return now.strftime("%Y-%m-%d")


def tehran_time_str(dt=None) -> str:
    now = datetime.now(TEHRAN_TZ) if dt is None else dt.astimezone(TEHRAN_TZ)
    return now.strftime("%Y-%m-%d %H:%M:%S")


def daily_csv_path(date_str: str = None) -> str:
    ensure_dir()
    d = tehran_date_str() if date_str is None else date_str
    return os.path.join(SIGNALS_DIR, f"{d}.csv")


def _fmt(v, nd=8) -> str:
    if v is None or v == "":
        return ""
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return str(v)


def _next_signal_id() -> str:
    """Monotonic id: YYYYMMDD-HHMMSS-NNN based on time + count."""
    now = datetime.now(TEHRAN_TZ)
    base = now.strftime("%Y%m%d-%H%M%S")
    path = daily_csv_path()
    n = 0
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                n = max(0, sum(1 for _ in f) - 1)
        except Exception:
            n = 0
    return f"{base}-{n+1:03d}"


def _read_csv(path: str) -> tuple:
    if not os.path.isfile(path):
        return list(CSV_HEADERS), []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or CSV_HEADERS)
        for h in CSV_HEADERS:
            if h not in fields:
                fields.append(h)
        rows = list(reader)
    return fields, rows


def _write_csv(path: str, fields: list, rows: list) -> None:
    ensure_dir()
    with open(path, mode="w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({h: row.get(h, "") for h in fields})


def append_signal_row(
    symbol: str, direction: str, entry_price: float,
    stop_loss: float, take_profit: float, issued_at_tehran: str,
    signal_source: str, scenario_id: str = "", scenario_name: str = "",
    position_size_usd: float = 10.0,
    telegram_message_id=None,
    initial_sl=None, trail_activate=0.003, trail_lock=0.90, sl_pct=0.04,
    leverage=10, margin_usd=10.0, position_usd=100.0, max_hold_candles=72,
    **kwargs,
) -> str:
    """Append OPEN signal to daily CSV. Returns signal_id."""
    path = daily_csv_path()
    fields, rows = _read_csv(path)
    for h in CSV_HEADERS:
        if h not in fields:
            fields.append(h)
    sid = _next_signal_id()
    row = {h: "" for h in fields}
    row.update({
        "signal_id": sid,
        "symbol": symbol,
        "direction": direction,
        "scenario_id": scenario_id or "",
        "scenario_name": scenario_name or "",
        "entry_price": _fmt(entry_price),
        "stop_loss": _fmt(stop_loss),
        "take_profit": _fmt(take_profit),
        "issued_at_tehran": issued_at_tehran,
        "status": "OPEN",
        "outcome": "",
        "position_size_usd": _fmt(position_size_usd, 2),
        "signal_source": signal_source or "titan_signal",
        "telegram_message_id": str(telegram_message_id) if telegram_message_id is not None else "",
        "initial_sl": _fmt(initial_sl if initial_sl is not None else stop_loss),
        "trail_activate": _fmt(trail_activate, 6),
        "trail_lock": _fmt(trail_lock, 4),
        "sl_pct": _fmt(sl_pct, 4),
        "leverage": str(int(leverage or 10)),
        "margin_usd": _fmt(margin_usd, 2),
        "position_usd": _fmt(position_usd, 2),
        "max_hold_candles": str(int(max_hold_candles or 72)),
    })
    rows.append(row)
    _write_csv(path, fields, rows)
    logger.info("CSV signal saved id=%s %s %s", sid, symbol, direction)
    return sid


def update_signal_csv_row(
    symbol: str = "",
    direction: str = "",
    scenario_id: str = "",
    issued_at_tehran: str = "",
    signal_id: str = "",
    status: str = "CLOSED",
    outcome: str = "",
    hit_time_tehran: str = "",
    hit_price=None,
    broker_fee=None,
    final_pnl_usd=None,
    return_pct=None,
    telegram_message_id=None,
    **kwargs,
) -> bool:
    """Update a signal row by signal_id or (symbol, direction, scenario, issued_at)."""
    # search last N days
    dates = []
    if issued_at_tehran and len(issued_at_tehran) >= 10:
        dates.append(issued_at_tehran[:10])
    now = datetime.now(TEHRAN_TZ)
    for i in range(0, 8):
        d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        if d not in dates:
            dates.append(d)

    for date_str in dates:
        path = daily_csv_path(date_str)
        if not os.path.isfile(path):
            continue
        fields, rows = _read_csv(path)
        updated = False
        for row in rows:
            match = False
            if signal_id and row.get("signal_id") == signal_id:
                match = True
            elif (
                row.get("symbol") == symbol
                and row.get("direction") == direction
                and (row.get("scenario_id") or "") == (scenario_id or "")
                and (row.get("issued_at_tehran") or "") == (issued_at_tehran or "")
            ):
                match = True
            if not match:
                continue
            row["status"] = status or "CLOSED"
            if outcome is not None:
                row["outcome"] = outcome or ""
            if hit_time_tehran:
                row["hit_time_tehran"] = hit_time_tehran
            if hit_price is not None and hit_price != "":
                row["hit_price"] = _fmt(hit_price)
            if broker_fee is not None:
                row["broker_fee"] = _fmt(broker_fee, 6)
            if final_pnl_usd is not None:
                row["final_pnl_usd"] = _fmt(final_pnl_usd, 6)
            if return_pct is not None:
                row["return_pct"] = _fmt(return_pct, 6)
            if telegram_message_id is not None:
                row["telegram_message_id"] = str(telegram_message_id)
            updated = True
            break
        if updated:
            _write_csv(path, fields, rows)
            logger.info("CSV updated %s %s -> %s %s", symbol or signal_id, direction, status, outcome)
            return True
    return False


def _row_to_open(row: dict) -> OpenSignal:
    def fget(key, default=0.0):
        v = row.get(key, "")
        if v is None or v == "":
            return default
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def iget(key, default=0):
        try:
            return int(float(row.get(key) or default))
        except (TypeError, ValueError):
            return default

    mid = row.get("telegram_message_id") or ""
    try:
        mid_v = int(mid) if str(mid).strip() else None
    except ValueError:
        mid_v = None

    entry = fget("entry_price")
    sl = fget("stop_loss") or fget("initial_sl")
    return OpenSignal(
        id=row.get("signal_id") or f"{row.get('symbol')}-{row.get('issued_at_tehran')}",
        symbol=row.get("symbol") or "",
        direction=row.get("direction") or "",
        scenario_id=row.get("scenario_id") or "",
        scenario_name=row.get("scenario_name") or "",
        entry_price=entry,
        stop_loss=sl,
        initial_sl=fget("initial_sl") or sl,
        issued_at_tehran=row.get("issued_at_tehran") or "",
        telegram_message_id=mid_v,
        trail_activate=fget("trail_activate", 0.003) or 0.003,
        trail_lock=fget("trail_lock", 0.90) or 0.90,
        sl_pct=fget("sl_pct", 0.04) or 0.04,
        leverage=iget("leverage", 10) or 10,
        margin_usd=fget("margin_usd", 10) or 10,
        position_usd=fget("position_usd", 100) or 100,
        max_hold_candles=iget("max_hold_candles", 72) or 72,
        position_size_usd=fget("position_size_usd", 10) or 10,
        take_profit=fget("take_profit", 0),
    )


def get_open_signals(days: int = 7) -> List[OpenSignal]:
    """Load OPEN signals from recent daily CSV files."""
    opens: List[OpenSignal] = []
    now = datetime.now(TEHRAN_TZ)
    for i in range(0, max(1, days + 1)):
        d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        path = daily_csv_path(d)
        if not os.path.isfile(path):
            continue
        _, rows = _read_csv(path)
        for row in rows:
            if (row.get("status") or "").upper() == "OPEN":
                try:
                    opens.append(_row_to_open(row))
                except Exception as e:
                    logger.warning("skip bad row: %s", e)
    opens.sort(key=lambda s: s.issued_at_tehran)
    return opens


def has_open_signal(symbol, direction=None, scenario_id=None, within_seconds=None) -> bool:
    opens = get_open_signals(days=7)
    now = datetime.now(timezone.utc)
    for s in opens:
        if s.symbol != symbol:
            continue
        if direction and s.direction != direction:
            continue
        if scenario_id and s.scenario_id != scenario_id:
            continue
        if within_seconds:
            age = (now - s.issued_at).total_seconds()
            if age > within_seconds:
                continue
        return True
    return False


def save_signal(**kwargs) -> str:
    """Alias used by bot: persist new signal to CSV only."""
    issued = kwargs.get("issued_at_tehran") or tehran_time_str()
    return append_signal_row(
        symbol=kwargs["symbol"],
        direction=kwargs["direction"],
        entry_price=kwargs["entry_price"],
        stop_loss=kwargs.get("stop_loss") or kwargs.get("initial_sl") or 0,
        take_profit=kwargs.get("take_profit") or 0,
        issued_at_tehran=issued,
        signal_source=kwargs.get("signal_source") or "titan_signal",
        scenario_id=kwargs.get("scenario_id") or "",
        scenario_name=kwargs.get("scenario_name") or "",
        position_size_usd=kwargs.get("position_size_usd") or kwargs.get("margin_usd") or 10,
        telegram_message_id=kwargs.get("telegram_message_id"),
        initial_sl=kwargs.get("initial_sl"),
        trail_activate=kwargs.get("trail_activate", 0.003),
        trail_lock=kwargs.get("trail_lock", 0.90),
        sl_pct=kwargs.get("sl_pct", 0.04),
        leverage=kwargs.get("leverage", 10),
        margin_usd=kwargs.get("margin_usd", 10),
        position_usd=kwargs.get("position_usd", 100),
        max_hold_candles=kwargs.get("max_hold_candles", 72),
    )


def update_signal_exit(
    signal_id=None,
    exit_price=None,
    outcome="",
    broker_fee=0.0,
    final_pnl_usd=0.0,
    return_pct=0.0,
    margin_roi_pct=None,
    exit_time=None,
    status="CLOSED",
    symbol="",
    direction="",
    scenario_id="",
    issued_at_tehran="",
    **kwargs,
) -> bool:
    hit_tehran = tehran_time_str(exit_time) if exit_time else tehran_time_str()
    return update_signal_csv_row(
        signal_id=signal_id or "",
        symbol=symbol,
        direction=direction,
        scenario_id=scenario_id,
        issued_at_tehran=issued_at_tehran,
        status=status or "CLOSED",
        outcome=outcome or "",
        hit_time_tehran=hit_tehran,
        hit_price=exit_price,
        broker_fee=broker_fee,
        final_pnl_usd=final_pnl_usd,
        return_pct=return_pct,
    )
