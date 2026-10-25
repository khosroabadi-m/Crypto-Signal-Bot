"""Trailing-stop engine — shared by bot (live) and monitor (night)."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import requests

from .config import (
    LEVERAGE, MARGIN_USD, POSITION_USD, FEE_PER_TRADE, SCENARIOS,
)
from .signal_store import get_open_signals, update_signal_exit


logger = logging.getLogger(__name__)
TEHRAN_TZ = ZoneInfo("Asia/Tehran")
KUCOIN_URL = "https://api.kucoin.com/api/v1/market/candles"
MAX_OPEN_DAYS = 6


def compute_pnl(direction: str, entry: float, exit_price: float,
                position_usd: float = None, fee: float = None,
                margin_usd: float = None):
    position_usd = position_usd if position_usd is not None else POSITION_USD
    fee = fee if fee is not None else FEE_PER_TRADE
    margin_usd = margin_usd if margin_usd is not None else MARGIN_USD
    if not entry:
        return 0.0, 0.0, fee, 0.0
    if direction == "LONG":
        ret_pct = (exit_price - entry) / entry
    else:
        ret_pct = (entry - exit_price) / entry
    gross = position_usd * ret_pct
    net = gross - fee
    roi = (net / margin_usd) * 100 if margin_usd else 0.0
    return net, ret_pct * 100.0, fee, roi


def fetch_kucoin_30m(symbol: str, start_unix: int, end_unix: int) -> List[dict]:
    params = {
        "symbol": symbol, "type": "30min",
        "startAt": int(start_unix), "endAt": int(end_unix),
    }
    for attempt in range(3):
        try:
            r = requests.get(KUCOIN_URL, params=params, timeout=20)
            if r.status_code == 200:
                data = r.json().get("data") or []
                candles = [
                    {"t": int(c[0]), "o": float(c[1]), "c": float(c[2]),
                     "h": float(c[3]), "l": float(c[4]), "v": float(c[5])}
                    for c in data
                ]
                return list(reversed(candles))
            if r.status_code == 429:
                time.sleep(8 * (attempt + 1))
            else:
                logger.warning("KuCoin %s status %s", symbol, r.status_code)
                return []
        except Exception as e:
            logger.warning("KuCoin fetch %s: %s", symbol, e)
            time.sleep(3)
    return []


def walk_trailing(
    candles: List[dict],
    direction: str,
    entry: float,
    initial_sl: float,
    trail_activate: float,
    trail_lock: float,
    max_hold_candles: int,
) -> Optional[Dict[str, Any]]:
    """Walk candles; return exit dict or None if still open."""
    if not candles or not entry:
        return None
    trail_active = False
    max_fav = entry
    hold = 0
    outcome = None
    exit_price = None

    for c in candles:
        hold += 1
        hi, lo, cl = c["h"], c["l"], c["c"]

        if direction == "LONG":
            if lo <= initial_sl:
                outcome, exit_price = "STOP_HIT", initial_sl
                break
            if hi > max_fav:
                max_fav = hi
            if not trail_active:
                if max_fav >= entry * (1 + trail_activate):
                    trail_active = True
            if trail_active:
                trail_stop = entry + (max_fav - entry) * trail_lock
                if lo <= trail_stop:
                    outcome, exit_price = "TRAIL_STOP", trail_stop
                    break
        else:
            if hi >= initial_sl:
                outcome, exit_price = "STOP_HIT", initial_sl
                break
            if lo < max_fav:
                max_fav = lo
            if not trail_active:
                if max_fav <= entry * (1 - trail_activate):
                    trail_active = True
            if trail_active:
                trail_stop = entry - (entry - max_fav) * trail_lock
                if hi >= trail_stop:
                    outcome, exit_price = "TRAIL_STOP", trail_stop
                    break

        if hold >= max_hold_candles:
            outcome, exit_price = "MAX_HOLD", cl
            break

    if not outcome:
        return None

    return {
        "outcome": outcome,
        "exit_price": exit_price,
        "hold": hold,
        "trail_activated": trail_active,
        "max_favorable": max_fav,
    }


def _sig_params(sig):
    sc = SCENARIOS.get(sig.scenario_id or "", {})
    entry = float(sig.entry_price or 0)
    direction = sig.direction
    sl = sig.initial_sl or sig.stop_loss
    if not sl or sl <= 0:
        pct = sc.get("sl_pct", 0.04)
        sl = entry * (1 - pct) if direction == "LONG" else entry * (1 + pct)
    activate = float(sig.trail_activate if sig.trail_activate is not None else sc.get("trail_activate", 0.003))
    lock = float(sig.trail_lock if sig.trail_lock is not None else sc.get("trail_lock", 0.90))
    max_hold = int(sig.max_hold_candles or sc.get("max_hold_candles", 72))
    return entry, float(sl), activate, lock, max_hold


def evaluate_signal(sig, force_close: bool = False) -> Optional[Dict[str, Any]]:
    """Evaluate one open signal against live/historical 30m data."""
    entry, sl, activate, lock, max_hold = _sig_params(sig)
    if not entry:
        return None

    # entry time unix (OpenSignal provides issued_at_unix / issued_at)
    start_unix = getattr(sig, "issued_at_unix", None) or None
    try:
        start_unix = int(start_unix) if start_unix else None
    except (TypeError, ValueError):
        start_unix = None
    if not start_unix:
        issued_at = getattr(sig, "issued_at", None)
        if issued_at is not None:
            dt = issued_at
            if getattr(dt, "tzinfo", None) is None:
                dt = dt.replace(tzinfo=timezone.utc)
            start_unix = int(dt.timestamp())
    if not start_unix:
        start_unix = int(time.time()) - 3 * 86400
    end_unix = int(time.time())

    candles = fetch_kucoin_30m(sig.symbol, start_unix - 1800, end_unix)
    # only candles at/after entry
    candles = [c for c in candles if c["t"] >= start_unix - 60]
    result = walk_trailing(candles, sig.direction, entry, sl, activate, lock, max_hold)

    pos = float(sig.position_usd or 0) or None
    margin = float(sig.margin_usd or 0) or None
    if pos is None and sig.leverage and sig.margin_usd:
        pos = float(sig.margin_usd) * int(sig.leverage)
    if result is None and force_close and candles:
        exit_price = candles[-1]["c"]
        net, ret_pct, fee, roi = compute_pnl(sig.direction, entry, exit_price, pos, None, margin)
        result = {
            "outcome": "EOD_FORCE_CLOSE",
            "exit_price": exit_price,
            "net_pnl": net,
            "ret_pct": ret_pct,
            "fee": fee,
            "margin_roi": roi,
            "hold": len(candles),
            "trail_activated": False,
            "max_favorable": entry,
        }
    elif result is not None:
        net, ret_pct, fee, roi = compute_pnl(
            sig.direction, entry, result["exit_price"], pos, None, margin
        )
        result["net_pnl"] = net
        result["ret_pct"] = ret_pct
        result["fee"] = fee
        result["margin_roi"] = roi
    return result


def process_open_signals(
    symbol: Optional[str] = None,
    force_close: bool = False,
    days: int = MAX_OPEN_DAYS,
) -> List[Dict[str, Any]]:
    """Process open signals; return list of closed results with signal meta."""
    opens = get_open_signals(days=days)
    if symbol:
        opens = [s for s in opens if s.symbol == symbol]

    closed = []
    for sig in opens:
        try:
            result = evaluate_signal(sig, force_close=force_close)
        except Exception as e:
            logger.error("Trail eval error id=%s %s: %s", sig.id, sig.symbol, e)
            continue
        if not result:
            continue
        update_signal_exit(
            signal_id=str(sig.id),
            exit_price=result["exit_price"],
            outcome=result["outcome"],
            broker_fee=result["fee"],
            final_pnl_usd=result["net_pnl"],
            return_pct=result["ret_pct"],
            margin_roi_pct=result.get("margin_roi"),
            status="CLOSED",
            symbol=sig.symbol,
            direction=sig.direction,
            scenario_id=sig.scenario_id,
            issued_at_tehran=sig.issued_at_tehran,
        )
        closed.append({
            "id": sig.id,
            "symbol": sig.symbol,
            "direction": sig.direction,
            "scenario_id": sig.scenario_id,
            "scenario_name": sig.scenario_name or "",
            "entry_price": sig.entry_price,
            "issued_at_tehran": sig.issued_at_tehran,
            "telegram_message_id": getattr(sig, "telegram_message_id", None),
            **result,
        })
        logger.info(
            "CLOSED %s %s [%s] %s pnl=%+.4f",
            sig.symbol, sig.direction, sig.scenario_id, result["outcome"], result["net_pnl"],
        )
        time.sleep(0.25)  # mild rate limit between KuCoin calls
    return closed
