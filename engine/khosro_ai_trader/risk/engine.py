"""Risk engine — position sizing, validation filters and circuit breakers.

Everything here is portfolio-protection logic, independent of the rule book:

  • 1R sizing        risk_usd = equity × risk%  →  size = risk / |entry−stop|
  • notional cap     position value ≤ equity × max_notional%
  • RR floor         reward:risk at TP2 must clear min_rr
  • cooldown         no re-entry on same symbol+direction within N hours
  • max open trades  portfolio cap from the paper journal
  • daily loss halt  circuit breaker after −max_daily_loss_r in one day

Signals that fail validation are dropped with a logged reason — never
silently resized into something riskier than requested.
"""

from __future__ import annotations

from ..config import Config
from ..logger import get_logger
from ..signals.base import Signal

log = get_logger("risk.engine")


class RiskEngine:
    """Turns raw candidate signals into sized, portfolio-safe signals."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.rk = cfg.risk

    # ------------------------------------------------------------------
    def filter_and_size(self, signals: list[Signal], journal) -> list[Signal]:
        """Apply validation + sizing; returns the final tradable list."""
        breaker, breaker_reason = self._circuit_breaker(journal)
        if breaker:
            log.warning("CIRCUIT BREAKER ACTIVE (%s) — no new signals", breaker_reason)
            return []

        open_trades = journal.open_trades()
        recent = journal.recent_closed(hours=self.cfg.signals.cooldown_hours)
        approved: list[Signal] = []

        for sig in signals:
            reason = self._validate(sig)
            if reason:
                log.info("%s rejected: %s", sig.symbol, reason)
                continue
            if any(t["symbol"] == sig.symbol for t in open_trades):
                log.info("%s rejected: position already open", sig.symbol)
                continue
            if any(
                t["symbol"] == sig.symbol and t["direction"] == sig.direction
                for t in recent
            ):
                log.info(
                    "%s rejected: cooldown %dh after a %s trade",
                    sig.symbol, self.cfg.signals.cooldown_hours, sig.direction,
                )
                continue

            self._size(sig)
            approved.append(sig)
            if len(approved) + len(open_trades) >= self.rk.max_open_trades:
                log.info("max_open_trades reached — stopping new approvals")
                break
        return approved

    # ------------------------------------------------------------------
    def _validate(self, sig: Signal) -> str | None:
        """Return a rejection reason or None when the signal is acceptable."""
        if sig.entry is None or sig.stop_loss is None or not sig.take_profits:
            return "incomplete risk geometry"
        r_dist = abs(sig.entry - sig.stop_loss)
        if r_dist <= 0:
            return "zero risk distance"
        rr_tp2 = (
            abs(sig.take_profits[1] - sig.entry) / r_dist
            if len(sig.take_profits) > 1 else 0.0
        )
        if rr_tp2 < self.rk.min_rr:
            return f"RR at TP2 ({rr_tp2:.2f}) < min_rr ({self.rk.min_rr})"
        stop_pct = r_dist / sig.entry * 100
        if stop_pct > 6.0:
            return f"stop too wide ({stop_pct:.1f}%)"
        return None

    # ------------------------------------------------------------------
    def _size(self, sig: Signal) -> None:
        """Fill position-size fields on the signal (1R sizing + notional cap)."""
        equity = self.rk.equity_usd
        risk_usd = equity * self.risk_per_trade_pct() / 100.0
        r_dist = abs(sig.entry - sig.stop_loss)
        size_units = risk_usd / r_dist
        notional = size_units * sig.entry
        max_notional = equity * self.rk.max_notional_pct / 100.0
        if notional > max_notional:
            scale = max_notional / notional
            notional = max_notional
            size_units *= scale
            risk_usd = size_units * r_dist  # risk shrinks with the capped size
            log.info(
                "%s: notional capped to %.0f$ (risk %.2f$)", sig.symbol,
                notional, risk_usd,
            )
        sig.position_size_usd = round(risk_usd, 2)
        sig.notional_usd = round(notional, 2)
        sig.r_value = r_dist

    def risk_per_trade_pct(self) -> float:
        """Per-trade risk with a hard sanity ceiling (never above 3%)."""
        return min(self.rk.risk_per_trade_pct, 3.0)

    # ------------------------------------------------------------------
    def _circuit_breaker(self, journal) -> tuple[bool, str]:
        open_trades = journal.open_trades()
        if len(open_trades) >= self.rk.max_open_trades:
            return True, f"{len(open_trades)} open trades (max {self.rk.max_open_trades})"
        daily_r = journal.realized_r_today()
        if daily_r <= -abs(self.rk.max_daily_loss_r):
            return True, f"daily loss {daily_r:+.2f}R ≤ -{self.rk.max_daily_loss_r}R"
        return False, ""
