"""Paper trading journal — the performance feedback loop.

Every approved signal becomes a paper trade; every hourly run marks open
trades to market with real 1h candles (SL/TP partials, conservative
SL-first fills, timeout exits) and compounds the paper equity. The journal
is a small JSON file committed back to the repo, so state survives between
GitHub Actions runs.

Accounting model (R-multiples):
  • 1R = the $ risked at stop (equity × risk%)
  • TP ladder 1R/2R/3R closes 40% / 40% / 20% of the position
  • fees+slippage charged per partial close (fee_r × weight)
  • equity += deltaR × risk_usd on every close

This is what makes the project honest: strategies must prove themselves
here (and in the backtester) before any real money is considered.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..config import Config
from ..logger import get_logger
from ..signals.base import Signal

log = get_logger("paper.journal")

SCHEMA_VERSION = 2


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PaperJournal:
    """Persistent paper-trading state (open trades, equity, stats)."""

    def __init__(self, cfg: Config, root: Path) -> None:
        self.cfg = cfg
        self.rk = cfg.risk
        self.path = (root / cfg.paper.journal_path).resolve()
        self.data: dict[str, Any] = self._load()

    # ------------------------------------------------------------------
    # persistence
    # ------------------------------------------------------------------
    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("version") == SCHEMA_VERSION:
                    log.info(
                        "journal loaded: %d open / %d closed trades, equity %.2f$",
                        self._count_open(data), data.get("closed_total", 0),
                        data.get("equity", 0),
                    )
                    return data
                log.warning("journal schema mismatch — starting a fresh journal")
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("journal unreadable (%s) — starting fresh", exc)
        return {
            "version": SCHEMA_VERSION,
            "equity": self.rk.equity_usd,
            "start_equity": self.rk.equity_usd,
            "risk_per_trade_pct": self.rk.risk_per_trade_pct,
            "tp_weights": self.rk.tp_weights,
            "fee_r": self.rk.fee_r_per_round_trip,
            "created_at": _utc_now().isoformat(timespec="seconds"),
            "trades": [],
            "closed_total": 0,
            "last_report_date": "",
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        log.info("journal saved: %s", self.path)

    # ------------------------------------------------------------------
    # queries used by the risk engine
    # ------------------------------------------------------------------
    def open_trades(self) -> list[dict[str, Any]]:
        return [t for t in self.data["trades"] if t["status"] == "open"]

    def recent_closed(self, hours: int) -> list[dict[str, Any]]:
        cutoff = _utc_now() - timedelta(hours=hours)
        out = []
        for t in self.data["trades"]:
            if t["status"] != "closed" or not t.get("closed_at_utc"):
                continue
            try:
                closed = datetime.fromisoformat(t["closed_at_utc"])
            except ValueError:
                continue
            if closed >= cutoff:
                out.append(t)
        return out

    def realized_r_today(self) -> float:
        today = _utc_now().strftime("%Y-%m-%d")
        return self.realized_r_on(today)

    def realized_r_on(self, day: str) -> float:
        total = 0.0
        for t in self.data["trades"]:
            if t["status"] == "closed" and (t.get("closed_at_utc") or "").startswith(day):
                total += t.get("realized_r", 0.0)
        return round(total, 3)

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def open_from_signals(self, signals: list[Signal]) -> list[dict[str, Any]]:
        """Create paper trades from approved signals (skips duplicates)."""
        opened = []
        existing = {t["signal_id"] for t in self.data["trades"]}
        for sig in signals:
            sig.signal_id = sig.signal_id or self._make_id(sig)
            if sig.signal_id in existing:
                continue
            trade = {
                "signal_id": sig.signal_id,
                "symbol": sig.symbol,
                "pair": sig.pair,
                "direction": sig.direction,
                "opened_at_utc": _utc_now().isoformat(timespec="seconds"),
                "entry": sig.entry,
                "stop": sig.stop_loss,
                "take_profits": sig.take_profits,
                "r_per_unit": abs((sig.entry or 0) - (sig.stop_loss or 0)),
                "risk_usd": sig.position_size_usd,
                "notional_usd": sig.notional_usd,
                "size_units": (
                    (sig.position_size_usd or 0)
                    / abs((sig.entry or 1) - (sig.stop_loss or 1))
                ),
                "confidence": sig.confidence,
                "timeframe": sig.timeframe,
                "reasons": sig.reasons[:4],
                "remaining_pct": 100.0,
                "tp_hit": [False, False, False],
                "realized_r": 0.0,
                "status": "open",
                "closed_at_utc": None,
                "close_reason": "",
                "last_price": sig.entry,
                "unrealized_r": 0.0,
            }
            self.data["trades"].append(trade)
            opened.append(trade)
            log.info(
                "PAPER OPEN %s %s @ %.6g (stop %.6g, tps %s, risk %.2f$)",
                sig.direction.upper(), sig.symbol, sig.entry or 0,
                sig.stop_loss or 0,
                [round(tp, 6) for tp in sig.take_profits], sig.position_size_usd,
            )
        if opened:
            self.save()
        return opened

    def mark_to_market(self, candles_by_pair: dict[str, list[dict]]) -> int:
        """Advance open trades with fresh candles; returns # of closes.

        `candles_by_pair[pair]` = [{"open_time", "close_time", "open", "high",
        "low", "close"}] ascending, covering the period since each trade's
        open. Conservative assumption: if a candle touches both SL and TP,
        the stop fills first.
        """
        closes = 0
        now_iso = _utc_now().isoformat(timespec="seconds")
        max_age_s = self.cfg.signals.max_age_hours * 3600
        for trade in self.open_trades():
            pair = trade.get("pair") or ""
            candles = candles_by_pair.get(pair) or []
            opened_at = datetime.fromisoformat(trade["opened_at_utc"])
            for c in candles:
                close_time = datetime.fromtimestamp(c["close_time"] / 1000, tz=timezone.utc)
                if close_time <= opened_at:
                    continue
                hit_stop, hit_tps = self._candle_touches(trade, c)
                if hit_stop:  # conservative: stop before targets
                    self._close_partial(trade, "stop", exit_price=trade["stop"], now_iso=now_iso)
                    closes += 1
                    break
                for idx, tp in enumerate(trade["take_profits"]):
                    if trade["tp_hit"][idx] or not hit_tps[idx]:
                        continue
                    self._tp_partial(trade, idx, tp, now_iso)
                if trade["status"] == "closed":   # fully exited via the TP ladder
                    closes += 1
                    break
                timeout = (_utc_now() - opened_at).total_seconds() > max_age_s
                if timeout and trade["status"] == "open":
                    self._close_partial(trade, "timeout", exit_price=c["close"], now_iso=now_iso)
                    closes += 1
                    break
            if trade["status"] == "open":
                trade["unrealized_r"] = round(self._unrealized_r(trade), 3)
        if closes:
            log.info("%d paper trade(s) closed this run", closes)
            self.save()
        return closes

    # ------------------------------------------------------------------
    # stats for dashboard / daily report
    # ------------------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        closed = [t for t in self.data["trades"] if t["status"] == "closed"]
        open_n = len(self.open_trades())
        rs = [t["realized_r"] for t in closed]
        wins = [r for r in rs if r > 0]
        losses = [r for r in rs if r <= 0]
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        equity = self.data["equity"]
        start = self.data["start_equity"] or 1.0
        unrealized = sum(t.get("unrealized_r", 0.0) for t in self.open_trades())
        curve = self.data.get("equity_curve", [])
        return {
            "equity": round(equity, 2),
            "return_pct": round((equity - start) / start * 100, 2),
            "total_r": round(sum(rs), 2),
            "unrealized_r": round(unrealized, 2),
            "closed_trades": len(closed),
            "open_trades": open_n,
            "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
            "avg_r": round(sum(rs) / len(rs), 3) if rs else None,
            "best_r": round(max(rs), 2) if rs else None,
            "worst_r": round(min(rs), 2) if rs else None,
            "today_r": self.realized_r_today(),
            "max_drawdown_pct": self._max_drawdown_pct(curve, start),
        }

    def note_equity_point(self) -> None:
        """Append/refresh today's equity-curve point."""
        today = _utc_now().strftime("%Y-%m-%d")
        curve = self.data.setdefault("equity_curve", [])
        if curve and curve[-1].get("t") == today:
            curve[-1]["equity"] = round(self.data["equity"], 2)
        else:
            curve.append({"t": today, "equity": round(self.data["equity"], 2)})

    @property
    def last_report_date(self) -> str:
        return self.data.get("last_report_date", "")

    @last_report_date.setter
    def last_report_date(self, value: str) -> None:
        self.data["last_report_date"] = value
        self.save()

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _candle_touches(self, trade: dict, c: dict) -> tuple[bool, list[bool]]:
        if trade["direction"] == "long":
            hit_stop = c["low"] <= trade["stop"]
            hit_tps = [c["high"] >= tp for tp in trade["take_profits"]]
        else:
            hit_stop = c["high"] >= trade["stop"]
            hit_tps = [c["low"] <= tp for tp in trade["take_profits"]]
        return hit_stop, hit_tps

    def _tp_partial(self, trade: dict, idx: int, tp: float, now_iso: str) -> None:
        w = self.rk.tp_weights[idx] if idx < len(self.rk.tp_weights) else 0.0
        closed_frac = w  # tp_weights are fractions of the ORIGINAL position
        level_r = idx + 1  # tp ladder = 1R/2R/3R by construction
        delta_r = (level_r - self.rk.fee_r_per_round_trip) * closed_frac
        trade["tp_hit"][idx] = True
        trade["remaining_pct"] = round(max(0.0, trade["remaining_pct"] - w * 100), 2)
        self._apply(trade, delta_r, exit_price=tp, now_iso=now_iso,
                    reason=f"tp{idx + 1}")
        if trade["remaining_pct"] <= 0.01:
            trade["status"] = "closed"
            trade["closed_at_utc"] = now_iso
            trade["close_reason"] = trade["close_reason"] or f"tp{idx + 1}"
            self.data["closed_total"] = self.data.get("closed_total", 0) + 1

    def _close_partial(self, trade: dict, reason: str, exit_price: float, now_iso: str) -> None:
        frac = trade["remaining_pct"] / 100.0
        r_mult = self._r_multiple(trade, exit_price)
        delta_r = (r_mult - self.rk.fee_r_per_round_trip) * frac
        trade["remaining_pct"] = 0.0
        trade["status"] = "closed"
        trade["closed_at_utc"] = now_iso
        trade["close_reason"] = reason
        self.data["closed_total"] = self.data.get("closed_total", 0) + 1
        self._apply(trade, delta_r, exit_price=exit_price, now_iso=now_iso, reason=reason)

    def _apply(self, trade: dict, delta_r: float, exit_price: float,
               now_iso: str, reason: str) -> None:
        risk_usd = trade.get("risk_usd") or 0.0
        trade["realized_r"] = round(trade.get("realized_r", 0.0) + delta_r, 3)
        trade["last_price"] = exit_price
        self.data["equity"] = round(self.data["equity"] + delta_r * risk_usd, 4)
        log.info(
            "PAPER CLOSE %s %s (%s) deltaR=%+.2f totalR=%+.2f equity=%.2f$",
            trade["direction"], trade["symbol"], reason, delta_r,
            trade["realized_r"], self.data["equity"],
        )

    def _unrealized_r(self, trade: dict) -> float:
        price = trade.get("last_price") or trade.get("entry")
        if not price or not trade.get("r_per_unit"):
            return 0.0
        return self._r_multiple(trade, price) * (trade["remaining_pct"] / 100.0)

    @staticmethod
    def _r_multiple(trade: dict, exit_price: float) -> float:
        r = trade.get("r_per_unit") or 0.0
        if r <= 0:
            return 0.0
        if trade["direction"] == "long":
            return (exit_price - trade["entry"]) / r
        return (trade["entry"] - exit_price) / r

    @staticmethod
    def _make_id(sig: Signal) -> str:
        stamp = _utc_now().strftime("%Y%m%d-%H%M")
        return f"{stamp}-{sig.symbol}-{sig.direction}"

    @staticmethod
    def _count_open(data: dict) -> int:
        return sum(1 for t in data.get("trades", []) if t.get("status") == "open")

    @staticmethod
    def _max_drawdown_pct(curve: list[dict], start: float) -> float | None:
        peak, mdd = start, 0.0
        for point in curve:
            eq = point.get("equity", 0)
            peak = max(peak, eq)
            if peak > 0:
                mdd = max(mdd, (peak - eq) / peak * 100)
        return round(mdd, 2) if curve else None
