# store.py — ذخیره‌سازی روزانه سیگنال‌ها (یکپارچه)
#
# ساختار:
#   data/signals/YYYY-MM-DD.csv
#
# هر ردیف = یک سیگنال کامل از صدور تا نتیجه:
#   ورود، حد ضرر، وضعیت، زمان/قیمت خروج، سود، کارمزد، BE، پیام تلگرام و ...
#
# بعد از نیمه‌شب:
#   سیگنال OPEN در فایل روز صدور می‌ماند
#   open_signals() فایل‌های اخیر را اسکن می‌کند
#   update_signal() همان فایل روز صدور را آپدیت می‌کند

import csv
import os
import re
import threading
from datetime import timedelta
from glob import glob

from . import settings
from .utils import tehran_now, parse_tehran

_lock = threading.Lock()

SIGNAL_HEADERS = [
    "signal_id", "issued_at_tehran", "candle_close_tehran", "symbol", "direction",
    "scenario_id", "entry_price", "stop_loss", "take_profit", "exit_mode",
    "exit_param", "sl_atr_mult", "cm_candles", "atr", "entry_candle_ts", "position_size_usd",
    "risk_pct", "reason", "status", "exit_price", "exit_time_tehran", "exit_reason",
    "pnl_usd", "return_pct", "fee_usd", "r_multiple", "be_armed", "be_price",
    "be_armed_at_tehran", "telegram_message_id", "settle_message_id",
    "last_check_ts", "notes",
]

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _signals_dir() -> str:
    d = settings.SIGNALS_DIR
    os.makedirs(d, exist_ok=True)
    return d


def _date_from_ts(ts_tehran: str) -> str:
    if not ts_tehran:
        return tehran_now().strftime("%Y-%m-%d")
    return ts_tehran[:10]


def _signals_path(date_str: str) -> str:
    return os.path.join(_signals_dir(), f"{date_str}.csv")


def _ensure(path, headers):
    if not os.path.isfile(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(headers)


def init():
    _signals_dir()
    os.makedirs(settings.ARCHIVE_DIR, exist_ok=True)


def _list_signal_files(max_days: int | None = None) -> list:
    files = sorted(glob(os.path.join(_signals_dir(), "*.csv")), reverse=True)
    out = []
    for p in files:
        name = os.path.basename(p)[:-4]
        if not _DATE_RE.match(name):
            continue
        out.append(p)
        if max_days is not None and len(out) >= max_days:
            break
    return out


def read_all(path, headers):
    if not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write_all(path, headers, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({h: r.get(h, "") for h in headers})


def append_signal(row: dict) -> str:
    """صدور سیگنال جدید در فایل روز صدور."""
    date_str = _date_from_ts(row.get("issued_at_tehran", ""))
    path = _signals_path(date_str)
    with _lock:
        _ensure(path, SIGNAL_HEADERS)
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=SIGNAL_HEADERS, extrasaction="ignore")
            w.writerow({h: row.get(h, "") for h in SIGNAL_HEADERS})
    return path


def append_event(ts_tehran: str, signal_id: str, event: str, detail: str = "",
                 message_id: str = "", reply_to: str = ""):
    """
    سازگاری با کد قدیمی — رویداد روی همان ردیف سیگنال ثبت می‌شود (notes).
    دیگر فایل events جدا ساخته نمی‌شود.
    """
    note = f"{ts_tehran}|{event}|{detail}".strip("|")
    with _lock:
        path, rows, idx = _find_signal_file(signal_id)
        if path is None or rows is None or idx is None:
            return
        prev = (rows[idx].get("notes") or "").strip()
        rows[idx]["notes"] = f"{prev} || {note}".strip(" |") if prev else note
        if event == "BE_ARMED" and not rows[idx].get("be_armed_at_tehran"):
            rows[idx]["be_armed_at_tehran"] = ts_tehran
        _write_all(path, SIGNAL_HEADERS, rows)


def _find_signal_file(signal_id: str):
    m = re.search(r"(\d{8})-\d{4}$", signal_id or "")
    candidates = []
    if m:
        d = m.group(1)
        date_guess = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        candidates.append(_signals_path(date_guess))
    for p in _list_signal_files():
        if p not in candidates:
            candidates.append(p)
    for path in candidates:
        if not os.path.isfile(path):
            continue
        rows = read_all(path, SIGNAL_HEADERS)
        for i, r in enumerate(rows):
            if r.get("signal_id") == signal_id:
                return path, rows, i
    return None, None, None


def update_signal(signal_id: str, **fields):
    """به‌روزرسانی نتیجه/وضعیت روی همان ردیف سیگنال (حتی بعد از نیمه‌شب)."""
    with _lock:
        path, rows, idx = _find_signal_file(signal_id)
        if path is None or rows is None or idx is None:
            return
        for k, v in fields.items():
            rows[idx][k] = str(v)
        _write_all(path, SIGNAL_HEADERS, rows)


def get_signal(signal_id: str):
    path, rows, idx = _find_signal_file(signal_id)
    if path is None or rows is None or idx is None:
        return None
    return rows[idx]


def open_signals():
    """سیگنال‌های OPEN از فایل‌های اخیر — بعد از نیمه‌شب گم نمی‌شوند."""
    out = []
    for path in _list_signal_files(max_days=settings.CSV_KEEP_DAYS):
        for r in read_all(path, SIGNAL_HEADERS):
            if (r.get("status") or "OPEN") == "OPEN":
                out.append(r)
    return out


def all_signals():
    out = []
    for path in _list_signal_files(max_days=settings.CSV_KEEP_DAYS):
        out.extend(read_all(path, SIGNAL_HEADERS))
    return out


def signals_for_date(date_str: str):
    return read_all(_signals_path(date_str), SIGNAL_HEADERS)


def realized_r_on(date_str: str) -> float:
    """جمع r_multiple سیگنال‌های بسته‌شده در تاریخ تهران داده‌شده (گارد W2).

    سیگنال‌های باز بعد از نیمه‌شب در فایل «روز صدور» می‌مانند اما تاریخ
    بسته‌شدنشان (exit_time_tehran) ممکن است روز بعد باشد — پس روی همه
    فایل‌های اخیر اسکن می‌کنیم و بر اساس تاریخ خروج فیلتر می‌کنیم.
    ردیف‌های OPEN و r_multiple خراب نادیده گرفته می‌شوند.
    """
    total = 0.0
    for path in _list_signal_files(max_days=settings.CSV_KEEP_DAYS):
        for r in read_all(path, SIGNAL_HEADERS):
            status = r.get("status") or "OPEN"
            if status == "OPEN":
                continue
            exit_date = (r.get("exit_time_tehran") or "")[:10]
            if exit_date != date_str:
                continue
            try:
                total += float(r.get("r_multiple") or 0.0)
            except (TypeError, ValueError):
                pass
    return total


def has_open(symbol: str, scenario_id: str) -> bool:
    for r in open_signals():
        if r.get("symbol") == symbol and r.get("scenario_id") == scenario_id:
            return True
    return False


def last_signal_time(symbol: str, scenario_id: str):
    best = None
    for r in all_signals():
        if r.get("symbol") == symbol and r.get("scenario_id") == scenario_id:
            t = parse_tehran(r.get("issued_at_tehran", ""))
            if t and (best is None or t > best):
                best = t
    return best


def rotate_90d(keep_days=None) -> dict:
    """فایل‌های روزانه قدیمی‌تر از keep_days را به archive/signals منتقل می‌کند."""
    keep_days = keep_days or settings.CSV_KEEP_DAYS
    cutoff = (tehran_now() - timedelta(days=keep_days)).strftime("%Y-%m-%d")
    moved = {"signals": 0}

    with _lock:
        arch_dir = os.path.join(settings.ARCHIVE_DIR, "signals")
        os.makedirs(arch_dir, exist_ok=True)
        for path in glob(os.path.join(_signals_dir(), "*.csv")):
            name = os.path.basename(path)[:-4]
            if not _DATE_RE.match(name):
                continue
            if name < cutoff:
                dest = os.path.join(arch_dir, os.path.basename(path))
                if os.path.isfile(dest):
                    os.remove(path)
                else:
                    os.rename(path, dest)
                moved["signals"] += 1
    return moved
