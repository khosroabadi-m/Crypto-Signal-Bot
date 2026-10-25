# indicators.py — اندیکاتورهای موردنیاز سناریوها (پیاده‌سازی ساده و قطعی)

from typing import List, Optional


def ema_series(values: List[float], period: int) -> List[Optional[float]]:
    """EMA استاندارد؛ seed = میانگین اولین period مقدار."""
    n = len(values)
    out: List[Optional[float]] = [None] * n
    if n < period or period <= 0:
        return out
    k = 2.0 / (period + 1.0)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, n):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def ema(values: List[float], period: int) -> Optional[float]:
    s = ema_series(values, period)
    return s[-1] if s and s[-1] is not None else None


def atr_series(candles, period=14) -> List[Optional[float]]:
    """ATR با روش Wilder؛ خروجی هم‌طول candles."""
    n = len(candles)
    out: List[Optional[float]] = [None] * n
    if n < period + 1:
        return out
    trs = []
    for i in range(1, n):
        h, l = candles[i]["h"], candles[i]["l"]
        pc = candles[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    # ATR اول: میانگین ساده اولین period تا
    atr = sum(trs[:period]) / period
    out[period] = atr
    for i in range(period + 1, n):
        atr = (atr * (period - 1) + trs[i - 1]) / period
        out[i] = atr
    return out


def atr(candles, period=14) -> Optional[float]:
    s = atr_series(candles, period)
    return s[-1] if s and s[-1] is not None else None


def highest(candles, i: int, lookback: int) -> float:
    """بالاترین high در پنجره [i-lookback, i-1] (کندل فعلی i را نمی‌بیند)."""
    return max(c["h"] for c in candles[i - lookback:i])


def lowest(candles, i: int, lookback: int) -> float:
    return min(c["l"] for c in candles[i - lookback:i])


def avg_volume(candles, i: int, lookback=20) -> float:
    prev = candles[max(0, i - lookback):i]
    if not prev:
        return 0.0
    return sum(c["v"] for c in prev) / len(prev)


def efficiency_ratio(candles, i: int, period=10) -> float:
    """نسبت کافمن (Kaufman ER): |جابجایی خالص| / جمع |تغییرات|. 0=نویز، 1=روند خالص."""
    if i < period:
        return 0.0
    net = abs(candles[i]["c"] - candles[i - period]["c"])
    path = sum(abs(candles[j]["c"] - candles[j - 1]["c"]) for j in range(i - period + 1, i + 1))
    return (net / path) if path > 0 else 0.0


def max_high(candles, i: int, lookback: int) -> float:
    """بالاترین high در [i-lookback+1, i] شامل کندل فعلی (برای drawdown گیت)."""
    return max(c["h"] for c in candles[i - lookback + 1:i + 1])


def rsi_series(values: List[float], period=14) -> List[Optional[float]]:
    n = len(values)
    out: List[Optional[float]] = [None] * n
    if n < period + 1:
        return out
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        d = values[i] - values[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    ag, al = gains / period, losses / period
    out[period] = 100.0 - 100.0 / (1.0 + (ag / al if al > 0 else 1e9))
    for i in range(period + 1, n):
        d = values[i] - values[i - 1]
        ag = (ag * (period - 1) + max(d, 0.0)) / period
        al = (al * (period - 1) + max(-d, 0.0)) / period
        out[i] = 100.0 - 100.0 / (1.0 + (ag / al if al > 0 else 1e9))
    return out


def macd(values: List[float], fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD line, signal line, histogram — same recursion as KhosroAiTrader rule book."""
    ema_fast = ema_series(values, fast)
    ema_slow = ema_series(values, slow)
    macd_line = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(ema_fast, ema_slow)
    ]
    valid = [v for v in macd_line if v is not None]
    if len(valid) < signal:
        sig_valid: List[Optional[float]] = []
    else:
        sig_valid = ema_series(valid, signal)
        # ema_series returns full length of `valid`; keep as-is
    offset = len(macd_line) - len(sig_valid)
    signal_line: List[Optional[float]] = [None] * offset + list(sig_valid)
    hist = [
        (m - s) if (m is not None and s is not None) else None
        for m, s in zip(macd_line, signal_line)
    ]
    return macd_line, signal_line, hist
