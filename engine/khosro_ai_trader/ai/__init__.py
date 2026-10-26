"""AI aggregation layer — multi-source LLM synthesis for trend detection.

Flow:
  multiple sources (CoinGecko trending + Binance momentum + macro /global
  + optional news)  →  compact structured context  →  LLM (OpenAI-compatible)
  →  strict-JSON verdicts (trend, direction, confidence, reasons)
"""

from .client import LLMError, OpenAICompatibleClient
from .analyst import AIAnalyst

__all__ = ["AIAnalyst", "OpenAICompatibleClient", "LLMError"]
