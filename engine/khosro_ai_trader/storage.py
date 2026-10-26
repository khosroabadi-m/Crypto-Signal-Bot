"""Persistence: latest snapshot JSON + daily history with retention."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .logger import get_logger
from .models import TrendingSnapshot

log = get_logger("storage")


def save_latest(snapshot: TrendingSnapshot, latest_path: str, root: Path) -> Path:
    path = (root / latest_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(snapshot.to_dict(), fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    log.info("latest snapshot written: %s", path)
    return path


def save_ai_analysis(analysis, ai_path: str, root: Path) -> Path:
    """Persist the AI aggregation-layer output for the run."""
    path = (root / ai_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(analysis.to_dict(), fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    log.info("ai analysis written: %s", path)
    return path


def save_signals(signals: list, signals_path: str, root: Path) -> Path:
    """Persist the approved signals of this run for diffable history."""
    from .signals.base import Signal  # local import: no cycle

    path = (root / signals_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = []
    for s in signals:
        assert isinstance(s, Signal)
        payload.append({
            "signal_id": s.signal_id,
            "symbol": s.symbol,
            "pair": s.pair,
            "direction": s.direction,
            "entry": s.entry,
            "stop_loss": s.stop_loss,
            "take_profits": s.take_profits,
            "rr": s.rr,
            "confidence": s.confidence,
            "risk_usd": s.position_size_usd,
            "notional_usd": s.notional_usd,
            "reasons": s.reasons,
            "meta": s.meta,
        })
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    log.info("signals written: %s (%d)", path, len(payload))
    return path


def append_history(
    snapshot: TrendingSnapshot, history_dir: str, history_days: int, root: Path
) -> Path:
    """Append the run into a daily JSON array file: trending_YYYY-MM-DD.json."""
    dir_path = (root / history_dir).resolve()
    dir_path.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = dir_path / f"trending_{day}.json"

    runs: list[dict] = []
    if path.exists():
        try:
            runs = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(runs, list):
                runs = []
        except (json.JSONDecodeError, OSError):
            log.warning("history file %s unreadable — recreating", path)
            runs = []

    runs.append(snapshot.to_dict())
    path.write_text(
        json.dumps(runs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    log.info("history appended: %s (run #%d today)", path.name, len(runs))

    _purge_old(dir_path, history_days)
    return path


def _purge_old(dir_path: Path, history_days: int) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=history_days)
    for f in dir_path.glob("trending_*.json"):
        try:
            file_day = datetime.strptime(
                f.stem.replace("trending_", ""), "%Y-%m-%d"
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if file_day < cutoff:
            f.unlink(missing_ok=True)
            log.info("history purged (>%dd old): %s", history_days, f.name)


def latest_changed(current: dict, latest_path: str, root: Path) -> bool:
    """True if the new snapshot differs from the committed latest file."""
    path = (root / latest_path).resolve()
    if not path.exists():
        return True
    try:
        prev = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True
    prev_coins = [(c.get("symbol"), c.get("score")) for c in prev.get("coins", [])]
    new_coins = [(c.get("symbol"), c.get("score")) for c in current.get("coins", [])]
    return prev_coins != new_coins
