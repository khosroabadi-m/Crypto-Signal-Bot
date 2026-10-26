"""Professional data edge — all free endpoints, no keys required.

Per-coin enrichment (futures funding rate, open interest change, global
long/short account ratio, spot order-book imbalance) plus a global Fear &
Greed snapshot. Binance Futures symbols sometimes differ from spot
(e.g. 1000PEPEUSDT), so the hub resolves the futures symbol with a
"1000"-prefix fallback and degrades gracefully when a market doesn't exist.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from ..config import Config
from ..logger import get_logger
from .base import BaseSource

SPOT = "https://api.binance.com"
FAPI = "https://fapi.binance.com"
FNG = "https://api.alternative.me/fng/"

log = get_logger("source.marketdata")


class MarketDataHub(BaseSource):
    """Aggregated derivatives/order-flow/sentiment data for signal inputs."""

    name = "marketdata"

    def __init__(self, config: Config) -> None:
        super().__init__(config)
        self.md = config.market_data
        self._fapi_symbol_cache: dict[str, str | None] = {}

    # ------------------------------------------------------------------
    # raw fetchers
    # ------------------------------------------------------------------
    def fetch_klines(
        self, symbol: str, interval: str = "1h", limit: int = 300
    ) -> list[list]:
        """Spot klines (OHLCV). Returns raw rows: [openTime, o,h,l,c,v, ...]."""
        data = self.get_json(
            f"{SPOT}/api/v3/klines",
            params={"symbol": symbol.upper(), "interval": interval, "limit": limit},
        )
        return data if isinstance(data, list) else []

    def fetch_klines_range(
        self, symbol: str, interval: str = "1h", days: int = 120, cap: int = 9000
    ) -> list[list]:
        """Paged historical klines going back `days` days (for backtesting)."""
        interval_ms = self._interval_ms(interval)
        end_ms = self._server_time()
        start_ms = end_ms - days * 86_400_000
        rows: list[list] = []
        cursor = start_ms
        while cursor < end_ms and len(rows) < cap:
            batch = self.get_json(
                f"{SPOT}/api/v3/klines",
                params={
                    "symbol": symbol.upper(),
                    "interval": interval,
                    "startTime": cursor,
                    "endTime": end_ms,
                    "limit": 1000,
                },
            )
            if not isinstance(batch, list) or not batch:
                break
            rows.extend(batch)
            cursor = int(batch[-1][6]) + 1  # closeTime + 1ms
            if len(batch) < 1000:
                break
        return rows

    def fetch_order_book_imbalance(self, symbol: str) -> dict[str, float] | None:
        """Bid/ask volume imbalance from the spot order book.

        imbalance > 0 → buyers heavier (supportive of longs).
        """
        data = self.get_json(
            f"{SPOT}/api/v3/depth",
            params={"symbol": symbol.upper(), "limit": self.md.depth_limit},
        )
        bids = sum(float(b[1]) for b in data.get("bids", []))
        asks = sum(float(a[1]) for a in data.get("asks", []))
        total = bids + asks
        if total <= 0:
            return None
        spread = (
            (float(data["asks"][0][0]) - float(data["bids"][0][0]))
            / float(data["asks"][0][0]) * 100
            if data.get("asks") and data.get("bids") else None
        )
        return {"imbalance": (bids - asks) / total, "bid_vol": bids, "ask_vol": asks,
                "spread_pct": spread}

    def fetch_funding(self, pair: str) -> dict[str, float] | None:
        """Current funding rate (per 8h) + mark price from Binance Futures."""
        sym = self.resolve_fapi_symbol(pair)
        if not sym:
            return None
        d = self.get_json(f"{FAPI}/fapi/v1/premiumIndex", params={"symbol": sym}, retries=1)
        return {
            "funding_rate_8h_pct": float(d.get("lastFundingRate", 0)) * 100,
            "mark_price": float(d.get("markPrice", 0) or 0),
            "fapi_symbol": sym,
        }

    def fetch_open_interest(self, pair: str) -> dict[str, float] | None:
        """Open interest + 24h change (rising OI confirms a trend's fuel)."""
        sym = self.resolve_fapi_symbol(pair)
        if not sym:
            return None
        cur = self.get_json(f"{FAPI}/fapi/v1/openInterest", params={"symbol": sym}, retries=1)
        hist = self.get_json(
            f"{FAPI}/futures/data/openInterestHist",
            params={"symbol": sym, "period": self.md.oi_period, "limit": 25},
            retries=1,
        )
        oi_now = float(cur.get("openInterest", 0) or 0)
        oi_prev = float(hist[0].get("sumOpenInterest", 0)) if isinstance(hist, list) and hist else 0
        change_pct = ((oi_now - oi_prev) / oi_prev * 100) if oi_prev > 0 else None
        return {"open_interest": oi_now, "oi_change_pct": change_pct, "fapi_symbol": sym}

    def fetch_long_short_ratio(self, pair: str) -> dict[str, float] | None:
        """Global long/short account ratio (crowd positioning)."""
        sym = self.resolve_fapi_symbol(pair)
        if not sym:
            return None
        data = self.get_json(
            f"{FAPI}/futures/data/globalLongShortAccountRatio",
            params={"symbol": sym, "period": self.md.lsr_period, "limit": 1},
            retries=1,
        )
        if not isinstance(data, list) or not data:
            return None
        return {
            "lsr": float(data[-1].get("longShortRatio", 0) or 0),
            "long_accounts_pct": float(data[-1].get("longAccount", 0) or 0) * 100,
        }

    def fetch_fear_greed(self) -> dict[str, Any] | None:
        """Crypto Fear & Greed index (0-100, alternative.me)."""
        if not self.md.fng_enabled:
            return None
        data = self.get_json(FNG, params={"limit": 1})
        entries = data.get("data") if isinstance(data, dict) else None
        if not entries:
            return None
        return {
            "value": int(entries[0]["value"]),
            "label": entries[0].get("value_classification", ""),
        }

    # ------------------------------------------------------------------
    # batch enrichment
    # ------------------------------------------------------------------
    def enrich_coins(self, coins: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
        """Parallel enrichment for top coins.

        `coins` = [{"symbol": "BTC", "pair": "BTCUSDT"}, ...]
        Returns {SYMBOL: {"funding": …, "oi": …, "lsr": …, "depth": …}}
        — each sub-block optional (missing market / API hiccup tolerated).
        """
        result: dict[str, dict[str, Any]] = {}
        if not coins:
            return result
        fng = None
        try:
            fng = self.fetch_fear_greed()
        except Exception as exc:  # noqa: BLE001
            log.warning("fear&greed unavailable: %s", exc)

        jobs = {}
        with ThreadPoolExecutor(max_workers=self.md.max_workers) as pool:
            for c in coins:
                sym, pair = c["symbol"], c["pair"]
                jobs[pool.submit(self._enrich_one, sym, pair)] = sym
            for fut in as_completed(jobs):
                sym = jobs[fut]
                try:
                    payload = fut.result()
                    if payload:
                        payload["fear_greed"] = fng
                        result[sym] = payload
                except Exception as exc:  # noqa: BLE001
                    log.warning("enrich %s failed: %s", sym, exc)
        return result

    def _enrich_one(self, symbol: str, pair: str) -> dict[str, Any]:
        block: dict[str, Any] = {}
        for key, fn in (
            ("funding", self.fetch_funding),
            ("oi", self.fetch_open_interest),
            ("lsr", self.fetch_long_short_ratio),
            ("depth", self.fetch_order_book_imbalance),
        ):
            try:
                block[key] = fn(pair)
            except Exception as exc:  # noqa: BLE001 — single metric must not fail the coin
                log.debug("%s/%s unavailable: %s", symbol, key, exc)
        return block

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def resolve_fapi_symbol(self, spot_pair: str) -> str | None:
        """Map a spot pair to its futures symbol (cached, with 1000-prefix try)."""
        if spot_pair in self._fapi_symbol_cache:
            return self._fapi_symbol_cache[spot_pair]
        sym: str | None = spot_pair.upper()
        try:
            self.get_json(f"{FAPI}/fapi/v1/premiumIndex", params={"symbol": sym}, retries=0)
        except Exception:  # noqa: BLE001
            try:
                sym = "1000" + spot_pair.upper()
                self.get_json(f"{FAPI}/fapi/v1/premiumIndex", params={"symbol": sym}, retries=0)
            except Exception:  # noqa: BLE001 — no futures market for this asset
                sym = None
        self._fapi_symbol_cache[spot_pair] = sym
        return sym

    def _server_time(self) -> int:
        try:
            return int(self.get_json(f"{SPOT}/api/v3/time")["serverTime"])
        except Exception:  # noqa: BLE001
            import time as _t
            return int(_t.time() * 1000)

    @staticmethod
    def _interval_ms(interval: str) -> int:
        unit = interval[-1]
        amount = int(interval[:-1])
        mult = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}
        return amount * mult.get(unit, 3_600_000)
