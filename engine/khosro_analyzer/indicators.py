from __future__ import annotations
from typing import List, Dict, Optional, Tuple
import math


def ema_series(prices: List[float], period: int) -> List[Optional[float]]:
    if period <= 0 or len(prices) < period:
        return [None] * len(prices)
    k = 2.0 / (period + 1.0)
    out = [None] * (period - 1)
    value = sum(prices[:period]) / period
    out.append(value)
    for price in prices[period:]:
        value = price * k + value * (1.0 - k)
        out.append(value)
    return out


def calculate_ema(prices: List[float], period: int) -> Optional[float]:
    s = ema_series(prices, period)
    return s[-1] if s else None


def calculate_rsi(prices: List[float], period: int = 14) -> Optional[float]:
    if len(prices) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(prices)):
        d = prices[i] - prices[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    if avg_gain == 0:
        return 0.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def calculate_macd(prices: List[float], fast: int = 12, slow: int = 26, signal_period: int = 9) -> Dict[str, Optional[float]]:
    if len(prices) < slow + signal_period:
        return {"macd": None, "signal": None, "histogram": None}
    fast_s = ema_series(prices, fast)
    slow_s = ema_series(prices, slow)
    macd = [None if a is None or b is None else a - b for a, b in zip(fast_s, slow_s)]
    valid = [x for x in macd if x is not None]
    sig = ema_series(valid, signal_period)
    signal_value = sig[-1] if sig else None
    line_value = valid[-1] if valid else None
    hist = line_value - signal_value if line_value is not None and signal_value is not None else None
    return {"macd": line_value, "signal": signal_value, "histogram": hist}


def calculate_atr(candles: List[dict], period: int = 14) -> Optional[float]:
    if len(candles) < period + 1:
        return None
    tr = []
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i - 1]
        tr.append(max(c['h'] - c['l'], abs(c['h'] - p['c']), abs(c['l'] - p['c'])))
    atr = sum(tr[:period]) / period
    for x in tr[period:]:
        atr = (atr * (period - 1) + x) / period
    return atr


def body_strength(candle: dict) -> float:
    rng = max(candle['h'] - candle['l'], 1e-12)
    return abs(candle['c'] - candle['o']) / rng


def calculate_adx(candles: List[dict], period: int = 14) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if len(candles) < period * 2 + 1:
        return None, None, None
    trs, plus_dm, minus_dm = [], [], []
    for i in range(1, len(candles)):
        cur, prev = candles[i], candles[i - 1]
        up = cur['h'] - prev['h']
        down = prev['l'] - cur['l']
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        trs.append(max(cur['h'] - cur['l'], abs(cur['h'] - prev['c']), abs(cur['l'] - prev['c'])))

    atr = sum(trs[:period]) / period
    p = sum(plus_dm[:period]) / period
    m = sum(minus_dm[:period]) / period
    dx = []
    for i in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[i]) / period
        p = (p * (period - 1) + plus_dm[i]) / period
        m = (m * (period - 1) + minus_dm[i]) / period
        pdi = 100.0 * p / atr if atr else 0.0
        mdi = 100.0 * m / atr if atr else 0.0
        dx.append(100.0 * abs(pdi - mdi) / (pdi + mdi) if pdi + mdi else 0.0)
    if len(dx) < period:
        return None, None, None
    adx = sum(dx[:period]) / period
    for x in dx[period:]:
        adx = (adx * (period - 1) + x) / period
    pdi = 100.0 * p / atr if atr else 0.0
    mdi = 100.0 * m / atr if atr else 0.0
    return adx, pdi, mdi


def calculate_cci(candles: List[dict], period: int = 20) -> Optional[float]:
    if len(candles) < period:
        return None
    tp = [(c['h'] + c['l'] + c['c']) / 3.0 for c in candles[-period:]]
    mean = sum(tp) / period
    dev = sum(abs(x - mean) for x in tp) / period
    return 0.0 if dev == 0 else (tp[-1] - mean) / (0.015 * dev)


def calculate_stochastic(candles: List[dict], period: int = 14, smooth_k: int = 3, smooth_d: int = 3) -> Tuple[Optional[float], Optional[float]]:
    if len(candles) < period + smooth_k + smooth_d - 2:
        return None, None
    ks = []
    for i in range(period - 1, len(candles)):
        window = candles[i - period + 1:i + 1]
        hi = max(c['h'] for c in window)
        lo = min(c['l'] for c in window)
        ks.append(50.0 if hi == lo else 100.0 * (candles[i]['c'] - lo) / (hi - lo))
    smoothed = []
    for i in range(smooth_k - 1, len(ks)):
        smoothed.append(sum(ks[i - smooth_k + 1:i + 1]) / smooth_k)
    if len(smoothed) < smooth_d:
        return None, None
    d = sum(smoothed[-smooth_d:]) / smooth_d
    return smoothed[-1], d


def calculate_swing_low(candles: List[dict], lookback: int = 20) -> Optional[float]:
    if len(candles) < lookback:
        return None
    return min(c['l'] for c in candles[-lookback:])


def calculate_swing_high(candles: List[dict], lookback: int = 20) -> Optional[float]:
    if len(candles) < lookback:
        return None
    return max(c['h'] for c in candles[-lookback:])


def find_pivots(candles: List[dict], left: int = 2, right: int = 2):
    highs, lows = [], []
    for i in range(left, len(candles) - right):
        h = candles[i]['h']; l = candles[i]['l']
        if h >= max(c['h'] for c in candles[i-left:i+right+1]):
            highs.append((i, h))
        if l <= min(c['l'] for c in candles[i-left:i+right+1]):
            lows.append((i, l))
    return highs, lows
