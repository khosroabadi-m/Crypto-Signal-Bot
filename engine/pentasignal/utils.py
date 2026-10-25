# utils.py — ابزارهای زمان تهران و فرمت اعداد

from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

TEHRAN = ZoneInfo("Asia/Tehran")


def tehran_now() -> datetime:
    return datetime.now(TEHRAN)


def ts_to_tehran(ts: int) -> datetime:
    """ثانیه‌ی unix → datetime تهران"""
    return datetime.fromtimestamp(int(ts), TEHRAN)


def tehran_str(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def tehran_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def tehran_hm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def parse_tehran(s: str):
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=TEHRAN)
        except Exception:
            pass
    try:
        return datetime.fromisoformat(s).replace(tzinfo=TEHRAN)
    except Exception:
        return None


WEEKDAYS_FA = {
    0: "دوشنبه", 1: "سه‌شنبه", 2: "چهارشنبه", 3: "پنجشنبه",
    4: "جمعه", 5: "شنبه", 6: "یکشنبه",
}


def fa_weekday(dt: datetime) -> str:
    return WEEKDAYS_FA.get(dt.weekday(), "")


def duration_fa(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} دقیقه"
    h, m = divmod(minutes, 60)
    if h < 24:
        return f"{h} ساعت و {m} دقیقه" if m else f"{h} ساعت"
    d, h = divmod(h, 24)
    parts = [f"{d} روز"]
    if h:
        parts.append(f"{h} ساعت")
    return " و ".join(parts)


def fmt_price(x) -> str:
    """فرمت قیمت با اعشار تطبیقی"""
    try:
        x = float(x)
    except Exception:
        return str(x)
    ax = abs(x)
    if ax == 0:
        return "0"
    if ax < 0.001:
        return f"{x:.8f}"
    if ax < 1:
        return f"{x:.6f}"
    if ax < 100:
        return f"{x:.4f}"
    return f"{x:,.2f}"


def fmt_pct(x) -> str:
    return f"{float(x):+.2f}%"


def fmt_usd(x) -> str:
    return f"{float(x):+.2f}$"


def utc_ts(dt: datetime) -> int:
    return int(dt.timestamp())
