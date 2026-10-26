"""AIAnalyst — synthesizes multi-source data into trend verdicts via LLM."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ..config import Config
from ..logger import get_logger
from ..models import AIAnalysis, CoinVerdict, TrendingSnapshot
from .client import LLMError, OpenAICompatibleClient
from .prompts import SYSTEM_PROMPT_FA, build_user_prompt

log = get_logger("ai.analyst")

_ALLOWED_TREND = {"hot", "rising", "cooling", "neutral"}
_ALLOWED_DIRECTION = {"long", "short", "watch", "avoid"}
_ALLOWED_SENTIMENT = {"risk-on", "risk-off", "mixed"}


class AIAnalyst:
    """Aggregates all collected source data and asks the LLM for verdicts."""

    name = "ai_analyst"

    def __init__(self, config: Config) -> None:
        self.cfg = config
        self.client = OpenAICompatibleClient(config)

    @property
    def available(self) -> bool:
        import os

        return self.cfg.ai.enabled and bool(
            os.getenv("AI_API_KEY", "").strip()
            or os.getenv("ZAI_API_KEY", "").strip()
        )

    # ------------------------------------------------------------------

    def analyze(
        self,
        snapshot: TrendingSnapshot,
        macro: dict[str, Any] | None = None,
        news_headlines: list[str] | None = None,
    ) -> AIAnalysis | None:
        """One LLM call per run; returns None if the response is unusable."""
        coins = [
            {
                "symbol": c.symbol,
                "name": c.name,
                "score": c.score,
                "cg_trend_rank": _cg_rank(snapshot, c),
                "stats": c.stats.__dict__,
                "binance_pair": c.binance_pair,
                "mentions": c.mentions,
            }
            for c in snapshot.coins[: self.cfg.ai.max_candidates]
        ]
        user = build_user_prompt(macro, coins, news_headlines)

        log.info(
            "calling LLM (%s, %d coins, ~%d chars context)...",
            self.cfg.ai.model, len(coins), len(user),
        )
        raw = self.client.chat(SYSTEM_PROMPT_FA, user)
        parsed = extract_json(raw)
        if not parsed:
            log.error("LLM response had no parsable JSON object")
            return None

        analysis = self._validate(parsed)
        if analysis is None:
            log.error("LLM JSON failed validation — discarded")
            return None

        analysis.model = self.cfg.ai.model
        analysis.run_at_utc = snapshot.run_at_utc

        # keep only verdicts for coins we actually scanned
        known = {c.symbol.upper() for c in snapshot.coins}
        analysis.verdicts = [v for v in analysis.verdicts if v.symbol.upper() in known]
        log.info(
            "AI analysis ok ✔ sentiment=%s (%d%%), verdicts=%d",
            analysis.sentiment, analysis.sentiment_confidence, len(analysis.verdicts),
        )
        return analysis

    # ------------------------------------------------------------------

    def _validate(self, data: dict[str, Any]) -> AIAnalysis | None:
        if not isinstance(data, dict):
            return None
        verdicts_raw = data.get("verdicts")
        if not isinstance(verdicts_raw, list) or not verdicts_raw:
            return None

        verdicts: list[CoinVerdict] = []
        for item in verdicts_raw:
            if not isinstance(item, dict) or not item.get("symbol"):
                continue
            verdicts.append(CoinVerdict(
                symbol=str(item["symbol"]).upper(),
                trend=_enum(item.get("trend"), _ALLOWED_TREND, "neutral"),
                direction=_enum(item.get("direction"), _ALLOWED_DIRECTION, "watch"),
                confidence=_clamp(item.get("confidence"), 0, 100, 50),
                reasons=[str(r)[:140] for r in (item.get("reasons") or [])[:2]
                         if str(r).strip()],
                risk_note=str(item.get("risk_note") or "")[:140],
            ))
        if not verdicts:
            return None

        return AIAnalysis(
            model="",  # filled by caller
            run_at_utc="",
            market_summary=str(data.get("market_summary") or "")[:400],
            sentiment=_enum(data.get("sentiment"), _ALLOWED_SENTIMENT, "mixed"),
            sentiment_confidence=_clamp(data.get("sentiment_confidence"), 0, 100, 50),
            verdicts=verdicts,
        )


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _cg_rank(snapshot: TrendingSnapshot, coin) -> int | None:
    # TrendingCoin does not store cg rank; re-derive presence cheaply:
    # rank 0 means "not on the CG board" for the prompt.
    comp = coin.components.get("coingecko_trending")
    if comp is None:
        return None
    return int(round((1.0 - comp) * 14)) + 1  # inverse of the scoring formula


def _enum(value: Any, allowed: set[str], default: str) -> str:
    return str(value).strip().lower() if str(value).strip().lower() in allowed else default


def _clamp(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(round(float(value)))))
    except (TypeError, ValueError):
        return default


def extract_json(text: str) -> dict[str, Any] | None:
    """Robustly pull the first balanced JSON object out of an LLM reply."""
    if not text:
        return None
    text = text.strip()
    # strip markdown fences
    if "```" in text:
        for part in text.split("```"):
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                text = part
                break

    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : i + 1]
                try:
                    obj = json.loads(candidate)
                    return obj if isinstance(obj, dict) else None
                except json.JSONDecodeError:
                    return None
    return None
