from __future__ import annotations
from typing import Optional


def candle_pattern(candles: list) -> Optional[str]:
    if len(candles) < 2:
        return None
    a, b = candles[-2], candles[-1]
    body = abs(b['c'] - b['o'])
    rng = max(b['h'] - b['l'], 1e-12)
    upper = b['h'] - max(b['o'], b['c'])
    lower = min(b['o'], b['c']) - b['l']
    if body / rng < 0.1:
        return 'DOJI'
    if lower >= body * 2 and upper <= body and b['c'] > b['o']:
        return 'HAMMER'
    if upper >= body * 2 and lower <= body and b['c'] < b['o']:
        return 'SHOOTING_STAR'
    if a['c'] < a['o'] and b['c'] > b['o'] and b['o'] <= a['c'] and b['c'] >= a['o']:
        return 'BULLISH_ENGULFING'
    if a['c'] > a['o'] and b['c'] < b['o'] and b['o'] >= a['c'] and b['c'] <= a['o']:
        return 'BEARISH_ENGULFING'
    return None


def ema_rejection(candles: list, ema_value: float, direction: str, tolerance: float = 0.003) -> bool:
    if len(candles) < 2 or ema_value is None:
        return False
    c = candles[-1]
    touched = c['l'] <= ema_value * (1 + tolerance) and c['h'] >= ema_value * (1 - tolerance)
    if direction == 'LONG':
        return touched and c['c'] > ema_value and c['c'] > c['o']
    return touched and c['c'] < ema_value and c['c'] < c['o']


def breakout_or_retest(candles: list, level: float, direction: str, tolerance: float = 0.0025) -> str:
    if len(candles) < 2 or level is None:
        return 'NONE'
    prev, cur = candles[-2], candles[-1]
    if direction == 'LONG':
        if prev['c'] <= level and cur['c'] > level and cur['l'] <= level * (1 + tolerance):
            return 'BREAKOUT'
        if cur['l'] <= level * (1 + tolerance) and cur['c'] > level:
            return 'RETEST'
    else:
        if prev['c'] >= level and cur['c'] < level and cur['h'] >= level * (1 - tolerance):
            return 'BREAKOUT'
        if cur['h'] >= level * (1 - tolerance) and cur['c'] < level:
            return 'RETEST'
    return 'NONE'
