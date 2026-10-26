"""CoinPaprika source: keyless fallback market snapshot (enrichment).

Docs: https://api.coinpaprika.com/v1/tickers  (free, no key)
"""

from __future__ import annotations

from typing import Any

from .base import BaseSource

BASE_URL = "https://api.coinpaprika.com"


class CoinPaprikaSource(BaseSource):
    name = "coinpaprika"

    def fetch(self) -> dict[str, dict[str, Any]]:
        """Return {SYMBOL: {name, price_usd, change_24h_pct, volume_24h_usd, market_cap_usd}}."""
        tickers = self.get_json(f"{BASE_URL}/v1/tickers")
        out: dict[str, dict[str, Any]] = {}
        for row in tickers or []:
            symbol = str(row.get("symbol") or "").upper()
            if not symbol or symbol in out:
                continue
            quotes = (row.get("quotes") or {}).get("USD") or {}
            out[symbol] = {
                "name": row.get("name"),
                "price_usd": quotes.get("price"),
                "change_24h_pct": quotes.get("percent_change_24h"),
                "volume_24h_usd": quotes.get("volume_24h"),
                "market_cap_usd": quotes.get("market_cap"),
            }
        self.log.info("paprika snapshot: %s assets", len(out))
        return out
