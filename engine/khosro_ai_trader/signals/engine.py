"""Rule-based signal engine (rule book v1) — the default trading brain.

The engine turns each top-ranked trending coin into a scored directional
signal using weighted technical votes:

  Long/Short symmetric vote bank (per coin, 1h klines):
    • trend structure        EMA20 > EMA50 > EMA200 (or mirror)   ±20
    • price vs EMA20                                              ±10
    • RSI14 healthy zone     50..72 long / 28..50 short           ±15
    • MACD histogram sign+rising                                  ±15
    • 12-bar momentum                                             ±10
    • volume surge (3h vs 48h avg)                                ±10
    • order-book imbalance    > +5% / < -5%                       ±10
    • funding-rate crowd tax  (extreme positioning is contrarian)  ±10
    • long/short ratio extreme (contrarian)                       ±10
  AI fusion: a matching AI verdict adds up to +20 confidence; an "avoid"
  verdict vetoes the signal (configurable).

Emission rule: winner score >= min_confidence AND |long-short| >= min_score_gap.
Risk is attached downstream by the RiskEngine (ATR stop, 1R/2R/3R ladder).

The user's own trading-rule file can replace this engine later by
subclassing SignalEngine — the pipeline contract stays identical.
"""

from __future__ import annotations

from typing import Any

from ..config import Config
from ..indicators import atr, ema, klines_to_ohlcv, last, macd, rsi
from ..logger import get_logger
from ..models import TrendingSnapshot
from ..sources import MarketDataHub
from .base import Signal, SignalEngine

log = get_logger("signals.engine")


class RuleSignalEngine(SignalEngine):
    """Default quant rule book — deterministic, explainable, replaceable."""

    def __init__(self, cfg: Config, hub: MarketDataHub | None = None) -> None:
        self.cfg = cfg
        self.sg = cfg.signals
        self.hub = hub or MarketDataHub(cfg)
        self._klines_cache: dict[str, list[list]] = {}

    # ------------------------------------------------------------------
    def evaluate(
        self, snapshot: TrendingSnapshot, md_data: dict[str, dict] | None = None
    ) -> list[Signal]:
        """Score top candidates → candidate signals (unsized, unfiltered)."""
        md_data = md_data or {}
        candidates = [
            c for c in snapshot.coins[: self.sg.top_candidates] if c.binance_pair
        ]
        scored: list[Signal] = []
        for coin in candidates:
            try:
                sig = self._evaluate_coin(coin.symbol, coin.binance_pair, snapshot, md_data)
            except Exception as exc:  # noqa: BLE001 — one coin must not kill the run
                log.warning("signal evaluation failed for %s: %s", coin.symbol, exc)
                continue
            if sig:
                scored.append(sig)

        scored.sort(key=lambda s: (s.confidence or 0), reverse=True)
        return scored[: self.sg.max_signals_per_run]

    # ------------------------------------------------------------------
    def _evaluate_coin(
        self, symbol: str, pair: str, snapshot: TrendingSnapshot,
        md_data: dict[str, dict],
    ) -> Signal | None:
        rows = self._get_klines(pair)
        if not rows or len(rows) < 220:
            log.info("%s: not enough klines (%d) — skipped", symbol, len(rows))
            return None
        o = klines_to_ohlcv(rows)
        closes, highs, lows, vols = (
            o["close"], o["high"], o["low"], o["volume"]
        )
        price = closes[-1]

        e20 = last(ema(closes, 20)[-1])
        e50 = last(ema(closes, 50)[-1])
        e200 = last(ema(closes, 200)[-1])
        r = last(rsi(closes)[-1])
        _, _, hist = macd(closes)
        h1, h2 = last(hist[-1]), last(hist[-2])
        a = last(atr(highs, lows, closes)[-1])
        atr_pct = (a / price * 100) if price else 0.0

        if atr_pct < self.sg.min_atr_pct:
            log.info("%s: ATR %.2f%% too low — skipped", symbol, atr_pct)
            return None
        if atr_pct > self.sg.max_atr_pct:
            log.info("%s: ATR %.2f%% too high — skipped", symbol, atr_pct)
            return None

        vol_surge = (
            (sum(vols[-3:]) / 3) / (sum(vols[-48:]) / 48)
            if len(vols) >= 48 and sum(vols[-48:]) > 0 else 0.0
        )
        mom12 = ((price - closes[-13]) / closes[-13] * 100) if len(closes) > 13 else 0.0

        md = md_data.get(symbol, {})
        depth = (md.get("depth") or {}).get("imbalance")
        funding = (md.get("funding") or {}).get("funding_rate_8h_pct")
        lsr = (md.get("lsr") or {}).get("lsr")

        long_score, long_reasons = self._votes(
            side="long", e20=e20, e50=e50, e200=e200, price=price, rsi_v=r,
            hist=h1, hist_prev=h2, mom12=mom12, vol_surge=vol_surge,
            depth=depth, funding=funding, lsr=lsr,
        )
        short_score, short_reasons = self._votes(
            side="short", e20=e20, e50=e50, e200=e200, price=price, rsi_v=r,
            hist=h1, hist_prev=h2, mom12=mom12, vol_surge=vol_surge,
            depth=depth, funding=funding, lsr=lsr,
        )

        direction, score, reasons = (
            ("long", long_score, long_reasons)
            if long_score >= short_score else ("short", short_score, short_reasons)
        )
        gap = abs(long_score - short_score)
        if score < self.sg.min_confidence or gap < self.sg.min_score_gap:
            log.info(
                "%s: no signal (long=%.0f short=%.0f gap=%.0f)",
                symbol, long_score, short_score, gap,
            )
            return None
        if self.sg.trend_gate and not self._structure_allows(direction, e20, e50, e200):
            log.info("%s: trend gate blocks %s (structure mixed/opposed)", symbol, direction)
            return None

        # ---- AI fusion ----
        ai_note = ""
        verdict = snapshot.verdict_for(symbol)
        if verdict and self.sg.use_ai_fusion:
            if verdict.direction == "avoid" and self.sg.ai_veto:
                log.info("%s: AI veto (avoid) — signal dropped", symbol)
                return None
            if verdict.direction == direction:
                bonus = self.sg.ai_confidence_bonus * (verdict.confidence / 100.0)
                score = min(100.0, score + bonus)
                reasons.append(f"تأیید هوش مصنوعی ({verdict.confidence}%)")
                ai_note = f"AI {direction} {verdict.confidence}%"
            elif verdict.direction in ("long", "short"):
                score = max(0.0, score - self.sg.ai_confidence_bonus / 2)
                ai_note = f"AI مخالف: {verdict.direction}"

        # ---- ATR risk geometry: stop = ATR × mult, TP ladder = 1R/2R/3R ----
        r_dist = a * self.sg.atr_sl_multiplier
        sign = 1 if direction == "long" else -1
        entry = price
        stop = entry - sign * r_dist
        tps = [entry + sign * r_dist * mult for mult in (1.0, 2.0, 3.0)]

        meta: dict[str, Any] = {
            "ema20": round(e20, 6), "ema50": round(e50, 6), "ema200": round(e200, 6),
            "rsi14": round(r, 1), "macd_hist": round(h1, 6),
            "atr": a, "atr_pct": round(atr_pct, 2),
            "vol_surge": round(vol_surge, 2), "mom12_pct": round(mom12, 2),
            "depth_imbalance": depth, "funding_8h_pct": funding, "lsr": lsr,
            "long_score": round(long_score, 1), "short_score": round(short_score, 1),
            "score_gap": round(gap, 1), "ai": ai_note,
            "fear_greed": (md.get("fear_greed") or {}).get("value"),
        }
        return Signal(
            symbol=symbol, direction=direction, entry=entry, stop_loss=stop,
            take_profits=tps, timeframe=self.sg.kline_interval,
            confidence=round(score, 1), pair=pair, r_value=r_dist, rr=2.0,
            reasons=reasons, meta=meta,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _structure_allows(direction: str, e20: float, e50: float, e200: float) -> bool:
        """Trend gate: longs need EMA20>EMA50>EMA200, shorts the mirror."""
        if direction == "long":
            return e20 > e50 > e200
        return e20 < e50 < e200

    def _votes(  # noqa: PLR0913 — explicit vote inputs (positional-friendly for backtests)
        self, side: str, e20: float, e50: float, e200: float, price: float,
        rsi_v: float, hist: float, hist_prev: float, mom12: float,
        vol_surge: float, depth: float | None, funding: float | None,
        lsr: float | None,
    ) -> tuple[float, list[str]]:
        """Weighted vote bank for one side; returns (score, persian reasons)."""
        score = 0.0
        reasons: list[str] = []
        long_ = side == "long"

        if long_ == (e20 > e50 > e200):
            score += 20
            reasons.append("ساختار روند همسو (" + ("EMA20>50>200" if long_ else "EMA20<50<200") + ")")
        if long_ == (price > e20):
            score += 10
            reasons.append("قیمت " + ("بالای" if long_ else "زیر") + " EMA20")

        rsi_ok = (50 <= rsi_v <= 72) if long_ else (28 <= rsi_v <= 50)
        rsi_extreme = rsi_v > 75 if long_ else rsi_v < 25
        if rsi_ok:
            score += 15
            reasons.append(f"RSI={rsi_v:.0f} در ناحیه سالم")
        elif rsi_extreme:
            score -= 15
            reasons.append(f"هشدار اشباع (RSI={rsi_v:.0f})")

        hist_pos = hist > 0 if long_ else hist < 0
        hist_rising = (hist > hist_prev) if long_ else (hist < hist_prev)
        if hist_pos and hist_rising:
            score += 15
            reasons.append("مومنتوم MACD همسو و در حال تقویت")
        elif hist_pos:
            score += 7

        if long_ == (mom12 > 0):
            score += 10
            reasons.append(f"مومنتوم 12h: {mom12:+.1f}%")
        if vol_surge >= 1.15:
            score += 10
            reasons.append(f"جهش حجم معاملات (×{vol_surge:.2f})")

        if depth is not None:
            if long_ and depth > 0.05:
                score += 10
                reasons.append(f"فشار خرید در دفتر سفارش ({depth:+.0%})")
            elif (not long_) and depth < -0.05:
                score += 10
                reasons.append(f"فشار فروش در دفتر سفارش ({depth:+.0%})")
            elif long_ and depth < -0.10:
                score -= 10
            elif (not long_) and depth > 0.10:
                score -= 10

        if funding is not None:
            # crowded side pays the funding tax — contrarian drag
            if long_ and funding > 0.08:
                score -= 10
                reasons.append(f"لانگ‌ها شلوغ (فاندینگ {funding:+.3f}%)")
            elif (not long_) and funding < -0.08:
                score -= 10
                reasons.append(f"شورت‌ها شلوغ (فاندینگ {funding:+.3f}%)")
            elif long_ and funding < -0.05:
                score += 5
            elif (not long_) and funding > 0.05:
                score += 5

        if lsr is not None:
            if long_ and lsr > 3.0:
                score -= 10
                reasons.append(f"نسبت لانگ/شورت افراطی ({lsr:.1f})")
            elif (not long_) and lsr < 0.8:
                score -= 10
                reasons.append(f"نسبت لانگ/شورت افراطی ({lsr:.1f})")

        return max(0.0, score), reasons

    # ------------------------------------------------------------------
    def _get_klines(self, pair: str) -> list[list]:
        if pair not in self._klines_cache:
            self._klines_cache[pair] = self.hub.fetch_klines(
                pair, self.sg.kline_interval, self.sg.kline_limit
            )
        return self._klines_cache[pair]
