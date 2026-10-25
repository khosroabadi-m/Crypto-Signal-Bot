# report.py — گزارش کامل شبانه (اجرای 24:00) برای روز گذشته تهران

import os
import json
from collections import defaultdict
from datetime import datetime, timedelta

from . import settings, store
from .exit_engine import (STATUS_TP, STATUS_SL, STATUS_BE, STATUS_TRAIL,
                          STATUS_CM, STATUS_OPEN)
from .scenarios import SCENARIOS, ACTIVE_SCENARIOS
from .utils import tehran_now, parse_tehran, fmt_price, fa_weekday, duration_fa
from .messages import hashtags_report, _scenario_line


def _f(x, d=0.0):
    try:
        return float(x)
    except Exception:
        return d


def collect_day(report_date: str):
    """سیگنال‌های صادرشده در report_date + تعیین‌تکلیف‌های همان روز (از هر تاریخی).
    نکته: تعیین‌تکلیفی که دقیقاً ساعت 00:00 روز بعد ثبت می‌شود، به کندل 23:30 روزِ
    گذشته تعلق دارد → با تفریق ۱ ثانیه به روز گذشته منتسب می‌شود.

    فیکس v3.5.5: باکت سوم «بسته‌شده بعد از نیمه‌شب» — اگر ورک‌فلو شبانه دیر اجرا شود
    (تأخیر رایج Actions) و پوزیشنی بین 00:00 و لحظهٔ گزارش بسته شود، قبلاً نه در
    «تعیین‌تکلیف امروز» بود (تاریخ خروج روز بعد است) نه در «باز مانده» (وضعیت OPEN
    نیست) → از گزارش کاملاً حذف می‌شد و PnL/وین‌ریت غلط گزارش می‌شد (مورد XRP در
    گزارش 2026-09-21: TP +0.32$ ساعت 02:37 — ولی گزارش +0.01$ و «باز» می‌گفت)."""
    all_rows = store.all_signals()
    issued = [r for r in all_rows if (r.get("issued_at_tehran") or "").startswith(report_date)]
    closed_statuses = (STATUS_TP, STATUS_SL, STATUS_BE, STATUS_TRAIL, STATUS_CM)
    settled_today = []
    closed_after = []
    for r in all_rows:
        et = parse_tehran(r.get("exit_time_tehran") or "")
        if et is None:
            continue
        if r.get("status") not in closed_statuses:
            continue
        exit_day = (et - timedelta(seconds=1)).strftime("%Y-%m-%d")
        if exit_day == report_date:
            settled_today.append(r)
        elif exit_day > report_date:
            closed_after.append(r)
    still_open = [r for r in all_rows if r.get("status") == STATUS_OPEN]
    return issued, settled_today, still_open, closed_after


def build_report_message(report_date: str) -> str:
    issued, settled, still_open, closed_after = collect_day(report_date)
    d = parse_tehran(report_date + " 12:00:00")

    # آمار کلی روز
    st_counts = defaultdict(int)
    pnl_total = 0.0
    fee_total = 0.0
    for r in settled:
        st_counts[r["status"]] += 1
        pnl_total += _f(r.get("pnl_usd"))
        fee_total += _f(r.get("fee_usd"))
    closed_n = sum(st_counts.values())
    # فیکس v3.5.5 — نتایج بعد از نیمه‌شب تا لحظهٔ گزارش (اجرای دیر Actions)
    after_pnl = sum(_f(r.get("pnl_usd")) for r in closed_after)
    after_fee = sum(_f(r.get("fee_usd")) for r in closed_after)
    # وین‌ریت بر اساس PnL واقعی (نه فقط TP) — تریل سودده هم برد است
    wins = sum(1 for r in settled if _f(r.get("pnl_usd")) > 0.005)
    losses = sum(1 for r in settled if _f(r.get("pnl_usd")) < -0.005)
    flats = closed_n - wins - losses
    wr = (100.0 * wins / closed_n) if closed_n else 0.0

    bw_pool = settled + closed_after
    best = max(bw_pool, key=lambda r: _f(r.get("pnl_usd")), default=None) if bw_pool else None
    worst = min(bw_pool, key=lambda r: _f(r.get("pnl_usd")), default=None) if bw_pool else None

    # تفکیک سناریو (سیگنال‌های صادرشده امروز + نتیجه امروزِ همان سناریو)
    by_sc = defaultdict(lambda: {
        "issued": 0, "settled": 0, "tp": 0, "sl": 0, "be": 0, "trail": 0, "cm": 0, "pnl": 0.0,
    })
    for r in issued:
        by_sc[r.get("scenario_id", "?")]["issued"] += 1
    for r in settled:
        s = by_sc[r.get("scenario_id", "?")]
        s["settled"] += 1
        key = r["status"].replace("_HIT", "").replace("_CLOSED", "").lower()
        if key in s:
            s[key] += 1
        s["pnl"] += _f(r.get("pnl_usd"))

    gen_now = tehran_now().strftime("%H:%M")
    lines = [
        f"🌙 <b>گزارش کامل شبانه</b> · PentaSignal <b>v{settings.VERSION}</b>",
        f"📅 روز گذشته: {fa_weekday(d) if d else ''} <b>{report_date}</b>  (تولید: {gen_now})",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🆕 سیگنال جدید: <b>{len(issued)}</b> (پنجره 07:00 تا 20:00)",
        f"🔒 تعیین‌تکلیف روز: <b>{len(settled)}</b>",
        "",
        f"✅ TP: {st_counts[STATUS_TP]}   ❌ SL: {st_counts[STATUS_SL]}",
        f"🔵 TRAIL: {st_counts[STATUS_TRAIL]}   ➖ BE: {st_counts[STATUS_BE]}   🕒 CM: {st_counts[STATUS_CM]}",
    ]
    if closed_after:
        lines.append(f"⚡ بسته‌شده بعد از نیمه‌شب (تا {gen_now}): <b>{len(closed_after)}</b>")
        for r in sorted(closed_after, key=lambda x: x.get("exit_time_tehran") or ""):
            lines.append(
                f"• {r['symbol'].replace('-', '/')} {r['direction']} "
                f"{r['status'].replace('_HIT', '')} @ {(r.get('exit_time_tehran') or '')[11:16]}"
                f" → {_f(r.get('pnl_usd')):+.2f}$"
            )
    lines += [
        f"📂 باز مانده: <b>{len(still_open)}</b> (کل پرتفوی)",
        "",
        f"🏆 وین‌ریت روز: <b>{wr:.1f}%</b>  (برد {wins} · باخت {losses} · سربه‌سر {flats})",
        f"💵 PnL روز: <b>{pnl_total:+.2f}$</b>  |  💸 کارمزد: {fee_total + after_fee:.2f}$  |  پوزیشن {settings.POSITION_SIZE_USD:.0f}$",
    ]
    if closed_after:
        lines.append(f"💵 PnL کل (روز + بعد از نیمه‌شب): <b>{pnl_total + after_pnl:+.2f}$</b>")

    if len(issued) == 0:
        lines.append("\n📭 امروز سیگنال جدیدی صادر نشد (فیلترهای سناریوها صبور بودند).")

    # تفکیک سناریو
    if ACTIVE_SCENARIOS:
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("📊 <b>به تفکیک سناریو:</b>")
        for sid in ACTIVE_SCENARIOS:
            s = by_sc.get(sid)
            sc = SCENARIOS[sid]
            if not s or (s["issued"] == 0 and s["settled"] == 0):
                lines.append(f"• <b>#{sid}</b> {sc['name_fa']} — بدون مورد")
                continue
            tags = (f"TP {s['tp']} · SL {s['sl']} · TRAIL {s['trail']} · "
                    f"BE {s['be']} · CM {s['cm']}")
            lines.append(f"• <b>#{sid}</b> {sc['name_fa']} — سیگنال {s['issued']} | {tags} | {s['pnl']:+.2f}$")

    # بهترین/بدترین
    if best and worst and closed_n > 0:
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(
            f"⭐️ بهترین: {best['symbol'].replace('-', '/')} {best['direction']} ({_f(best['pnl_usd']):+.2f}$)"
            f"   |   😞 بدترین: {worst['symbol'].replace('-', '/')} {worst['direction']} ({_f(worst['pnl_usd']):+.2f}$)"
        )

    # سیگنال‌های باز
    if still_open:
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"📂 <b>پوزیشن‌های باز ({len(still_open)}):</b>")
        for r in still_open[:8]:
            lines.append(
                f"• #{r.get('scenario_id')} {r['symbol'].replace('-', '/')} {r['direction']}"
                f" @ {fmt_price(_f(r.get('entry_price')))}"
            )
        if len(still_open) > 8:
            lines.append(f"• … و {len(still_open) - 8} مورد دیگر")

    lines += ["", "🛌 از 20:00 تا 01:00 فقط تعیین تکلیف انجام می‌شود؛ سیگنال جدیدی صادر نمی‌شود.",
              hashtags_report()]
    return "\n".join(lines)


def save_report_json(report_date: str, extra: dict | None = None) -> str:
    issued, settled, still_open, closed_after = collect_day(report_date)
    payload = {
        "report_date": report_date,
        "generated_at": tehran_now().strftime("%Y-%m-%d %H:%M:%S"),
        "version": settings.VERSION,
        "issued": issued,
        "settled_today": settled,
        "closed_after_midnight": closed_after,
        "still_open": still_open,
    }
    if extra:
        payload.update(extra)
    path = os.path.join(settings.REPORTS_DIR, f"{report_date}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path
