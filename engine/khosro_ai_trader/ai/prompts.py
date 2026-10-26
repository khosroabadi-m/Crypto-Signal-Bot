"""Prompt builders for the AI trend-aggregation layer."""

from __future__ import annotations

import json
from typing import Any

SYSTEM_PROMPT_FA = """تو یک تحلیلگر ارشد بازار ارزهای دیجیتال هستی که داده‌های تجمیع‌شده از چند منبع را تحلیل می‌کند.

ورودی تو شامل:
- بورد ترند CoinGecko (پرجستجوترین کوین‌های ۲۴ ساعت)
- مومنتوم بازار از Binance (حجم معاملات و تغییر قیمت ۲۴ ساعت، جفت‌های USDT)
- کانتکست کلان بازار (سلطه BTC، تغییر کل مارکت‌کپ)
- تعداد اخبار/منشن‌های اجتماعی (در صورت موجود بودن)

وظایف تو:
1. ترکیب این منابع و پیدا کردن «ترندهای واقعی روز» — کوینی که در چند منبع همزمان ظاهر شود مهم‌تر از کوینی است که فقط در یک منبع داغ است.
2. برای هر ارز یک جهت‌دهی اولیه سیگنال بده: long / short / watch / avoid.
   - تغییر منفی شدید + حجم بالا + حضور در ترند = ریسک ادامه ریزش (short) یا فرصت برگشت؛ با کانتکست کلان تصمیم بگیر.
   - if BTC dominance در حال افزایش است، لانگ روی آلت‌کوین‌ها ریسک بالاتری دارد.
   - حجم نجومی نسبت به مارکت‌کپ (velocity بالا) = حرکت جدی، نه نوسان معمول.
3. اطمینان (confidence) واقع‌بینانه بده؛ بیشتر از ۹۰ فقط برای ست‌شدن چندمنبعی قوی.

قوانین خروجی (بسیار مهم):
- فقط و فقط یک JSON معتبر برگردان. هیچ متن، توضیح یا markdown اضافه نده.
- reasons حداکثر ۲ آیتم، هر آیتم یک جمله کوتاه فارسی.
- direction فقط یکی از: "long", "short", "watch", "avoid"
- trend فقط یکی از: "hot", "rising", "cooling", "neutral"
- sentiment فقط یکی از: "risk-on", "risk-off", "mixed"

ساختار خروجی:
{"market_summary": "...", "sentiment": "...", "sentiment_confidence": 0-100,
 "verdicts": [{"symbol": "...", "trend": "...", "direction": "...", "confidence": 0-100,
               "reasons": ["..."], "risk_note": "..."}]}"""


def build_user_prompt(
    macro: dict[str, Any] | None,
    coins: list[dict[str, Any]],
    news_headlines: list[str] | None = None,
) -> str:
    """Compact, token-efficient context for the model."""
    context: dict[str, Any] = {"macro": macro or {}}

    context["coins"] = [
        {
            "symbol": c.get("symbol"),
            "name": c.get("name"),
            "score": c.get("score"),
            "cg_trend_rank": c.get("cg_trend_rank"),
            "change_24h_pct": _round(c.get("stats", {}).get("change_24h_pct")),
            "volume_24h_usd": _round(c.get("stats", {}).get("volume_24h_usd"), 0),
            "market_cap_usd": _round(c.get("stats", {}).get("market_cap_usd"), 0),
            "vol_to_mcap": _ratio(
                c.get("stats", {}).get("volume_24h_usd"),
                c.get("stats", {}).get("market_cap_usd"),
            ),
            "binance_pair": c.get("binance_pair"),
            "mentions": c.get("mentions", 0),
        }
        for c in coins
    ]

    if news_headlines:
        context["recent_news_headlines"] = news_headlines[:12]

    return (
        "داده‌های تجمیع‌شده امروز (عدد حجم و مارکت‌کپ به دلار):\n"
        + json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        + "\n\nطبق ساختار مشخص‌شده، فقط JSON برگردان."
    )


def _round(value: Any, digits: int = 2) -> Any:
    try:
        return round(float(value), digits) if value is not None else None
    except (TypeError, ValueError):
        return None


def _ratio(vol: Any, mcap: Any) -> float | None:
    try:
        if vol and mcap and float(mcap) > 0:
            return round(float(vol) / float(mcap), 3)
    except (TypeError, ValueError):
        pass
    return None
