"""v31_filters.py — V3.1 optional filter functions for Titan Signal.

These filters are applied AFTER V1 signal/scoring passes.
They must NEVER replace V1's original scoring.

In the live system, ONLY the RSI zone filter is used, and ONLY on S1.
"""


def rsi_zone(direction, rsi, long_zone, short_zone):
    """RSI entry zone filter.
    LONG: rsi must be in long_zone (e.g. 40-65)
    SHORT: rsi must be in short_zone (e.g. 35-55)
    """
    if rsi is None:
        return False
    if direction == "LONG":
        low, high = long_zone
    elif direction == "SHORT":
        low, high = short_zone
    else:
        return False
    return low <= rsi <= high
