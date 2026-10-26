"""Data models shared across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class MarketStats:
    """Market snapshot for one asset (all values USD where applicable)."""

    price_usd: float | None = None
    change_24h_pct: float | None = None
    volume_24h_usd: float | None = None
    market_cap_usd: float | None = None

    def merge(self, other: "MarketStats") -> None:
        """Fill missing fields from `other` (existing values win)."""
        for f in ("price_usd", "change_24h_pct", "volume_24h_usd", "market_cap_usd"):
            if getattr(self, f) is None:
                setattr(self, f, getattr(other, f))


@dataclass
class TrendingCoin:
    """One candidate coin with its scoring breakdown."""

    symbol: str
    name: str
    coingecko_id: str | None = None
    binance_pair: str | None = None
    stats: MarketStats = field(default_factory=MarketStats)
    components: dict[str, float] = field(default_factory=dict)  # component -> 0..1
    score: float = 0.0          # final weighted score 0..100
    rank: int = 0
    sources: list[str] = field(default_factory=list)
    mentions: int = 0           # reddit/social mention count (when available)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class CoinVerdict:
    """AI verdict for one coin (from the multi-source aggregation layer)."""

    symbol: str
    trend: str = "neutral"            # hot | rising | cooling | neutral
    direction: str = "watch"           # long | short | watch | avoid
    confidence: int = 50               # 0..100
    reasons: list[str] = field(default_factory=list)
    risk_note: str = ""


@dataclass
class AIAnalysis:
    """Output of the LLM trend-aggregation layer for one run."""

    model: str
    run_at_utc: str
    market_summary: str = ""
    sentiment: str = "mixed"           # risk-on | risk-off | mixed
    sentiment_confidence: int = 50     # 0..100
    verdicts: list[CoinVerdict] = field(default_factory=list)

    def verdict_for(self, symbol: str) -> CoinVerdict | None:
        sym = symbol.upper()
        return next((v for v in self.verdicts if v.symbol.upper() == sym), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "run_at_utc": self.run_at_utc,
            "market_summary": self.market_summary,
            "sentiment": self.sentiment,
            "sentiment_confidence": self.sentiment_confidence,
            "verdicts": [asdict(v) for v in self.verdicts],
        }


@dataclass
class TrendingSnapshot:
    """Full output of one pipeline run."""

    run_at_utc: str
    run_at_tehran: str
    duration_seconds: float
    coins: list[TrendingCoin] = field(default_factory=list)
    sources_ok: list[str] = field(default_factory=list)
    sources_failed: dict[str, str] = field(default_factory=dict)
    weights_used: dict[str, float] = field(default_factory=dict)
    filters: dict[str, Any] = field(default_factory=dict)
    candidate_count: int = 0
    macro: dict[str, Any] | None = None        # global market context (/global)
    ai: AIAnalysis | None = None               # AI aggregation-layer output
    meta_signals: list[str] = field(default_factory=list)  # v2.0.0: symbols with approved signals

    def verdict_for(self, symbol: str) -> CoinVerdict | None:
        return self.ai.verdict_for(symbol) if self.ai else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_at_utc": self.run_at_utc,
            "run_at_tehran": self.run_at_tehran,
            "duration_seconds": round(self.duration_seconds, 2),
            "sources_ok": self.sources_ok,
            "sources_failed": self.sources_failed,
            "candidate_count": self.candidate_count,
            "meta_signals": self.meta_signals,
            "weights_used": self.weights_used,
            "filters": self.filters,
            "macro": self.macro,
            "coins": [c.to_dict() for c in self.coins],
            "ai": self.ai.to_dict() if self.ai else None,
        }
