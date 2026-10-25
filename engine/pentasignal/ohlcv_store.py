# ohlcv_store.py — دیتاست 1m غلتان 90 روزه برای بک‌تست
# فایل‌های این بخش دیتابیس سیگنال نیستند؛ داده خام بازار در data/ohlcv نگهداری می‌شود.

import gzip
import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import settings

TEHRAN = ZoneInfo("Asia/Tehran")
ROOT = os.path.join(settings.DATA_DIR, "ohlcv")


def _day_path(symbol: str, date_str: str) -> str:
    safe = symbol.replace("/", "-")
    d = os.path.join(ROOT, safe)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{date_str}.jsonl.gz")


def _date_from_ts(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), TEHRAN).strftime("%Y-%m-%d")


def append_candles(symbol: str, candles: list[dict], closed_before_ts: int | None = None) -> int:
    """ذخیره کندل‌های 1m بسته‌شده؛ تکراری‌ها حذف می‌شوند."""
    if not candles:
        return 0
    grouped = {}
    for c in candles:
        try:
            ts = int(c["t"])
            # کندل جاری/باز هرگز ذخیره نمی‌شود.
            if closed_before_ts is not None and ts >= int(closed_before_ts):
                continue
            grouped.setdefault(_date_from_ts(ts), {})[ts] = {
                "t": ts, "o": float(c["o"]), "h": float(c["h"]),
                "l": float(c["l"]), "c": float(c["c"]), "v": float(c["v"]),
            }
        except Exception:
            continue

    added = 0
    manifest = {}
    for date_str, incoming in grouped.items():
        path = _day_path(symbol, date_str)
        existing = {}
        if os.path.isfile(path):
            try:
                with gzip.open(path, "rt", encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            x = json.loads(line)
                            existing[int(x["t"])] = x
            except Exception:
                existing = {}
        before = len(existing)
        existing.update(incoming)
        added += max(0, len(existing) - before)
        tmp = path + ".tmp"
        with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as f:
            for ts in sorted(existing):
                f.write(json.dumps(existing[ts], separators=(",", ":"), ensure_ascii=False) + "\n")
        os.replace(tmp, path)
        manifest[date_str] = len(existing)

    # ایندکس سبک برای کنترل سلامت دیتاست؛ داده خام در فایل‌های روزانه باقی می‌ماند.
    if manifest:
        idx_path = os.path.join(ROOT, "manifest.json")
        idx = {}
        try:
            with open(idx_path, encoding="utf-8") as f:
                idx = json.load(f)
        except Exception:
            pass
        sym = symbol.replace("/", "-")
        idx[sym] = {"updated_at": datetime.now(TEHRAN).strftime("%Y-%m-%d %H:%M:%S"),
                    "days": sorted(manifest), "bars_last_written": sum(manifest.values())}
        tmp = idx_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False, indent=2)
        os.replace(tmp, idx_path)
    return added


def load(symbol: str, start_ts: int | None = None, end_ts: int | None = None) -> list[dict]:
    """بارگذاری داده 1m یک نماد از آرشیو محلی."""
    symdir = os.path.join(ROOT, symbol.replace("/", "-"))
    if not os.path.isdir(symdir):
        return []
    out = []
    for name in sorted(os.listdir(symdir)):
        if not name.endswith(".jsonl.gz"):
            continue
        try:
            date_str = name[:-9]
            day = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=TEHRAN)
            day_ts = int(day.timestamp())
            if end_ts is not None and day_ts > int(end_ts):
                continue
            if start_ts is not None and day_ts + 86400 < int(start_ts):
                continue
            with gzip.open(os.path.join(symdir, name), "rt", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    c = json.loads(line)
                    ts = int(c["t"])
                    if start_ts is not None and ts < int(start_ts):
                        continue
                    if end_ts is not None and ts >= int(end_ts):
                        continue
                    out.append(c)
        except Exception:
            continue
    out.sort(key=lambda x: x["t"])
    return out


def prune(keep_days: int | None = None) -> int:
    keep_days = keep_days or settings.OHLCV_RETENTION_DAYS
    cutoff = int((datetime.now(TEHRAN) - timedelta(days=keep_days)).timestamp())
    removed = 0
    if not os.path.isdir(ROOT):
        return 0
    for sym in os.listdir(ROOT):
        symdir = os.path.join(ROOT, sym)
        if not os.path.isdir(symdir):
            continue
        for name in os.listdir(symdir):
            if not name.endswith(".jsonl.gz"):
                continue
            try:
                date_str = name[:-9]
                day_ts = int(datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=TEHRAN).timestamp())
                if day_ts + 86400 <= cutoff:
                    os.remove(os.path.join(symdir, name))
                    removed += 1
            except Exception:
                pass
    return removed
