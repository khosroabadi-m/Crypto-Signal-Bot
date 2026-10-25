# kucoin.py — کلاینت عمومی KuCoin (بدون احراز هویت) + کش دیسکی

import json
import time
import requests
from . import settings
from .utils import utc_ts


class KuCoinError(Exception):
    pass


def _get(path, params, timeout=25, retries=3):
    last_err = None
    for i in range(retries):
        try:
            r = requests.get(settings.KUCOIN_BASE + path, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json().get("data")
            if r.status_code == 429:
                time.sleep(1.5 * (i + 1))
                continue
            last_err = f"HTTP {r.status_code}: {r.text[:150]}"
        except Exception as e:
            last_err = str(e)
            time.sleep(1.0 * (i + 1))
    raise KuCoinError(f"KuCoin request failed {path} {params}: {last_err}")


def fetch_candles(symbol: str, ctype: str, start_ts: int, end_ts: int, use_cache=True):
    """کندل‌های KuCoin؛ خروجی صعودی بر اساس زمان شروع کندل.
    هر ردیف: dict(t,o,h,l,c,v) — t=زمان شروع کندل (ثانیه)."""
    cache_key = f"{symbol}_{ctype}_{start_ts}_{end_ts}.json"
    cache_path = settings.CACHE_DIR + "/" + cache_key
    if use_cache:
        try:
            import os
            if os.path.isfile(cache_path):
                with open(cache_path, encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
    raw = _get(settings.KUCOIN_CANDLES, {
        "symbol": symbol, "type": ctype,
        "startAt": int(start_ts), "endAt": int(end_ts),
    }) or []
    # KuCoin جدیدترین را اول می‌دهد → معکوس
    candles = [{
        "t": int(c[0]), "o": float(c[1]), "c": float(c[2]),
        "h": float(c[3]), "l": float(c[4]), "v": float(c[5]),
    } for c in raw]
    candles.sort(key=lambda x: x["t"])
    # endAt may be inclusive on some API versions; the candle at end_ts is not closed yet.
    candles = [c for c in candles if c["t"] < int(end_ts)]
    if use_cache:
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(candles, f)
        except Exception:
            pass
    return candles


def fetch_recent(symbol: str, ctype: str, days: int, use_cache=True, now_ts=None):
    """کندل‌های «بسته‌شده» recent با کش دیسکی پایدار (کلید کش روی مرز کندل گرد می‌شود)."""
    now = int(now_ts) if now_ts else int(time.time())
    step = {"30min": 1800, "15min": 900, "5min": 300, "1min": 60, "1hour": 3600, "4hour": 14400}.get(ctype)
    if step:
        now = (now // step) * step  # فقط کندل‌های بسته‌شده → کلید کش ثابت در هر کندل
    end_ts = now
    start_ts = end_ts - days * 86400
    if use_cache:
        return fetch_candles(symbol, ctype, start_ts, end_ts, use_cache=True)
    return fetch_candles(symbol, ctype, start_ts, end_ts, use_cache=False)


def fetch_recent_minutes(symbol: str, ctype: str, minutes: int, use_cache=True, now_ts=None):
    """داده اخیر بر حسب دقیقه؛ فقط کندل‌های کاملاً بسته‌شده."""
    now = int(now_ts) if now_ts else int(time.time())
    step = {"30min": 1800, "4hour": 14400, "1min": 60, "1hour": 3600}.get(ctype)
    if step:
        now = (now // step) * step
    start_ts = now - int(minutes) * 60
    return fetch_candles(symbol, ctype, start_ts, now, use_cache=use_cache)


def fetch_ticker(symbol: str):
    """آخرین قیمت معامله (برای ورود لحظه‌ای در ربات زنده)."""
    d = _get(settings.KUCOIN_TICKER, {"symbol": symbol})
    try:
        return float(d.get("price"))
    except Exception:
        return None


def chunked_candles(symbol: str, ctype: str, start_ts: int, end_ts: int, chunk_days=25):
    """برای بازه‌های بلند: KuCoin تا ~1500 کندل در هر درخواست می‌دهد."""
    out = []
    cur = start_ts
    step = chunk_days * 86400
    while cur < end_ts:
        nxt = min(cur + step, end_ts)
        out.extend(fetch_candles(symbol, ctype, cur, nxt))
        cur = nxt
        time.sleep(0.15)
    # حذف تکراری بر اساس t
    seen, uniq = set(), []
    for c in out:
        if c["t"] not in seen:
            seen.add(c["t"])
            uniq.append(c)
    return uniq


def fetch_1h_binance_style(symbol: str, limit: int = 300, use_cache: bool = True):
    """کندل 1hour کوکوین → ردیف خام Binance-style برای RuleSignalEngine.

    symbol: فرم KuCoin مثل BTC-USDT
    """
    import time as _time
    now = int(_time.time())
    now = (now // 3600) * 3600  # فقط میله‌های بسته‌شده
    start = now - int(limit) * 3600
    candles = fetch_candles(symbol, "1hour", start, now, use_cache=use_cache)
    rows = []
    for c in candles[-limit:]:
        ms = int(c["t"]) * 1000
        rows.append([
            ms, str(c["o"]), str(c["h"]), str(c["l"]), str(c["c"]), str(c.get("v") or 0),
            ms + 3_599_999, "0", 0, "0", "0", "0",
        ])
    return rows
