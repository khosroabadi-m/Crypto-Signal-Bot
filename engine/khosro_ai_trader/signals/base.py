"""Signal engine contract (Phase 2 — awaiting the trading rule book).

The trending scanner produces a `TrendingSnapshot`; the signal engine will
consume it, evaluate the user's trading rules per coin, and emit signals.
Implementation lands in the next phase — only the interface is fixed now so
everything downstream (storage, notifications, backtests) can be built
against a stable contract.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..models import TrendingSnapshot


@dataclass
class Signal:
    """One actionable trading signal produced by the rule book."""

    symbol: str
    direction: str                      # "long" | "short" | "flat"
    entry: float | None = None
    stop_loss: float | None = None
    take_profits: list[float] = field(default_factory=list)
    timeframe: str | None = None        # e.g. "1h", "4h"
    confidence: float | None = None     # 0..100
    notes: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    # v2.0.0 — risk-engine fields (filled by RiskEngine.filter_and_size)
    pair: str | None = None             # Binance pair, e.g. "BTCUSDT"
    reasons: list[str] = field(default_factory=list)
    r_value: float | None = None        # per-unit risk distance in price terms
    rr: float | None = None             # reward:risk at TP2
    position_size_usd: float | None = None   # $ risked at stop (1R)
    notional_usd: float | None = None        # position value
    signal_id: str | None = None        # journal trace id, e.g. 20260921-1234-BTC-long


class SignalEngine(ABC):
    """Contract every future rule-book engine must satisfy."""

    @abstractmethod
    def evaluate(self, snapshot: TrendingSnapshot) -> list[Signal]:
        """Evaluate trading rules against a trending snapshot → signals."""


class UnimplementedSignalEngine(SignalEngine):
    """Safe placeholder: returns no signals instead of crashing the pipeline."""

    def evaluate(self, snapshot: TrendingSnapshot) -> list[Signal]:
        return []
