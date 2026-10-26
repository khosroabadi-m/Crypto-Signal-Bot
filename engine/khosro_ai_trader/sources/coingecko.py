"""CoinGecko source: trending list + market snapshot for enrichment.

Docs: https://api.coingecko.com/api/v3/search/trending  (free, no key)
      https://api.coingecko.com/api/v3/coins/markets    (free, rate-limited)
"""

from __future__ import annotations

from typing import Any

from .base import BaseSource, SourceError

BASE_URL = "https://api.coingecko.com/api/v3"


class CoinGeckoSource(BaseSource):
    name = "coingecko"

    def __init__(self, config) -> None:
        super().__init__(config)
        # Optional free "demo" key raises the public rate limit
        # (30 calls/min) — see https://www.coingecko.com/en/api/pricing
        if getattr(config, "coingecko_api_key", ""):
            self.session.headers["x-cg-demo-api-key"] = config.coingecko_api_key

    def fetch_global(self) -> dict[str, Any]:
        """Global market context (macro input for the AI layer). Free endpoint."""
        data = self.get_json(f"{BASE_URL}/global").get("data") or {}
        mcap_pct = data.get("market_cap_percentage") or {}
        out = {
            "total_market_cap_usd": (data.get("total_market_cap") or {}).get("usd"),
            "market_cap_change_24h_pct": data.get("market_cap_change_percentage_24h_usd"),
            "btc_dominance": mcap_pct.get("btc"),
            "eth_dominance": mcap_pct.get("eth"),
            "active_cryptocurrencies": data.get("active_cryptocurrencies"),
        }
        self.log.info("global snapshot: BTC dom %s%%, mcap 24h %s%%", 
                      round(out["btc_dominance"] or 0, 2),
                      round(out["market_cap_change_24h_pct"] or 0, 2))
        return out

    def fetch_trending(self) -> list[dict[str, Any]]:
        """Return ordered trending items: [{id, symbol, name, market_cap_rank, rank}]."""
        data = self.get_json(f"{BASE_URL}/search/trending")
        coins = data.get("coins") or []
        out: list[dict[str, Any]] = []
        for pos, item in enumerate(coins, start=1):
            info = item.get("item") or {}
            if not info.get("symbol"):
                continue
            out.append(
                {
                    "id": info.get("id"),
                    "symbol": str(info["symbol"]).upper(),
                    "name": info.get("name") or info["symbol"],
                    "market_cap_rank": info.get("market_cap_rank"),
                    "rank": pos,  # position on the trending board (1 = hottest)
                }
            )
        self.log.info("trending list fetched: %s coins", len(out))
        return out

    def fetch_markets(self, per_page: int = 250) -> dict[str, dict[str, Any]]:
        """Market snapshot keyed by CoinGecko id (top `per_page` by market cap)."""
        markets: dict[str, dict[str, Any]] = {}
        for page in (1, 2):
            data = self.get_json(
                f"{BASE_URL}/coins/markets",
                params={
                    "vs_currency": "usd",
                    "order": "market_cap_desc",
                    "per_page": min(per_page, 250),
                    "page": page,
                    "sparkline": "false",
                    "price_change_percentage": "24h",
                },
            )
            for row in data or []:
                markets[row.get("id")] = {
                    "symbol": str(row.get("symbol") or "").upper(),
                    "name": row.get("name"),
                    "price_usd": row.get("current_price"),
                    "change_24h_pct": row.get("price_change_percentage_24h"),
                    "volume_24h_usd": row.get("total_volume"),
                    "market_cap_usd": row.get("market_cap"),
                }
        self.log.info("markets snapshot: %s assets", len(markets))
        return markets

    def fetch(self) -> dict[str, Any]:
        """Combined payload; markets failure is tolerated (Paprika is the fallback)."""
        payload: dict[str, Any] = {"trending": [], "markets": {}}
        payload["trending"] = self.fetch_trending()
        try:
            payload["markets"] = self.fetch_markets(self.cfg.sources.markets_per_page)
        except SourceError as exc:
            self.log.warning("markets endpoint failed (fallback will cover): %s", exc)
        return payload
