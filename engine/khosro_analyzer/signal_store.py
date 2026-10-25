import os
import csv
import glob
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from config import MARGIN_USD, LEVERAGE, SIGNAL_MAX_AGE_HOURS

SIGNALS_DIR = "signals"
CSV_HEADERS = [
    "symbol", "direction", "risk_level", "entry_price", "stop_loss", "take_profit",
    "issued_at_tehran", "issued_at_epoch", "status", "last_checked_epoch", "hit_time_tehran", "hit_price",
    "broker_fee_usd", "final_pnl_usd", "position_margin_usd", "leverage", "notional_usd",
    "return_pct", "telegram_message_id", "resolution_message_id", "signal_source"
]


def ensure_dir():
    os.makedirs(SIGNALS_DIR, exist_ok=True)


def tehran_date_str(dt=None):
    tz = ZoneInfo("Asia/Tehran")
    now = datetime.now(tz) if dt is None else dt.astimezone(tz)
    return now.strftime("%Y-%m-%d")


def tehran_time_str(dt=None):
    tz = ZoneInfo("Asia/Tehran")
    now = datetime.now(tz) if dt is None else dt.astimezone(tz)
    return now.strftime("%Y-%m-%d %H:%M:%S")


def daily_csv_path(date_str=None):
    ensure_dir()
    d = tehran_date_str() if date_str is None else date_str
    return os.path.join(SIGNALS_DIR, f"{d}.csv")


def initialize_daily_file(date_str=None):
    path = daily_csv_path(date_str)
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=CSV_HEADERS).writeheader()
    return path


def _all_signal_files():
    ensure_dir()
    return sorted(glob.glob(os.path.join(SIGNALS_DIR, "*.csv")))


def load_signals_for_date(date_str):
    """Return every signal row (OPEN and resolved) issued on a Tehran calendar day.

    Each day's ledger is one self-contained CSV named YYYY-MM-DD.csv.
    A missing file simply yields an empty list.
    """
    path = os.path.join(SIGNALS_DIR, f"{date_str}.csv")
    rows = []
    if not os.path.exists(path):
        return rows
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                rows.append(row)
    except (OSError, csv.Error):
        pass
    return rows


def load_open_signals():
    """Return every OPEN signal from the persistent daily CSV files."""
    result = []
    for path in _all_signal_files():
        try:
            with open(path, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    if row.get("status", "").upper() == "OPEN":
                        row["_path"] = path
                        result.append(row)
        except (OSError, csv.Error):
            continue
    return result


def _rewrite_file(path, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in CSV_HEADERS})
    os.replace(tmp, path)


def resolve_signal(row, hit_time_tehran, hit_price, pnl_usd, broker_fee_usd, outcome,
                   resolution_message_id=""):
    path = row.get("_path")
    if not path or not os.path.exists(path):
        return False
    rows = []
    changed = False
    with open(path, newline="", encoding="utf-8") as f:
        for current in csv.DictReader(f):
            same = (
                current.get("issued_at_epoch") == row.get("issued_at_epoch") and
                current.get("symbol") == row.get("symbol") and
                current.get("direction") == row.get("direction") and
                current.get("status", "").upper() == "OPEN"
            )
            if same:
                current["status"] = outcome
                current["last_checked_epoch"] = str(row.get("last_checked_epoch") or current.get("last_checked_epoch") or "")
                current["hit_time_tehran"] = hit_time_tehran
                current["hit_price"] = f"{hit_price:.8f}"
                current["broker_fee_usd"] = f"{broker_fee_usd:.4f}"
                current["final_pnl_usd"] = f"{pnl_usd:.4f}"
                margin = float(current.get("position_margin_usd") or MARGIN_USD)
                current["return_pct"] = f"{(pnl_usd / margin) * 100:.4f}"
                current["resolution_message_id"] = str(resolution_message_id or "")
                changed = True
            rows.append(current)
    if changed:
        _rewrite_file(path, rows)
    return changed



def update_last_checked(row, epoch):
    """Atomically advance an OPEN signal's 1m-resolution checkpoint."""
    path = row.get("_path")
    if not path or not os.path.exists(path):
        return False
    target = str(row.get("issued_at_epoch") or "")
    rows = []
    changed = False
    with open(path, newline="", encoding="utf-8") as f:
        for current in csv.DictReader(f):
            same = (
                current.get("issued_at_epoch") == target and
                current.get("symbol") == row.get("symbol") and
                current.get("direction") == row.get("direction") and
                current.get("status", "").upper() == "OPEN"
            )
            if same:
                old = int(float(current.get("last_checked_epoch") or 0))
                new = max(old, int(epoch or 0))
                if new != old:
                    current["last_checked_epoch"] = str(new)
                    changed = True
            rows.append(current)
    if changed:
        _rewrite_file(path, rows)
    return changed


def signal_age_hours(row, now_epoch=None) -> float:
    """Age of a signal in hours based on issued_at_epoch (v11.2)."""
    issued = int(float(row.get("issued_at_epoch") or 0))
    now_epoch = int(now_epoch or datetime.now(timezone.utc).timestamp())
    return max(0.0, (now_epoch - issued) / 3600.0)


def should_expire(row, now_epoch=None, max_age_hours=SIGNAL_MAX_AGE_HOURS) -> bool:
    """True when an OPEN signal has been open longer than the max age (v11.2).

    Set SIGNAL_MAX_AGE_HOURS = 0 in config to disable expiry.
    """
    if not max_age_hours or max_age_hours <= 0:
        return False
    if str(row.get("status", "")).upper() != "OPEN":
        return False
    return signal_age_hours(row, now_epoch) >= float(max_age_hours)

def append_signal_row(symbol, direction, risk_level_name, entry_price, stop_loss, take_profit,
                      issued_at_tehran, signal_source, position_margin_usd=MARGIN_USD,
                      leverage=LEVERAGE, telegram_message_id="", issued_at_epoch=None):
    path = daily_csv_path()
    initialize_daily_file()
    epoch = int(issued_at_epoch or datetime.now(timezone.utc).timestamp())
    notional = float(position_margin_usd) * float(leverage)
    row = {
        "symbol": symbol,
        "direction": direction,
        "risk_level": risk_level_name,
        "entry_price": f"{entry_price:.8f}",
        "stop_loss": f"{stop_loss:.8f}",
        "take_profit": f"{take_profit:.8f}",
        "issued_at_tehran": issued_at_tehran,
        "issued_at_epoch": str(epoch),
        "status": "OPEN",
        "last_checked_epoch": str(max(0, epoch - 60)),
        "hit_time_tehran": "",
        "hit_price": "",
        "broker_fee_usd": "",
        "final_pnl_usd": "",
        "position_margin_usd": f"{float(position_margin_usd):.2f}",
        "leverage": f"{float(leverage):.2f}",
        "notional_usd": f"{notional:.2f}",
        "return_pct": "",
        "telegram_message_id": str(telegram_message_id or ""),
        "resolution_message_id": "",
        "signal_source": signal_source,
    }
    with open(path, "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=CSV_HEADERS).writerow(row)
    return path, row
