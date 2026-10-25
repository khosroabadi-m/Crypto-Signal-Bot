"""patterns.py — Price pattern detection for Titan Signal."""


def ema_rejection(prices_series: list, ema21: float) -> bool:
    """Check if price recently rejected EMA21."""
    if not prices_series or ema21 is None or len(prices_series) < 5:
        return False
    recent = prices_series[-5:]
    for i in range(1, len(recent)):
        if ema21 is not None:
            if recent[i-1] < ema21 <= recent[i]:
                return True
            if recent[i-1] > ema21 >= recent[i]:
                return True
    return False


def resistance_test(prices_series: list, ema50: float) -> bool:
    """Check if price recently tested EMA50 as support/resistance."""
    if not prices_series or ema50 is None or len(prices_series) < 10:
        return False
    recent = prices_series[-10:]
    touches = 0
    for p in recent:
        if abs(p - ema50) / ema50 < 0.002:
            touches += 1
    return touches >= 2


def pullback(prices_series: list, direction: str) -> bool:
    """Detect pullback in trend."""
    if len(prices_series) < 10:
        return False
    recent = prices_series[-10:]
    if direction == "LONG":
        # Price dipped then recovered
        min_idx = recent.index(min(recent))
        return min_idx < len(recent) - 1 and recent[-1] > recent[min_idx]
    else:
        max_idx = recent.index(max(recent))
        return max_idx < len(recent) - 1 and recent[-1] < recent[max_idx]


def double_top_bottom(prices_series: list) -> str:
    """Detect double top or double bottom pattern."""
    if len(prices_series) < 20:
        return None
    recent = prices_series[-20:]
    # Simplified: check if two similar highs/lows exist
    highs = [max(recent[i:i+3]) for i in range(0, len(recent)-2, 3)]
    lows = [min(recent[i:i+3]) for i in range(0, len(recent)-2, 3)]
    if len(highs) >= 2 and len(lows) >= 2:
        if abs(highs[-1] - highs[-2]) / highs[-2] < 0.003:
            return "double_top"
        if abs(lows[-1] - lows[-2]) / lows[-2] < 0.003:
            return "double_bottom"
    return None
