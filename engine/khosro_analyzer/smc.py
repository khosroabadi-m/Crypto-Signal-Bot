"""SMC / confluence scoring components for Khosro Confluence Engine v11.

Every scorer returns (score, detail) where score is in [0, 1]:
  score >= 0.6  -> aligned with the trade direction
  score ~ 0.35-0.5 -> neutral / not aligned
  score <= 0.2  -> against the trade direction

All functions are pure-python (no numpy/pandas) and side-effect free so the
backtester can call them on any prefix of candles without look-ahead.
"""
from __future__ import annotations
from typing import List, Tuple, Optional

from indicators import (
    calculate_ema, calculate_rsi, calculate_macd, calculate_atr,
    ema_series, find_pivots,
)

NEUTRAL = 0.35


def _safe(fn, *args, **kwargs) -> Tuple[float, str]:
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # a broken component must never kill a scan
        return 0.5, f'error: {type(exc).__name__}'


# ----------------------------------------------------------------------------
# 1) Order flow — approximate aggressive buy/sell pressure from recent candles
# ----------------------------------------------------------------------------
def order_flow_score(candles: List[dict], direction: str, lookback: int = 12) -> Tuple[float, str]:
    if len(candles) < lookback:
        return 0.5, 'insufficient data'
    win = candles[-lookback:]
    buy = sum(c['v'] for c in win if c['c'] >= c['o'])
    sell = sum(c['v'] for c in win if c['c'] < c['o'])
    total = buy + sell
    delta = (buy - sell) / total if total else 0.0            # [-1, +1]
    close_pos = []
    for c in win:
        rng = max(c['h'] - c['l'], 1e-12)
        close_pos.append((c['c'] - c['l']) / rng)             # 0..1, where price closed in range
    avg_cp = sum(close_pos) / len(close_pos)
    # combine: volume delta (dominant) + where price closes inside its range
    flow = 0.5 + 0.30 * delta + 0.40 * (avg_cp - 0.5) * 2 * 0.5
    flow = min(1.0, max(0.0, flow))
    if direction == 'SHORT':
        flow = 1.0 - flow
    return flow, f'buyVol={buy:.0f} sellVol={sell:.0f} delta={delta:+.2f} closePos={avg_cp:.2f}'


# ----------------------------------------------------------------------------
# 2) Volume profile — POC / value area high-low, acceptance above/below value
# ----------------------------------------------------------------------------
def volume_profile_score(candles: List[dict], direction: str,
                         lookback: int = 120, bins: int = 24,
                         va_pct: float = 0.70) -> Tuple[float, str]:
    if len(candles) < 60:
        return 0.5, 'insufficient data'
    win = candles[-lookback:]
    lo = min(c['l'] for c in win)
    hi = max(c['h'] for c in win)
    if hi <= lo:
        return 0.5, 'flat range'
    step = (hi - lo) / bins
    vols = [0.0] * bins
    for c in win:
        tp = (c['h'] + c['l'] + c['c']) / 3.0
        idx = min(bins - 1, max(0, int((tp - lo) / step)))
        vols[idx] += c['v']
    poc_idx = max(range(bins), key=lambda i: vols[i])
    poc = lo + (poc_idx + 0.5) * step
    total = sum(vols) or 1.0
    acc = vols[poc_idx]
    li = ri = poc_idx
    while acc < va_pct * total and (li > 0 or ri < bins - 1):
        left = vols[li - 1] if li > 0 else -1.0
        right = vols[ri + 1] if ri < bins - 1 else -1.0
        if right >= left:
            ri += 1
            acc += max(right, 0.0)
        else:
            li -= 1
            acc += max(left, 0.0)
    val = lo + li * step
    vah = lo + (ri + 1) * step
    price = win[-1]['c']
    if direction == 'LONG':
        if price > vah:
            score = 0.85      # expanded above value: bullish but extended
        elif price > poc:
            score = 0.95      # upper value area: acceptance
        elif price >= val:
            score = 0.45      # inside value, below POC
        else:
            score = 0.15      # below value: weak
    else:
        if price < val:
            score = 0.85
        elif price < poc:
            score = 0.95
        elif price <= vah:
            score = 0.45
        else:
            score = 0.15
    return score, f'POC={poc:.6g} VA[{val:.6g}..{vah:.6g}] price={price:.6g}'


# ----------------------------------------------------------------------------
# 3) Sweep — wick beyond a prior pivot level with close back inside
# ----------------------------------------------------------------------------
def sweep_score(candles: List[dict], direction: str,
                piv_lookback: int = 90, recent: int = 8) -> Tuple[float, str]:
    if len(candles) < 30:
        return NEUTRAL, 'insufficient data'
    highs, lows = find_pivots(candles[-piv_lookback:])
    last = candles[-recent:]
    best = 0.0
    found = 'none'
    if direction == 'LONG' and len(lows) >= 1:
        levels = [lvl for _, lvl in lows[:-1]] or [lvl for _, lvl in lows]
        for lvl in levels:
            for j, c in enumerate(last):
                if c['l'] < lvl and c['c'] > lvl:
                    depth = (lvl - c['l']) / max(lvl, 1e-12)
                    recency = 1.0 - (len(last) - 1 - j) / (recent * 2.0)
                    s = min(1.0, 0.55 + min(depth * 40, 0.25) + recency * 0.20)
                    if s > best:
                        best = s
                        found = f'swept low {lvl:.6g} {len(last)-j} bars ago'
    if direction == 'SHORT' and len(highs) >= 1:
        levels = [lvl for _, lvl in highs[:-1]] or [lvl for _, lvl in highs]
        for lvl in levels:
            for j, c in enumerate(last):
                if c['h'] > lvl and c['c'] < lvl:
                    depth = (c['h'] - lvl) / max(lvl, 1e-12)
                    recency = 1.0 - (len(last) - 1 - j) / (recent * 2.0)
                    s = min(1.0, 0.55 + min(depth * 40, 0.25) + recency * 0.20)
                    if s > best:
                        best = s
                        found = f'swept high {lvl:.6g} {len(last)-j} bars ago'
    return (best if best else NEUTRAL), f'sweep={found}'


# ----------------------------------------------------------------------------
# 4) Liquidity — equal highs/lows pools relative to current price
# ----------------------------------------------------------------------------
def liquidity_score(candles: List[dict], direction: str,
                    piv_lookback: int = 90, tol: float = 0.0015,
                    atr_mult: float = 3.0) -> Tuple[float, str]:
    if len(candles) < 40:
        return 0.5, 'insufficient data'
    win = candles[-piv_lookback:]
    highs, lows = find_pivots(win)
    price = win[-1]['c']
    atr = calculate_atr(candles) or price * 0.01

    def pools(levels, side):
        """Group pivots into pools; side=-1 lows below, +1 highs above price."""
        out = []
        lv = sorted(lvl for _, lvl in levels)
        used = [False] * len(lv)
        for i, v in enumerate(lv):
            if used[i]:
                continue
            grp = [v]
            for k in range(i + 1, len(lv)):
                if not used[k] and abs(lv[k] - v) / v <= tol:
                    grp.append(lv[k])
                    used[k] = True
            if len(grp) >= 2:
                p = sum(grp) / len(grp)
                if (side < 0 and p < price) or (side > 0 and p > price):
                    out.append(p)
        return sorted(out)

    eq_lows = pools(lows, -1)
    eq_highs = pools(highs, +1)
    score = NEUTRAL
    bits = []
    if direction == 'LONG':
        fuel = [p for p in eq_lows if (price - p) / price <= atr_mult * atr / price]
        target = [p for p in eq_highs if (p - price) / price <= atr_mult * atr / price]
        if fuel:
            score += 0.35
            bits.append(f'eq-lows fuel @ {fuel[0]:.6g}')
        if target:
            score += 0.30
            bits.append(f'eq-highs magnet @ {target[0]:.6g}')
        if not fuel and not target:
            bits.append('no pools in range')
    else:
        fuel = [p for p in eq_highs if (p - price) / price <= atr_mult * atr / price]
        target = [p for p in eq_lows if (price - p) / price <= atr_mult * atr / price]
        if fuel:
            score += 0.35
            bits.append(f'eq-highs fuel @ {fuel[0]:.6g}')
        if target:
            score += 0.30
            bits.append(f'eq-lows magnet @ {target[0]:.6g}')
        if not fuel and not target:
            bits.append('no pools in range')
    score = min(1.0, score)
    return score, ', '.join(bits) if bits else 'no pools'


# ----------------------------------------------------------------------------
# 5) FVG — unmitigated fair value gaps near price
# ----------------------------------------------------------------------------
def fvg_score(candles: List[dict], direction: str,
              lookback: int = 15, max_atr_dist: float = 1.2) -> Tuple[float, str]:
    if len(candles) < 20:
        return NEUTRAL, 'insufficient data'
    atr = calculate_atr(candles)
    if not atr:
        return NEUTRAL, 'no ATR'
    scan = candles[-(lookback + 2):]
    price = candles[-1]['c']
    best = 0.0
    found = 'none'
    for i in range(2, len(scan)):
        c0, c2 = scan[i - 2], scan[i]
        # bullish gap: candle[i].low above candle[i-2].high
        if c2['l'] > c0['h']:
            gap_lo, gap_hi = c0['h'], c2['l']
            mitigated = any(scan[k]['l'] <= gap_lo for k in range(i + 1, len(scan)))
            if not mitigated and direction == 'LONG':
                mid = (gap_lo + gap_hi) / 2
                dist_atr = abs(price - mid) / atr
                if gap_lo <= price <= gap_hi:
                    s = 1.0
                elif dist_atr <= max_atr_dist:
                    s = 0.85 - 0.30 * (dist_atr / max_atr_dist)
                else:
                    s = 0.0
                if s > best:
                    best = s
                    found = f'bull FVG [{gap_lo:.6g}..{gap_hi:.6g}] {dist_atr:.1f} ATR'
        # bearish gap
        if c2['h'] < c0['l']:
            gap_lo, gap_hi = c2['h'], c0['l']
            mitigated = any(scan[k]['h'] >= gap_hi for k in range(i + 1, len(scan)))
            if not mitigated and direction == 'SHORT':
                mid = (gap_lo + gap_hi) / 2
                dist_atr = abs(price - mid) / atr
                if gap_lo <= price <= gap_hi:
                    s = 1.0
                elif dist_atr <= max_atr_dist:
                    s = 0.85 - 0.30 * (dist_atr / max_atr_dist)
                else:
                    s = 0.0
                if s > best:
                    best = s
                    found = f'bear FVG [{gap_lo:.6g}..{gap_hi:.6g}] {dist_atr:.1f} ATR'
    return (best if best else NEUTRAL), f'fvg={found}'


# ----------------------------------------------------------------------------
# 6) Fibonacci — retracement into the golden zone of the current leg
# ----------------------------------------------------------------------------
def fibonacci_score(candles: List[dict], direction: str,
                    lookback: int = 120) -> Tuple[float, str]:
    if len(candles) < 60:
        return NEUTRAL, 'insufficient data'
    win = candles[-lookback:]
    price = win[-1]['c']
    if direction == 'LONG':
        swing_low = min(c['l'] for c in win)
        hi_idx = max(range(len(win)), key=lambda i: win[i]['h'])
        if win[hi_idx]['t'] <= win[min(range(len(win)), key=lambda i: win[i]['l'])]['t']:
            return NEUTRAL, 'no up-leg'
        swing_high = win[hi_idx]['h']
        span = swing_high - swing_low
        if span <= 0:
            return NEUTRAL, 'flat leg'
        retr = (swing_high - price) / span           # 0 = at high, 1 = at low
        zones = [(0.0, 0.236, 0.40), (0.236, 0.382, 0.75), (0.382, 0.618, 1.0),
                 (0.618, 0.786, 0.65), (0.786, 2.0, 0.20)]
        score = 0.40
        for lo_z, hi_z, s in zones:
            if lo_z <= retr < hi_z:
                score = s
                break
        return score, f'retr={retr:.3f} of [{swing_low:.6g}..{swing_high:.6g}]'
    swing_high = max(c['h'] for c in win)
    lo_idx = min(range(len(win)), key=lambda i: win[i]['l'])
    if win[lo_idx]['t'] <= win[max(range(len(win)), key=lambda i: win[i]['h'])]['t']:
        return NEUTRAL, 'no down-leg'
    swing_low = win[lo_idx]['l']
    span = swing_high - swing_low
    if span <= 0:
        return NEUTRAL, 'flat leg'
    retr = (price - swing_low) / span               # 0 = at low, 1 = at high
    zones = [(0.0, 0.236, 0.40), (0.236, 0.382, 0.75), (0.382, 0.618, 1.0),
             (0.618, 0.786, 0.65), (0.786, 2.0, 0.20)]
    score = 0.40
    for lo_z, hi_z, s in zones:
        if lo_z <= retr < hi_z:
            score = s
            break
    return score, f'retr={retr:.3f} of [{swing_low:.6g}..{swing_high:.6g}]'


# ----------------------------------------------------------------------------
# 7) Supply / demand — impulse zones from strong body + volume candles
# ----------------------------------------------------------------------------
def supply_demand_score(candles: List[dict], direction: str,
                        lookback: int = 100,
                        body_mult: float = 1.5,
                        vol_mult: float = 1.25,
                        max_atr_dist: float = 2.0) -> Tuple[float, str]:
    if len(candles) < 60:
        return 0.5, 'insufficient data'
    win = candles[-lookback:]
    atr = calculate_atr(candles)
    if not atr:
        return 0.5, 'no ATR'
    bodies = [abs(c['c'] - c['o']) for c in win]
    avg_body = sum(bodies) / len(bodies) or 1e-12
    avg_vol = sum(c['v'] for c in win) / len(win) or 1e-12
    price = win[-1]['c']
    zones = []
    for i, c in enumerate(win[:-2]):        # skip last two candles (zone not mature)
        body = abs(c['c'] - c['o'])
        if body >= body_mult * avg_body and c['v'] >= vol_mult * avg_vol:
            lo_z = min(c['o'], c['c'])
            hi_z = max(c['o'], c['c'])
            zones.append((lo_z, hi_z, 'demand' if c['c'] > c['o'] else 'supply'))
    best = 0.0
    found = 'none'
    for lo_z, hi_z, kind in zones:
        mid = (lo_z + hi_z) / 2
        if direction == 'LONG' and kind == 'demand' and lo_z <= price:
            dist_atr = (price - mid) / atr
            if dist_atr <= max_atr_dist:
                s = 1.0 if lo_z <= price <= hi_z else max(0.4, 0.95 - 0.28 * dist_atr)
                if s > best:
                    best = s
                    found = f'demand [{lo_z:.6g}..{hi_z:.6g}] {dist_atr:.1f} ATR below'
        if direction == 'SHORT' and kind == 'supply' and hi_z >= price:
            dist_atr = (mid - price) / atr
            if dist_atr <= max_atr_dist:
                s = 1.0 if lo_z <= price <= hi_z else max(0.4, 0.95 - 0.28 * dist_atr)
                if s > best:
                    best = s
                    found = f'supply [{lo_z:.6g}..{hi_z:.6g}] {dist_atr:.1f} ATR above'
    return (best if best else NEUTRAL), f'zone={found}'


# ----------------------------------------------------------------------------
# 8) Moving average — EMA stack, price position and slope
# ----------------------------------------------------------------------------
def moving_average_score(candles: List[dict], direction: str) -> Tuple[float, str]:
    closes = [c['c'] for c in candles]
    if len(closes) < 60:
        return 0.5, 'insufficient data'
    e21_s = ema_series(closes, 21)
    e50_s = ema_series(closes, 50)
    e21, e50 = e21_s[-1], e50_s[-1]
    price = closes[-1]
    if e21 is None or e50 is None:
        return 0.5, 'EMA unavailable'
    score = 0.0
    if direction == 'LONG':
        score += 0.45 if e21 > e50 else 0.0
        score += 0.30 if price > e21 else 0.0
        prev = next((x for x in reversed(e21_s[:-1]) if x is not None), None)
        idx_prev = len(e21_s) - 7
        slope = e21 - e21_s[idx_prev] if idx_prev >= 0 and e21_s[idx_prev] is not None else 0.0
        score += 0.25 if slope > 0 else 0.0
    else:
        score += 0.45 if e21 < e50 else 0.0
        score += 0.30 if price < e21 else 0.0
        idx_prev = len(e21_s) - 7
        slope = e21 - e21_s[idx_prev] if idx_prev >= 0 and e21_s[idx_prev] is not None else 0.0
        score += 0.25 if slope < 0 else 0.0
    return score, f'price={price:.6g} EMA21={e21:.6g} EMA50={e50:.6g} slope={slope:+.3g}'


# ----------------------------------------------------------------------------
# 9) MACD — standalone alignment
# ----------------------------------------------------------------------------
def macd_score(candles: List[dict], direction: str) -> Tuple[float, str]:
    closes = [c['c'] for c in candles]
    if len(closes) < 40:
        return 0.5, 'insufficient data'
    m = calculate_macd(closes)
    hist, line, sig = m.get('histogram'), m.get('macd'), m.get('signal')
    if hist is None:
        return 0.5, 'MACD unavailable'
    score = 0.0
    aligned = hist > 0 if direction == 'LONG' else hist < 0
    score += 0.40 if aligned else 0.0
    # histogram expanding in trade direction
    if len(closes) >= 60:
        prev = calculate_macd(closes[:-3]).get('histogram')
        if prev is not None:
            growing = hist > prev if direction == 'LONG' else hist < prev
            score += 0.30 if growing else 0.0
    if line is not None and sig is not None:
        cross_ok = line > sig if direction == 'LONG' else line < sig
        score += 0.30 if cross_ok else 0.0
    return score, f'MACD={line:.6g} sig={sig:.6g} hist={hist:.6g}'


# ----------------------------------------------------------------------------
# 10) RSI — standalone zone scoring
# ----------------------------------------------------------------------------
def rsi_score(candles: List[dict], direction: str) -> Tuple[float, str]:
    closes = [c['c'] for c in candles]
    rsi = calculate_rsi(closes)
    if rsi is None:
        return 0.5, 'RSI unavailable'
    if direction == 'LONG':
        score = 0.55 if 50 <= rsi <= 68 else 0.35 if 45 <= rsi < 50 else 0.15 if rsi < 45 else 0.0
    else:
        score = 0.55 if 32 <= rsi <= 50 else 0.35 if 50 < rsi <= 55 else 0.15 if rsi > 55 else 0.0
    return score, f'RSI={rsi:.1f}'


ALL_COMPONENTS = (
    'order_flow', 'volume_profile', 'sweep', 'liquidity', 'fvg',
    'fibonacci', 'supply_demand', 'moving_average', 'macd', 'rsi',
)


def rule_components(candles: List[dict], direction: str) -> List[Tuple[str, float, str]]:
    """Compute all 10 rule-based components for one direction."""
    out = []
    out.append(('order_flow', *_safe(order_flow_score, candles, direction)))
    out.append(('volume_profile', *_safe(volume_profile_score, candles, direction)))
    out.append(('sweep', *_safe(sweep_score, candles, direction)))
    out.append(('liquidity', *_safe(liquidity_score, candles, direction)))
    out.append(('fvg', *_safe(fvg_score, candles, direction)))
    out.append(('fibonacci', *_safe(fibonacci_score, candles, direction)))
    out.append(('supply_demand', *_safe(supply_demand_score, candles, direction)))
    out.append(('moving_average', *_safe(moving_average_score, candles, direction)))
    out.append(('macd', *_safe(macd_score, candles, direction)))
    out.append(('rsi', *_safe(rsi_score, candles, direction)))
    return out
