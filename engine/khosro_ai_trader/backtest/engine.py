"""Backtest engine — validates the rule book on real Binance history.

Walks forward bar-by-bar over 1h klines, evaluating the SAME weighted vote
logic as the live RuleSignalEngine (imported, not copied), simulating the
same 1R/2R/3R partial ladder, fee model and conservative SL-first fills.
Derivatives/order-book context is not historical, so those votes are
neutral in backtests — a slightly harder test for the technical core.

Anti-overfit stance: parameters are FIXED (no grid-search curve fitting).
Metrics per pair: trades, win rate, profit factor, expectancy (R),
per-trade Sharpe, max drawdown (R), avg duration. Results land in
data/backtest/ and feed the dashboard.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import Config, load_config
from ..indicators import atr as atr_fn
from ..indicators import ema, klines_to_ohlcv, last, macd, rsi
from ..logger import get_logger, setup_logging
from ..sources import MarketDataHub

log = get_logger("backtest")


class Backtester:
    """Event-driven, single-position-per-pair backtest of the rule book."""

    def __init__(self, cfg: Config, hub: MarketDataHub | None = None) -> None:
        self.cfg = cfg
        self.bt = cfg.backtest
        self.engine = RuleSignalEngineProxy(cfg, hub)

    def run_pair(self, pair: str, days: int | None = None) -> dict[str, Any]:
        days = days or self.bt.days
        hub = self.engine.hub
        log.info("backtesting %s — fetching %dd of %s klines…", pair, days, self.bt.interval)
        rows = hub.fetch_klines_range(pair, self.bt.interval, days)
        if len(rows) < self.bt.warmup_bars + 10:
            log.warning("%s: only %d bars — skipped", pair, len(rows))
            return {"pair": pair, "error": f"not enough bars ({len(rows)})"}
        o = klines_to_ohlcv(rows)
        highs, lows, closes, vols = o["high"], o["low"], o["close"], o["volume"]
        n = len(closes)

        trades: list[dict[str, Any]] = []
        pos: dict[str, Any] | None = None

        for i in range(self.bt.warmup_bars, n):
            candle = {
                "high": highs[i], "low": lows[i], "close": closes[i],
                "close_time": o["close_time"][i],
            }

            # ---- manage the open position first (fills start next bar) ----
            if pos is not None:
                if self._advance(pos, candle, trades, i):
                    pos = None  # position fully closed this bar

            # ---- look for a new entry ----
            if pos is None:
                sig = self._evaluate_at(closes, highs, lows, vols, i)
                if sig:
                    pos = {
                        "direction": sig["direction"],
                        "entry_i": i,
                        "entry": closes[i],
                        "stop": sig["stop"],
                        "tps": sig["tps"],
                        "r": sig["r"],
                        "remaining_pct": 100.0,
                        "tp_hit": [False, False, False],
                        "realized_r": 0.0,
                        "entry_time": o["close_time"][i - 1],
                        "last_reasons": sig["reasons"],
                    }
        # close any leftover position at the final close
        if pos is not None and pos["remaining_pct"] > 0:
            self._close(pos, closes[-1], "eod", trades, n - 1)

        return self._report(pair, trades, days)

    # ------------------------------------------------------------------
    def _evaluate_at(self, closes, highs, lows, vols, i: int) -> dict | None:
        window = closes[: i + 1]
        price = closes[i]
        e20 = last(ema(window, 20)[-1])
        e50 = last(ema(window, 50)[-1])
        e200 = last(ema(window, 200)[-1])
        r = last(rsi(window)[-1])
        _, _, hist = macd(window)
        h1, h2 = last(hist[-1]), last(hist[-2])
        a = last(atr_fn(highs[: i + 1], lows[: i + 1], window)[-1])
        atr_pct = a / price * 100 if price else 0.0
        sg = self.cfg.signals
        if atr_pct < sg.min_atr_pct or atr_pct > sg.max_atr_pct:
            return None
        vol_surge = (
            (sum(vols[i - 2 : i + 1]) / 3) / (sum(vols[i - 47 : i + 1]) / 48)
            if i >= 47 and sum(vols[i - 47 : i + 1]) > 0 else 0.0
        )
        mom12 = (price - closes[i - 13]) / closes[i - 13] * 100 if i > 13 else 0.0

        long_score, _ = self.engine.votes(
            "long", e20, e50, e200, price, r, h1, h2, mom12, vol_surge, None, None, None
        )
        short_score, _ = self.engine.votes(
            "short", e20, e50, e200, price, r, h1, h2, mom12, vol_surge, None, None, None
        )
        gap = abs(long_score - short_score)
        direction = "long" if long_score >= short_score else "short"
        score = max(long_score, short_score)
        if score < self.cfg.signals.min_confidence or gap < self.cfg.signals.min_score_gap:
            return None
        if self.cfg.signals.trend_gate and not RuleSignalEngineProxy.structure_allows(direction, e20, e50, e200):
            return None

        sign = 1 if direction == "long" else -1
        r_dist = a * self.cfg.signals.atr_sl_multiplier
        return {
            "direction": direction,
            "stop": price - sign * r_dist,
            "tps": [price + sign * r_dist * m for m in (1.0, 2.0, 3.0)],
            "r": r_dist,
            "reasons": [],
        }

    # ------------------------------------------------------------------
    def _advance(self, pos: dict, c: dict, trades: list, i: int) -> bool:
        """Process one bar against the open position; returns True when closed."""
        long_ = pos["direction"] == "long"
        hit_stop = c["low"] <= pos["stop"] if long_ else c["high"] >= pos["stop"]
        if hit_stop:  # conservative: stop fills before targets
            self._close(pos, pos["stop"], "stop", trades, i)
            return True
        for idx, tp in enumerate(pos["tps"]):
            if pos["tp_hit"][idx]:
                continue
            touched = c["high"] >= tp if long_ else c["low"] <= tp
            if touched:
                self._tp(pos, idx, tp)
        if pos["remaining_pct"] <= 0:  # ladder complete (1R/2R/3R all hit)
            self._close(pos, c["close"], "tp3", trades, i)
            return True
        if i - pos["entry_i"] >= self.bt.max_age_bars:
            self._close(pos, c["close"], "timeout", trades, i)
            return True
        return False

    def _tp(self, pos: dict, idx: int, tp: float) -> None:
        w = self.cfg.risk.tp_weights[idx]
        closed_frac = w  # tp_weights are fractions of the ORIGINAL position
        pos["realized_r"] += (idx + 1 - self.cfg.risk.fee_r_per_round_trip) * closed_frac
        pos["tp_hit"][idx] = True
        pos["remaining_pct"] = round(max(0.0, pos["remaining_pct"] - w * 100), 2)

    def _close(self, pos: dict, price: float, reason: str, trades: list, i: int) -> None:
        frac = pos["remaining_pct"] / 100.0
        r_mult = self._r_mult(pos, price)
        pos["realized_r"] += (r_mult - self.cfg.risk.fee_r_per_round_trip) * frac
        pos["remaining_pct"] = 0.0
        entry_i = pos["entry_i"]
        trades.append({
            "direction": pos["direction"],
            "entry": round(pos["entry"], 8),
            "exit": round(price, 8),
            "stop": round(pos["stop"], 8),
            "r_multiple": round(pos["realized_r"], 3),
            "bars_held": i - entry_i,
            "exit_reason": reason,
        })

    def _r_mult(self, pos: dict, price: float) -> float:
        r = pos["r"] or 0.0
        if r <= 0:
            return 0.0
        return (price - pos["entry"]) / r if pos["direction"] == "long" \
            else (pos["entry"] - price) / r

    # ------------------------------------------------------------------
    def _report(self, pair: str, trades: list[dict], days: int) -> dict[str, Any]:
        rs = [t["r_multiple"] for t in trades]
        wins = [r for r in rs if r > 0]
        losses = [r for r in rs if r <= 0]
        gross_win, gross_loss = sum(wins), abs(sum(losses))
        mean_r = sum(rs) / len(rs) if rs else 0.0
        std_r = (
            math.sqrt(sum((r - mean_r) ** 2 for r in rs) / len(rs)) if len(rs) > 1 else 0.0
        )
        # equity curve in R + max drawdown
        cum, peak, mdd = 0.0, 0.0, 0.0
        for r in rs:
            cum += r
            peak = max(peak, cum)
            mdd = max(mdd, peak - cum)
        durations = [t["bars_held"] for t in trades]
        report = {
            "pair": pair,
            "days": days,
            "interval": self.bt.interval,
            "trades": len(trades),
            "win_rate": round(len(wins) / len(rs) * 100, 1) if rs else None,
            "total_r": round(sum(rs), 2),
            "expectancy_r": round(mean_r, 3) if rs else None,
            "sharpe_trade": round(mean_r / std_r, 3) if std_r > 0 else None,
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
            "max_drawdown_r": round(mdd, 2),
            "avg_bars_held": round(sum(durations) / len(durations), 1) if durations else None,
            "exit_breakdown": {
                "stop": sum(1 for t in trades if t["exit_reason"] == "stop"),
                "tp3": sum(1 for t in trades if t["exit_reason"] == "tp3"),
                "timeout": sum(1 for t in trades if t["exit_reason"] == "timeout"),
                "eod": sum(1 for t in trades if t["exit_reason"] == "eod"),
            },
            "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "trade_log": trades[-40:],   # keep the file lean; last 40 trades
        }
        verdict = (
            "PASS" if (report["expectancy_r"] or 0) > 0.05 and (report["profit_factor"] or 0) >= 1.2
            else "REVIEW"
        )
        report["verdict"] = verdict
        log.info(
            "%s: %d trades | win %.1f%% | expectancy %+.3fR | PF %s | DD %.1fR → %s",
            pair, report["trades"], report["win_rate"] or 0,
            report["expectancy_r"] or 0, report["profit_factor"],
            report["max_drawdown_r"], verdict,
        )
        return report


class RuleSignalEngineProxy:
    """Thin adapter exposing the live engine's vote bank for backtests."""

    def __init__(self, cfg: Config, hub: MarketDataHub | None) -> None:
        from ..signals.engine import RuleSignalEngine  # local import: no cycle

        self._engine = RuleSignalEngine(cfg, hub)
        self.hub = self._engine.hub

    def votes(self, *args):  # noqa: ANN002 — mirrors RuleSignalEngine._votes
        return self._engine._votes(*args)  # noqa: SLF001 — intentional reuse

    @staticmethod
    def structure_allows(direction: str, e20: float, e50: float, e200: float) -> bool:
        if direction == "long":
            return e20 > e50 > e200
        return e20 < e50 < e200


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def save_reports(reports: list[dict], root: Path) -> Path:
    out_dir = (root / "data" / "backtest").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    for rep in reports:
        if "error" in rep:
            continue
        (out_dir / f"{rep['pair']}.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pairs": [
            {k: rep[k] for k in (
                "pair", "trades", "win_rate", "total_r", "expectancy_r",
                "profit_factor", "sharpe_trade", "max_drawdown_r", "verdict",
            ) if k in rep}
            for rep in reports
        ],
    }
    path = out_dir / "summary.json"
    path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def _print_summary(reports: list[dict]) -> None:
    print("\n=== 📈 Backtest Summary (rule book v1, fixed parameters) ===")
    print(f"{'PAIR':<14}{'TRADES':>7}{'WIN%':>7}{'EXP R':>8}{'PF':>7}"
          f"{'SHARPE':>8}{'DD R':>7}  VERDICT")
    print("-" * 78)
    for r in reports:
        if "error" in r:
            print(f"{r['pair']:<14}  skipped: {r['error']}")
            continue
        print(
            f"{r['pair']:<14}{r['trades']:>7}{r['win_rate'] or 0:>7.1f}"
            f"{r['expectancy_r'] or 0:>+8.3f}{r['profit_factor'] or 0:>7.2f}"
            f"{r['sharpe_trade'] or 0:>8.2f}{r['max_drawdown_r']:>7.1f}  {r['verdict']}"
        )
    print("-" * 78 + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="khosro-backtest")
    parser.add_argument("--pairs", default=None, help="comma list, e.g. BTCUSDT,SOLUSDT")
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--config", default=None)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    setup_logging(args.log_level)
    cfg = load_config(args.config)
    pairs = (
        [p.strip().upper() for p in args.pairs.split(",")]
        if args.pairs else cfg.backtest.default_pairs
    )

    bt = Backtester(cfg)
    reports = []
    for pair in pairs:
        try:
            reports.append(bt.run_pair(pair, args.days))
        except Exception as exc:  # noqa: BLE001 — one pair must not kill the batch
            log.exception("backtest failed for %s", pair)
            reports.append({"pair": pair, "error": str(exc)[:200]})

    _print_summary(reports)
    path = save_reports(reports, cfg.project_root)
    print(f"reports written to {path}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
