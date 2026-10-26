"""Source adapters package — each provider exposes one resilient fetch method."""

from .base import BaseSource, SourceError
from .coingecko import CoinGeckoSource
from .binance import BinanceSource
from .coinpaprika import CoinPaprikaSource
from .reddit import RedditSource
from .cryptopanic import CryptoPanicSource
from .market_data import MarketDataHub

__all__ = [
    "BaseSource",
    "SourceError",
    "CoinGeckoSource",
    "BinanceSource",
    "CoinPaprikaSource",
    "RedditSource",
    "CryptoPanicSource",
    "MarketDataHub",
]
