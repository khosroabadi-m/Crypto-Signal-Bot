"""Binance source: full 24h ticker board for the quote asset (USDT).

Why Binance?  No API key, extremely reliable, and — critically — every symbol
that survives here is a *tradeable* pair for the future signal engine.
Endpoint: GET /api/v3/ticker/24hr  (public, no key)
"""

from __future__ import annotations

import re
from typing import Any

from .base import BaseSource

BASE_URL = "https://api.binance.com"

# Leveraged-token suffixes to ignore (SOLUP, BTCDOWN, BTCBULL, ...)
_LEVERAGED = re.compile(r"(UP|DOWN|BULL|BEAR)$")


class BinanceSource(BaseSource):
    name = "binance"

    def __init__(self, config) -> None:
        super().__init__(config)
        self.quote = config.sources.quote_asset

    def fetch(self) -> dict[str, dict[str, Any]]:
        """Return {base_asset: {pair, price_usd, change_24h_pct, volume_24h_usd, trades}}.

        For base assets quoted in several markets the highest-volume pair wins.
        """
        tickers = self.get_json(f"{BASE_URL}/api/v3/ticker/24hr")
        best: dict[str, dict[str, Any]] = {}
        for row in tickers or []:
            symbol = row.get("symbol") or ""
            if not symbol.endswith(self.quote):
                continue
            base = symbol[: -len(self.quote)]
            if not base or _LEVERAGED.search(base):
                continue
            try:
                record = {
                    "pair": symbol,
                    "price_usd": float(row.get("lastPrice") or 0.0),
                    "change_24h_pct": float(row.get("priceChangePercent") or 0.0),
                    "volume_24h_usd": float(row.get("quoteVolume") or 0.0),
                    "trades": int(row.get("count") or 0),
                }
            except (TypeError, ValueError):
                continue
            prev = best.get(base)
            if prev is None or record["volume_24h_usd"] > prev["volume_24h_usd"]:
                best[base] = record
        self.log.info("binance board: %s %s-quoted assets", len(best), self.quote)
        return best
