"""Hybrid weighted scoring engine.

Pipeline:
  1. build candidates   — CoinGecko trending ∪ top-volume Binance assets
  2. enrich             — market stats from CoinGecko markets → Paprika fallback → Binance
  3. filter             — liquidity floors, stablecoin/blacklist exclusion, tradeability
  4. score components   — each normalized to 0..1
  5. combine            — weights from config, re-normalized over available components
  6. rank & cut         — top_n coins

All component scores are direction-agnostic: a coin pumping or dumping hard is
equally "trending" — the future signal engine decides direction.
"""

from __future__ import annotations

import math
from typing import Any

from .config import Config
from .logger import get_logger
from .models import MarketStats, TrendingCoin

log = get_logger("scoring")

STABLECOINS = {
    "USDT", "USDC", "DAI", "TUSD", "FDUSD", "USDE", "USDS", "PYUSD", "USD1",
    "USDTB", "USDP", "GUSD", "SUSD", "LUSD", "FRAX", "BUSD", "USDD", "EURT",
    "AEUR", "USDX", "USDG", "BSC-USD", "RLUSD", "USDF", "EUSD",
}

# price move caps for normalization (pump or dump beyond cap scores 1.0)
_PRICE_MOVE_CAP_PCT = 15.0
_MIN_VOL_FOR_MOMENTUM = 5_000_000  # below this a coin cannot "trend" via momentum


# --------------------------------------------------------------------------
# 1) candidates
# --------------------------------------------------------------------------

def build_candidates(
    trending: list[dict[str, Any]],
    binance: dict[str, dict[str, Any]],
    momentum_candidates: int,
) -> list[dict[str, Any]]:
    """Union of trending coins and top-volume Binance assets.

    Returns [{symbol, name, coingecko_id, binance_pair, cg_trend_rank}]
    keyed by symbol (upper-cased).
    """
    pool: dict[str, dict[str, Any]] = {}

    for item in trending or []:
        sym = item["symbol"]
        pool[sym] = {
            "symbol": sym,
            "name": item.get("name") or sym,
            "coingecko_id": item.get("id"),
            "binance_pair": None,
            "cg_trend_rank": item.get("rank"),
        }

    # top-volume Binance assets also become candidates (pure momentum entries)
    by_vol = sorted(
        binance.items(), key=lambda kv: kv[1].get("volume_24h_usd") or 0.0, reverse=True
    )
    for base, stats in by_vol[: max(1, momentum_candidates)]:
        sym = base.upper()
        if sym in pool:
            pool[sym]["binance_pair"] = stats["pair"]
        else:
            pool[sym] = {
                "symbol": sym,
                "name": sym,
                "coingecko_id": None,
                "binance_pair": stats["pair"],
                "cg_trend_rank": None,
            }
    log.info("candidate pool: %d symbols", len(pool))
    return list(pool.values())


# --------------------------------------------------------------------------
# 2) enrichment
# --------------------------------------------------------------------------

def enrich_candidates(
    candidates: list[dict[str, Any]],
    cg_markets: dict[str, dict[str, Any]],
    paprika: dict[str, dict[str, Any]],
    binance: dict[str, dict[str, Any]],
) -> None:
    """Attach MarketStats + binance pair to each candidate, in-place.

    Priority for fundamentals: CoinGecko markets → Paprika (by symbol).
    Binance always supplies pair/price/momentum stats when the pair exists.
    """
    markets_by_symbol: dict[str, dict[str, Any]] = {}
    for row in cg_markets.values():
        sym = row.get("symbol")
        if sym and sym not in markets_by_symbol:
            markets_by_symbol[sym] = row

    for cand in candidates:
        sym = cand["symbol"]
        stats = MarketStats()

        src = markets_by_symbol.get(sym) or (cg_markets.get(cand.get("coingecko_id") or ""))
        if src:
            stats.merge(MarketStats(
                price_usd=src.get("price_usd"),
                change_24h_pct=src.get("change_24h_pct"),
                volume_24h_usd=src.get("volume_24h_usd"),
                market_cap_usd=src.get("market_cap_usd"),
            ))
        if sym in paprika:
            p = paprika[sym]
            stats.merge(MarketStats(
                price_usd=p.get("price_usd"),
                change_24h_pct=p.get("change_24h_pct"),
                volume_24h_usd=p.get("volume_24h_usd"),
                market_cap_usd=p.get("market_cap_usd"),
            ))

        bstats = binance.get(sym.lower()) or binance.get(sym)
        if bstats:
            cand["binance_pair"] = cand["binance_pair"] or bstats["pair"]
            stats.merge(MarketStats(
                price_usd=bstats.get("price_usd"),
                change_24h_pct=bstats.get("change_24h_pct"),
                volume_24h_usd=bstats.get("volume_24h_usd"),
            ))
            cand["binance_trades"] = bstats.get("trades", 0)

        cand["stats"] = stats


# --------------------------------------------------------------------------
# 3) filters
# --------------------------------------------------------------------------

def apply_filters(candidates: list[dict[str, Any]], cfg: Config) -> list[dict[str, Any]]:
    t = cfg.trending
    blacklist = set(t.exclude_symbols) | (STABLECOINS if t.exclude_stablecoins else set())
    kept: list[dict[str, Any]] = []

    for cand in candidates:
        sym = cand["symbol"]
        if sym in blacklist:
            continue
        if t.require_binance_pair and not cand.get("binance_pair"):
            continue
        stats: MarketStats = cand.get("stats") or MarketStats()
        vol = stats.volume_24h_usd or 0.0
        mcap = stats.market_cap_usd
        if vol < t.min_volume_24h_usd:
            continue
        if mcap is not None and mcap < t.min_market_cap_usd:
            continue
        kept.append(cand)

    log.info(
        "filters: %d/%d candidates kept (mcap>=%s, vol>=%s, stablecoin blacklist=%d)",
        len(kept), len(candidates),
        f"{t.min_market_cap_usd:,.0f}", f"{t.min_volume_24h_usd:,.0f}", len(blacklist),
    )
    return kept


# --------------------------------------------------------------------------
# 4) component scores (0..1)
# --------------------------------------------------------------------------

def _log_norm(value: float, max_value: float, floor: float = 1.0) -> float:
    """Log-scale normalization; robust to whale outliers."""
    if value <= 0 or max_value <= 0:
        return 0.0
    value = max(value, floor)
    max_value = max(max_value, floor)
    return max(0.0, min(1.0, math.log10(value) / math.log10(max_value)))


def compute_components(
    coins: list[dict[str, Any]],
    reddit_mentions: dict[str, int] | None,
) -> None:
    """Attach `components` dict to each candidate (in-place)."""
    max_vol = max(
        ((c.get("stats").volume_24h_usd or 0.0) for c in coins), default=0.0
    )
    max_mcap = max(
        ((c.get("stats").market_cap_usd or 0.0) for c in coins), default=0.0
    )
    max_mentions = max((reddit_mentions or {}).values(), default=0)

    for cand in coins:
        stats: MarketStats = cand["stats"]
        components: dict[str, float] = {}

        # (a) CoinGecko trending board position
        trend_rank = cand.get("cg_trend_rank")
        if trend_rank is not None:
            components["coingecko_trending"] = max(0.0, 1.0 - (trend_rank - 1) / 14.0)

        # (b) market momentum: volume velocity (60%) + price move magnitude (40%)
        vol = stats.volume_24h_usd or 0.0
        if vol > 0:
            velocity = _log_norm(vol, max_vol) if max_vol > 0 else 0.0
            move = min(abs(stats.change_24h_pct or 0.0) / _PRICE_MOVE_CAP_PCT, 1.0)
            if vol >= _MIN_VOL_FOR_MOMENTUM:
                components["market_momentum"] = 0.6 * velocity + 0.4 * move

        # (c) liquidity depth (market cap)
        mcap = stats.market_cap_usd
        if mcap and max_mcap > 0:
            components["liquidity"] = _log_norm(mcap, max_mcap)

        # (d) social/news mentions
        if reddit_mentions is not None:
            mentions = reddit_mentions.get(cand["symbol"], 0)
            cand["mentions"] = mentions
            if max_mentions > 0:
                components["reddit_mentions"] = _log_norm(mentions, max_mentions)

        cand["components"] = components


# --------------------------------------------------------------------------
# 5) combine + 6) rank
# --------------------------------------------------------------------------

def combine_and_rank(coins: list[dict[str, Any]], cfg: Config) -> list[TrendingCoin]:
    """Weighted sum over available components (weights re-normalized), then rank."""
    base_weights = dict(cfg.weights)

    # count how many candidates carry each component
    availability: dict[str, int] = {}
    for cand in coins:
        for comp in cand.get("components") or {}:
            availability[comp] = availability.get(comp, 0) + 1

    active = {
        comp: base_weights.get(comp, 0.0)
        for comp, count in availability.items()
        if count > 0 and base_weights.get(comp, 0.0) > 0
    }
    total_w = sum(active.values())
    if total_w <= 0:  # pathological config — fall back to equal weights
        active = {comp: 1.0 for comp in availability}
        total_w = sum(active.values())
    weights_used = {comp: round(w / total_w, 4) for comp, w in active.items()}

    out: list[TrendingCoin] = []
    for cand in coins:
        components: dict[str, float] = cand.get("components") or {}
        score = sum(weights_used.get(comp, 0.0) * val for comp, val in components.items())
        stats: MarketStats = cand["stats"]
        out.append(TrendingCoin(
            symbol=cand["symbol"],
            name=cand.get("name") or cand["symbol"],
            coingecko_id=cand.get("coingecko_id"),
            binance_pair=cand.get("binance_pair"),
            stats=stats,
            components={k: round(v, 4) for k, v in components.items()},
            score=round(score * 100.0, 1),
            sources=sorted(cand.get("sources_seen") or _seen_sources(cand)),
            mentions=cand.get("mentions", 0),
        ))

    out.sort(key=lambda c: (-c.score, -(c.stats.volume_24h_usd or 0.0)))
    for i, coin in enumerate(out, start=1):
        coin.rank = i

    top = out[: cfg.trending.top_n]
    log.info(
        "scored %d coins with weights %s → kept top %d",
        len(out), weights_used, len(top),
    )
    return top, weights_used  # type: ignore[return-value]


def _seen_sources(cand: dict[str, Any]) -> list[str]:
    seen: list[str] = []
    if cand.get("cg_trend_rank") is not None:
        seen.append("coingecko_trending")
    if cand.get("binance_pair"):
        seen.append("binance")
    if cand.get("coingecko_id") and cand.get("stats").market_cap_usd:
        seen.append("coingecko_markets")
    if cand.get("symbol") and cand.get("_paprika"):
        seen.append("coinpaprika")
    return seen
