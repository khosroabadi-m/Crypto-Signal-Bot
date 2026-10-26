"""Signals package — rule-based engine + contract for future rule books."""

from .base import Signal, SignalEngine, UnimplementedSignalEngine
from .engine import RuleSignalEngine

__all__ = [
    "Signal",
    "SignalEngine",
    "UnimplementedSignalEngine",
    "RuleSignalEngine",
]
