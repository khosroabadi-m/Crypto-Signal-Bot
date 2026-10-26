"""Pure-Python technical indicators (no pandas/numpy — keeps CI fast & light).

All functions return lists aligned with the input length; warmup positions
hold `None` until enough bars exist. Designed for 1h OHLCV lists from Binance
klines. Used by both the live signal engine and the backtester so the rule
book behaves identically in production and in validation.
"""

from __future__ import annotations

from typing import Sequence

Num = float | None


def ema(values: Sequence[float], period: int) -> list[Num]:
    """Exponential moving average (SMA seed, standard EMA recursion)."""
    out: list[Num] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(values: Sequence[float], period: int = 14) -> list[Num]:
    """Wilder's RSI."""
    out: list[Num] = [None] * len(values)
    if len(values) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        diff = values[i] - values[i - 1]
        gains += max(diff, 0.0)
        losses += max(-diff, 0.0)
    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    for i in range(period + 1, len(values)):
        diff = values[i] - values[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(diff, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-diff, 0.0)) / period
        out[i] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return out


def macd(
    values: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[list[Num], list[Num], list[Num]]:
    """Returns (macd_line, signal_line, histogram)."""
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)
    macd_line: list[Num] = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(ema_fast, ema_slow)
    ]
    valid = [v for v in macd_line if v is not None]
    sig_valid = ema(valid, signal) if len(valid) >= signal else []
    offset = len(macd_line) - len(sig_valid)
    signal_line: list[Num] = [None] * offset + sig_valid
    hist: list[Num] = [
        (m - s) if (m is not None and s is not None) else None
        for m, s in zip(macd_line, signal_line)
    ]
    return macd_line, signal_line, hist


def atr(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
    period: int = 14,
) -> list[Num]:
    """Wilder's ATR (True Range smoothing)."""
    n = len(closes)
    out: list[Num] = [None] * n
    if n <= period:
        return out
    trs: list[float] = [highs[0] - lows[0]]
    for i in range(1, n):
        trs.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        )
    prev = sum(trs[1 : period + 1]) / period
    out[period] = prev
    for i in range(period + 1, n):
        prev = (prev * (period - 1) + trs[i]) / period
        out[i] = prev
    return out


def klines_to_ohlcv(rows: list[list]) -> dict[str, list[float]]:
    """Binance klines rows → OHLCV dict of float lists.

    Row layout: [openTime, open, high, low, close, volume, closeTime, ...]
    """
    return {
        "open": [float(r[1]) for r in rows],
        "high": [float(r[2]) for r in rows],
        "low": [float(r[3]) for r in rows],
        "close": [float(r[4]) for r in rows],
        "volume": [float(r[5]) for r in rows],
        "close_time": [int(r[6]) for r in rows],
    }


def last(value: Num, fallback: float = 0.0) -> float:
    """Safely read the newest indicator value (None → fallback)."""
    return value if value is not None else fallback
