"""indicators.py — Technical indicator calculations for Titan Signal.

All indicators work with candle dictionaries: {t, o, c, h, v, l}.
EMA uses O(n) single-pass precomputation via ema_series().
"""

import math


# ===== EMA (O(n) single-pass) =====
def ema_series(prices: list, period: int) -> list:
    """Compute EMA for entire price series in O(n). Returns list with None padding."""
    if len(prices) < period:
        return [None] * len(prices)
    k = 2.0 / (period + 1)
    ema_vals = [sum(prices[:period]) / period]
    for price in prices[period:]:
        ema_vals.append(price * k + ema_vals[-1] * (1 - k))
    return [None] * (period - 1) + ema_vals


def calculate_ema(prices: list, period: int):
    """Return the last EMA value for a price series."""
    series = ema_series(prices, period)
    return series[-1] if series else None


# ===== RSI =====
def calculate_rsi(prices: list, period: int = 14):
    """Relative Strength Index using Wilder's smoothing."""
    if len(prices) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(prices)):
        change = prices[i] - prices[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    if avg_loss == 0:
        return 100.0
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


# ===== MACD =====
def calculate_macd(prices: list, fast: int = 12, slow: int = 26, signal_period: int = 9) -> dict:
    """MACD line, signal line, and histogram."""
    if len(prices) < slow + signal_period:
        return {'macd': None, 'signal': None, 'histogram': None}
    ema_fast = ema_series(prices, fast)
    ema_slow = ema_series(prices, slow)
    macd_line = [
        (f - s) if f is not None and s is not None else None
        for f, s in zip(ema_fast, ema_slow)
    ]
    valid_macd = [m for m in macd_line if m is not None]
    if len(valid_macd) < signal_period:
        return {'macd': macd_line[-1], 'signal': None, 'histogram': None}
    signal_full = ema_series(valid_macd, signal_period)
    signal_series = [None] * (len(macd_line) - len(signal_full)) + signal_full
    histogram = [
        m - s if m and s else None
        for m, s in zip(macd_line, signal_series)
    ]
    return {'macd': macd_line[-1], 'signal': signal_series[-1], 'histogram': histogram[-1]}


# ===== ATR =====
def calculate_atr(candles: list, period: int = 14):
    """Average True Range."""
    if len(candles) < period + 1:
        return None
    tr_list = []
    for i in range(1, len(candles)):
        high, low, prev_close = candles[i]['h'], candles[i]['l'], candles[i-1]['c']
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_list.append(tr)
    if len(tr_list) < period:
        return None
    atr = sum(tr_list[:period]) / period
    for tr in tr_list[period:]:
        atr = (atr * (period - 1) + tr) / period
    return round(atr, 6)


# ===== Body Strength =====
def body_strength(candle: dict) -> float:
    """Ratio of candle body to total range."""
    body = abs(candle['c'] - candle['o'])
    total_range = candle['h'] - candle['l']
    return body / total_range if total_range > 0 else 0.0


# ===== ADX (Wilder-smoothed) =====
def calculate_adx(candles: list, period: int = 14):
    """Average Directional Index with proper Wilder smoothing.
    Returns (ADX, DI+, DI-). Requires at least 2*period candles."""
    if len(candles) < period * 2:
        return None, None, None
    # Build raw +DM, -DM, TR series
    plus_dm_list, minus_dm_list, tr_list = [], [], []
    for i in range(1, len(candles)):
        high = candles[i]['h']
        low = candles[i]['l']
        prev_high = candles[i - 1]['h']
        prev_low = candles[i - 1]['l']
        prev_close = candles[i - 1]['c']
        up = high - prev_high
        down = prev_low - low
        plus_dm_list.append(up if up > down and up > 0 else 0)
        minus_dm_list.append(down if down > up and down > 0 else 0)
        tr_list.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    n = len(tr_list)
    if n < period:
        return None, None, None
    # Wilder-smoothed ATR, +DI, -DI
    atr_s = sum(tr_list[:period])
    pdi_s = sum(plus_dm_list[:period])
    mdi_s = sum(minus_dm_list[:period])
    dx_list = []
    for i in range(period, n):
        atr_s = atr_s - atr_s / period + tr_list[i]
        pdi_s = pdi_s - pdi_s / period + plus_dm_list[i]
        mdi_s = mdi_s - mdi_s / period + minus_dm_list[i]
        pdi_val = 100 * (pdi_s / atr_s) if atr_s else 0
        mdi_val = 100 * (mdi_s / atr_s) if atr_s else 0
        dx_sum = pdi_val + mdi_val
        dx_val = abs(pdi_val - mdi_val) / dx_sum * 100 if dx_sum else 0
        dx_list.append(dx_val)
    if not dx_list:
        pdi_val = 100 * (pdi_s / atr_s) if atr_s else 0
        mdi_val = 100 * (mdi_s / atr_s) if atr_s else 0
        dx_sum = pdi_val + mdi_val
        dx_val = abs(pdi_val - mdi_val) / dx_sum * 100 if dx_sum else 0
        return round(dx_val, 2), round(pdi_val, 2), round(mdi_val, 2)
    # ADX = Wilder smoothing of DX (average-based)
    adx_s = sum(dx_list[:period]) / period
    for dx_val in dx_list[period:]:
        adx_s = (adx_s * (period - 1) + dx_val) / period
    # Last DI values
    last_pdi = 100 * (pdi_s / atr_s) if atr_s else 0
    last_mdi = 100 * (mdi_s / atr_s) if atr_s else 0
    return round(adx_s, 2), round(last_pdi, 2), round(last_mdi, 2)


def calculate_swing_low(candles: list, lookback: int = 10):
    """Lowest low in the last N candles."""
    if len(candles) < lookback:
        return None
    return min(c['l'] for c in candles[-lookback:])


def calculate_swing_high(candles: list, lookback: int = 10):
    """Highest high in the last N candles."""
    if len(candles) < lookback:
        return None
    return max(c['h'] for c in candles[-lookback:])


# ===== CCI =====
def calculate_cci(candles: list, period: int = 20):
    """Commodity Channel Index."""
    if len(candles) < period:
        return None
    tp = [(c['h'] + c['l'] + c['c']) / 3 for c in candles]
    sma = sum(tp[-period:]) / period
    mean_dev = sum(abs(x - sma) for x in tp[-period:]) / period
    if mean_dev == 0:
        return 0
    return round((tp[-1] - sma) / (0.015 * mean_dev), 2)


# ===== Parabolic SAR (simplified) =====
def calculate_sar(candles: list, step: float = 0.02, max_step: float = 0.2):
    """Simplified Parabolic SAR — last value only."""
    if len(candles) < 2:
        return None
    prev_high, prev_low = candles[-2]['h'], candles[-2]['l']
    curr_high, curr_low = candles[-1]['h'], candles[-1]['l']
    ep = curr_high if curr_high > prev_high else curr_low
    sar = prev_low + step * (ep - prev_low)
    return round(sar, 4)


# ===== Stochastic Oscillator =====
def calculate_stochastic(candles: list, period: int = 14, smooth_k: int = 3, smooth_d: int = 3):
    """Stochastic Oscillator. Returns (%K, %D)."""
    if len(candles) < period:
        return None, None
    closes = [c['c'] for c in candles]
    highs = [c['h'] for c in candles]
    lows = [c['l'] for c in candles]
    highest_high = max(highs[-period:])
    lowest_low = min(lows[-period:])
    k = 100 * (closes[-1] - lowest_low) / (highest_high - lowest_low) if highest_high != lowest_low else 0
    d = k  # simplified with single value
    return round(k, 2), round(d, 2)
