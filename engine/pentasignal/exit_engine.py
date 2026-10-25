# exit_engine.py — موتور تعیین تکلیف پوزیشن (مطابق معناشناسی بک‌تست سخت‌گیرانه پنتا)
#
# قواعد کلیدی:
#  • ورود: بازِ کندل بعد از کندل سیگنال (ts+1800)
#  • اولویت داخل کندل: اول استاپ، بعد حد سود، بعد آپدیت تریل/آرم (بدون آینده‌نگری)
#  • گپ: اگر بازِ کندل از استاپ/تی‌پی عبور کرده باشد، پرشدگی در قیمتِ باز (بدتر)
#  • کارمزد رفت‌وبرگشت 0.2٪ روی نوتونال
#  • BK: پس از رسیدن به +armR استاپ به ورود+بافر کارمزد می‌رود (رویداد BE_ARMED)
#  • CM: بستن زمانی در کندل cm-اُم با قیمت بسته‌شدن

from dataclasses import dataclass, field
from typing import List, Optional

from . import settings

STATUS_TP = "TP_HIT"
STATUS_SL = "SL_HIT"
STATUS_BE = "BE_HIT"
STATUS_TRAIL = "TRAIL_HIT"
STATUS_CM = "CM_CLOSED"
STATUS_OPEN = "OPEN"

EXIT_REASON = {
    STATUS_TP: "TP",
    STATUS_SL: "SL",
    STATUS_BE: "BE",
    STATUS_TRAIL: "TRAIL",
    STATUS_CM: "CM",
}


@dataclass
class ExitResult:
    status: str = STATUS_OPEN
    exit_price: Optional[float] = None
    exit_candle_ts: Optional[int] = None   # زمان شروع کندلِ خروج
    events: List[dict] = field(default_factory=list)  # {ts, event, detail}
    r_multiple: float = 0.0
    candles_held: int = 0


def be_price_for(entry: float, direction: str, fee_rt: float = None) -> float:
    """قیمت سربه‌سر + بافر کارمزد"""
    fee_rt = fee_rt if fee_rt is not None else settings.FEE_RT
    return entry * (1 + fee_rt) if direction == "LONG" else entry * (1 - fee_rt)


def _status_on_stop(armed: bool, stop: float, be_px: float, exit_mode: str,
                    initial_sl: float) -> str:
    """تشخیص نوع برخورد استاپ: BE / تریل / حدضرر اولیه."""
    if armed and abs(stop - be_px) < 1e-12:
        return STATUS_BE
    # در مود تریل، برخورد با استاپ (چه اولیه چه جابه‌جاشده) TRAIL است
    if exit_mode == "TRAIL":
        return STATUS_TRAIL
    return STATUS_SL


def resolve(entry_candle_index: int,
            entry_price: float,
            direction: str,
            initial_sl: float,
            take_profit: Optional[float],
            exit_mode: str,
            trail_atr: Optional[float],
            atr_at_entry: Optional[float],
            candles: List[dict],
            cm_candles: int,
            bk_arm_r: float = 1.0) -> ExitResult:
    """از کندل ورود به بعد، کندل‌به‌کندل پوزیشن را رزولو می‌کند.
    candles[i] با i>=entry_candle_index استفاده می‌شود؛ ورود در open کندل ورود رخ داده است."""
    res = ExitResult()
    if direction not in ("LONG", "SHORT"):
        raise ValueError(direction)
    long = direction == "LONG"

    R = abs(entry_price - initial_sl) or 1e-12
    stop = initial_sl
    armed = False
    be_px = be_price_for(entry_price, direction)
    tp = take_profit
    if exit_mode == "TRAIL" and not tp:
        # تریلینگ چندلیر روی اکسترمم بسته‌شده از ورود
        pass
    best = entry_price   # برای TRAIL: بهترین قیمت دیده‌شده (LONG: بالاترین high)
    n = len(candles)

    for i in range(entry_candle_index, n):
        c = candles[i]
        res.candles_held = i - entry_candle_index + 1

        # ---------- گپ روی باز ----------
        gap_exit = None
        if long and c["o"] <= stop:
            gap_exit = c["o"]
        if (not long) and c["o"] >= stop:
            gap_exit = c["o"]
        if gap_exit is not None:
            res.status = _status_on_stop(armed, stop, be_px, exit_mode, initial_sl)
            res.exit_price = gap_exit
            res.exit_candle_ts = c["t"]
            break

        # ---------- استاپ داخل کندل (اولویت اول) ----------
        if (long and c["l"] <= stop) or ((not long) and c["h"] >= stop):
            res.status = _status_on_stop(armed, stop, be_px, exit_mode, initial_sl)
            res.exit_price = stop
            res.exit_candle_ts = c["t"]
            break

        # ---------- حد سود (اولویت دوم) ----------
        if tp is not None and ((long and c["h"] >= tp) or ((not long) and c["l"] <= tp)):
            res.status = STATUS_TP
            res.exit_price = tp
            res.exit_candle_ts = c["t"]
            break

        # ---------- آپدیت‌های پس از عبور ----------
        if exit_mode == "TRAIL" and trail_atr and atr_at_entry:
            best = max(best, c["h"]) if long else min(best, c["l"])
            trail_stop = (best - trail_atr * atr_at_entry) if long else (best + trail_atr * atr_at_entry)
            if long and trail_stop > stop:
                stop = trail_stop
                res.events.append({"ts": c["t"], "event": "TRAIL_MOVE",
                                   "detail": round(stop, 10)})
            elif (not long) and trail_stop < stop:
                stop = trail_stop
                res.events.append({"ts": c["t"], "event": "TRAIL_MOVE",
                                   "detail": round(stop, 10)})

        if exit_mode == "BK" and not armed:
            arm_px = entry_price + bk_arm_r * R if long else entry_price - bk_arm_r * R
            if (long and c["h"] >= arm_px) or ((not long) and c["l"] <= arm_px):
                armed = True
                stop = be_px
                res.events.append({"ts": c["t"], "event": "BE_ARMED",
                                   "detail": round(be_px, 10)})

        # ---------- بستن زمانی ----------
        if res.candles_held >= cm_candles:
            res.status = STATUS_CM
            res.exit_price = c["c"]
            res.exit_candle_ts = c["t"]
            break

    if res.status != STATUS_OPEN and res.exit_price is not None:
        move = (res.exit_price - entry_price) if long else (entry_price - res.exit_price)
        res.r_multiple = move / R
    return res


def pnl_usd(direction: str, entry: float, exit_px: float, pos_usd: float = None,
            fee_rt: float = None):
    """خالص PnL دلاری + بازده درصدی + کارمزد"""
    pos_usd = pos_usd if pos_usd is not None else settings.POSITION_SIZE_USD
    fee_rt = fee_rt if fee_rt is not None else settings.FEE_RT
    long = direction == "LONG"
    ret = (exit_px - entry) / entry if long else (entry - exit_px) / entry
    fee = pos_usd * fee_rt
    net = pos_usd * ret - fee
    return net, ret * 100.0, fee
