"""v31_cooldown.py — Independent per-symbol::scenario cooldown for V3.1.

There must be NO global cooldown across symbols or across scenarios.
Each symbol/scenario pair has its own state.
"""


def cooldown_passed(last_entry_ts, current_ts, cooldown_hours):
    """Check if cooldown has passed."""
    if last_entry_ts is None:
        return True
    elapsed = current_ts - last_entry_ts
    return elapsed >= cooldown_hours * 3600


def independent_key(symbol, scenario):
    """Every symbol/scenario pair has its own state.
    Example: BTCUSDT::S1 and BTCUSDT::S2 are completely independent.
    """
    return f"{symbol}::{scenario}"
