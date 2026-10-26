"""CryptoPanic source (optional): news velocity per candidate coin.

Activates only when CRYPTOPANIC_API_KEY is present. Free key:
https://cryptopanic.com/developers/api/
"""

from __future__ import annotations

from typing import Any

from .base import BaseSource

BASE_URL = "https://cryptopanic.com/api/free/v1"


class CryptoPanicSource(BaseSource):
    name = "cryptopanic"

    @property
    def has_key(self) -> bool:
        return bool(self.cfg.cryptopanic_api_key)

    def fetch_news_counts(self, candidates: list[dict[str, Any]]) -> dict[str, int]:
        """Return {SYMBOL: number_of_recent_news_items} for the given candidates."""
        if not self.has_key:
            raise RuntimeError("CRYPTOPANIC_API_KEY is not set")

        counts: dict[str, int] = {c["symbol"]: 0 for c in candidates}
        filt = self.cfg.sources.cryptopanic_filter
        for cand in candidates:
            sym = cand["symbol"]
            try:
                data = self.get_json(
                    f"{BASE_URL}/posts/",
                    params={
                        "auth_token": self.cfg.cryptopanic_api_key,
                        "currencies": sym,
                        "filter": filt,
                        "public": "true",
                    },
                )
                results = data.get("results") or []
                counts[sym] = len(results)
            except Exception as exc:  # noqa: BLE001 — one bad symbol must not kill the run
                self.log.warning("cryptopanic query for %s failed: %s", sym, exc)
        return counts
