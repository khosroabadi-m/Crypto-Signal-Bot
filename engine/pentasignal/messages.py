# messages.py — قالب پیام‌های Telegram برای PentaSignal v3.0.1
#
# قوانین ظاهری:
#  • جداکننده: ---------------------
#  • هشتگ‌ها: فقط #نماد و #اسم_سناریو (با زیرخط)
#  • نام سناریو: F3 · پول‌بک در روند
#  • نام پروژه و ورژن در انتهای پیام
#  • نتیجه با علامت سبز/قرمز مشخص شود

from . import settings
from .utils import fmt_price, fa_weekday, duration_fa
from .exit_engine import STATUS_TP, STATUS_SL, STATUS_BE, STATUS_TRAIL, STATUS_CM

VERSION = settings.VERSION
BRAND = "PentaSignal"
SEP = "---------------------"


def _scenario_name_fa(sig) -> str:
    from .scenarios import SCENARIOS
    sc = SCENARIOS.get(sig["scenario_id"], {})
    return sc.get("name_fa", sig["scenario_id"])


def _scenario_line(sig) -> str:
    return f"{sig['scenario_id']} · {_scenario_name_fa(sig)}"


def _scenario_hashtag(sig) -> str:
    """هشتگ سناریو: فاصله‌ها به _ تبدیل می‌شوند."""
    name = _scenario_name_fa(sig)
    tag = name.replace(" ", "_").replace("‌", "_").replace("·", "").replace("__", "_")
    return f"#{tag}"


def _symbol_tag(sig) -> str:
    return f"#{sig['symbol'].split('-')[0]}"


def _hashtags(sig) -> str:
    return f"{_symbol_tag(sig)}  {_scenario_hashtag(sig)}"


def _risk_pct(entry, sl, direction) -> float:
    if direction == "LONG":
        return (entry - sl) / entry * 100.0
    return (sl - entry) / entry * 100.0


def _tp_label(sig) -> str:
    if sig.get("take_profit"):
        return f"<code>{fmt_price(sig['take_profit'])}</code>"
    mode = sig.get("exit_mode")
    if mode == "TRAIL":
        return f"تریلینگ {sig.get('exit_param')}×ATR (دینامیک)"
    if mode == "BK":
        arm = "1"
        ep = str(sig.get("exit_param") or "")
        if "arm=" in ep:
            arm = ep.split("arm=")[1].split(";")[0]
        try:
            cm_days = float(sig.get("cm_candles", 288)) / 48.0
            cm_label = f"{cm_days:g} روز"
        except Exception:
            cm_label = "طبق سناریو"
        return f"سربه‌سر خودکار پس از +{arm}R · سقف نگهداری {cm_label}"
    return "—"


def _date_line(candle_close_tehran: str) -> str:
    from .utils import parse_tehran
    d = parse_tehran(candle_close_tehran)
    if not d:
        return candle_close_tehran
    return f"{fa_weekday(d)} {d.strftime('%Y-%m-%d')} · {d.strftime('%H:%M')} تهران"


def _footer() -> str:
    return f"<i>{BRAND}  ·  v{VERSION}</i>"


# ---------- پیام سیگنال جدید ----------
def format_signal(sig) -> str:
    sym = sig["symbol"].replace("-", "/")
    entry = float(sig["entry_price"])
    sl = float(sig["stop_loss"])
    risk = _risk_pct(entry, sl, sig["direction"])
    is_long = sig["direction"] == "LONG"
    arrow = "🟢" if is_long else "🔴"
    dir_fa = "لانگ" if is_long else "شورت"
    risk_usd = settings.POSITION_SIZE_USD * risk / 100.0
    tp_html = _tp_label(sig)

    lines = [
        f"{arrow} <b>سیگنال {dir_fa}</b>",
        f"🏷 <b>{_scenario_line(sig)}</b>",
        f"🪙 <b>{sym}</b>  ·  30m",
        f"🗓 {_date_line(sig['candle_close_tehran'])}",
        SEP,
        f"◈ ورود      <code>{fmt_price(entry)}</code>",
        f"◈ حد ضرر    <code>{fmt_price(sl)}</code>  <i>({-risk:+.2f}%)</i>",
        f"◈ خروج      {tp_html}",
        f"⚖️ ریسک     <b>{risk:.2f}%</b>  ≈  <b>{risk_usd:.2f}$</b> از {settings.POSITION_SIZE_USD:.0f}$",
        SEP,
        "↩️ <i>تعیین تکلیف در ریپلای همین پیام اعلام می‌شود.</i>",
        _hashtags(sig),
        _footer(),
    ]
    return "\n".join(lines)


# ---------- پیام جابجایی استاپ به سربه‌سر (ریپلای) ----------
def format_be_armed(sig) -> str:
    sym = sig["symbol"].replace("-", "/")
    dir_fa = "لانگ" if sig["direction"] == "LONG" else "شورت"
    arm = sig.get("bk_arm_r", 1)
    lines = [
        "🛡 <b>حد ضرر به سربه‌سر منتقل شد</b>",
        SEP,
        f"↩️ <b>{_scenario_line(sig)}</b>",
        f"🪙 {sym}  ·  {dir_fa}",
        f"• قیمت به <b>+{arm}R</b> رسید",
        f"• استاپ جدید: <code>{fmt_price(sig.get('be_price'))}</code>",
        "• از این لحظه ریسک این معامله ≈ صفر است.",
        SEP,
        _hashtags(sig),
        _footer(),
    ]
    return "\n".join(lines)


# ---------- پیام تعیین تکلیف (ریپلای) ----------
_SETTLE_HEAD = {
    STATUS_TP: ("🟢", "✅", "حد سود فعال شد", "سود کامل برداشت شد"),
    STATUS_SL: ("🔴", "❌", "حد ضرر فعال شد", "معامله با ضرر بسته شد"),
    STATUS_BE: ("🟡", "➖", "خروج سربه‌سر", "پس از جابجایی استاپ، بدون سود/ضرر بسته شد"),
    STATUS_TRAIL: ("🔵", "📉", "خروج تریلینگ", "استاپ متحرک فعال شد و پوزیشن بسته شد"),
    STATUS_CM: ("🟠", "🕒", "بستن زمانی پوزیشن", "پس از پایان مهلت نگهداری، با قیمت بازار بسته شد"),
}


def format_settle(sig, status: str, exit_price: float, pnl_usd: float,
                  ret_pct: float, held_minutes: int) -> str:
    color, emoji, title, sub = _SETTLE_HEAD[status]
    dur = duration_fa(held_minutes)
    sym = sig["symbol"].replace("-", "/")
    dir_fa = "لانگ" if sig["direction"] == "LONG" else "شورت"

    # علامت سبز/قرمز بر اساس نتیجه واقعی
    if pnl_usd > 0.005:
        result_mark = "🟢"
        result_label = "سود"
    elif pnl_usd < -0.005:
        result_mark = "🔴"
        result_label = "ضرر"
    else:
        result_mark = "🟡"
        result_label = "سربه‌سر"

    lines = [
        f"{result_mark} {emoji} <b>{title}</b>",
        SEP,
        f"↩️ <b>{_scenario_line(sig)}</b>",
        f"🪙 {sym}  ·  {dir_fa}",
        SEP,
        f"◈ ورود     <code>{fmt_price(sig['entry_price'])}</code>",
        f"◈ خروج     <code>{fmt_price(exit_price)}</code>",
        f"📈 بازده    <b>{ret_pct:+.2f}%</b>",
        f"💵 نتیجه    <b>{pnl_usd:+.2f}$</b>  ({result_label})",
        f"⏱ مدت      <b>{dur}</b>",
        f"💬 {sub}",
        SEP,
        _hashtags(sig),
        _footer(),
    ]
    return "\n".join(lines)


# ---------- هشتگ گزارش شبانه (برای سازگاری با report.py) ----------
def hashtags_report() -> str:
    return "#گزارش_شبانه"
