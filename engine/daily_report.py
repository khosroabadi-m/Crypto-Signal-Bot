"""گزارش روزانه — خلاصه عملکرد ۲۴ ساعت گذشته.

این اسکریپت در GitHub Actions هر شب اجرا می‌شود و خلاصه‌ای از نتایج
سیگنال‌های ۲۴ ساعت گذشته را به تلگرام می‌فرستد.

برای اجرای دستی:
    python engine/daily_report.py --print
"""
import os
import json
import asyncio
import argparse
from datetime import datetime, timezone, timedelta
from collections import defaultdict

import aiohttp

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TRADE_HISTORY_FILE = os.getenv("TRADE_HISTORY_FILE", "trade_history.json")


async def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(text)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=15) as r:
                if r.status == 200:
                    print("✓ گزارش روزانه ارسال شد")
                else:
                    print(f"⚠️ Telegram HTTP {r.status}")
    except Exception as e:
        print(f"❌ Telegram error: {e}")


def load_history():
    if not os.path.exists(TRADE_HISTORY_FILE):
        return []
    try:
        with open(TRADE_HISTORY_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def save_history(history):
    with open(TRADE_HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


def build_report(rows):
    if not rows:
        return "📋 <b>گزارش روزانه</b>\n\n⏳ هنوز سیگنالی ثبت نشده است."

    wins = [r for r in rows if r.get("status") == "TP2_HIT"]
    losses = [r for r in rows if r.get("status") == "SL_HIT"]
    expired = [r for r in rows if r.get("status") == "EXPIRED"]
    open_count = len([r for r in rows if r.get("status") == "OPEN"])
    closed = wins + losses

    pnl_total = sum(r.get("pnlUsd", 0) or 0 for r in rows)
    fee_total = sum(r.get("brokerFeeUsd", 0) or 0 for r in rows)

    by_side = {"LONG": [], "SHORT": []}
    for r in rows:
        if r.get("direction") in by_side:
            by_side[r["direction"]].append(r)

    win_rate = (len(wins) / len(closed) * 100) if closed else 0

    lines = [
        f"<b>╔═ 📋 DAILY REPORT ═╗</b>",
        f"<b>گزارش ۲۴ ساعت گذشته</b>",
        f"<b>╚════════════════════╝</b>",
        "",
        f"📊 <b>کل سیگنال:</b> <b>{len(rows)}</b>",
        f"  ├─ 🏆 تیک‌پروفت: <b>{len(wins)}</b>",
        f"  ├─ 🛑 حد ضرر: <b>{len(losses)}</b>",
        f"  ├─ ⏸ منقضی: <b>{len(expired)}</b>",
        f"  └─ ⏳ باز: <b>{open_count}</b>",
    ]
    if closed:
        lines.append(f"📈 <b>نرخ برد:</b> <code>{win_rate:.1f}%</code> ({len(wins)}/{len(closed)})")
    lines.append(f"💰 <b>PnL خالص:</b> <b>{'+' if pnl_total >= 0 else ''}${pnl_total:.2f}</b>")
    lines.append(f"🧾 کارمزد: <b>${fee_total:.2f}</b>")
    lines.append("")
    for side_name, side_rows in by_side.items():
        if not side_rows:
            continue
        side_wins = len([r for r in side_rows if r.get("status") == "TP2_HIT"])
        side_pnl = sum(r.get("pnlUsd", 0) or 0 for r in side_rows)
        icon = "🟢" if side_name == "LONG" else "🔴"
        lines.append(f"{icon} {side_name}: {len(side_rows)} سیگنال · {side_wins} برد · <b>{'+' if side_pnl >= 0 else ''}${side_pnl:.2f}</b>")

    best = max(closed, key=lambda r: r.get("pnlUsd", 0) or 0, default=None)
    worst = min(closed, key=lambda r: r.get("pnlUsd", 0) or 0, default=None)
    if best and (best.get("pnlUsd", 0) or 0) > 0:
        lines.append(f"⭐ بهترین: {best['symbol']} {best['direction']} <b>+${best.get('pnlUsd', 0):.2f}</b>")
    if worst and (worst.get("pnlUsd", 0) or 0) < 0:
        lines.append(f"💀 بدترین: {worst['symbol']} {worst['direction']} <b>${worst.get('pnlUsd', 0):.2f}</b>")

    lines.append("")
    lines.append("<i>گزارش خودکار شبانه · GitHub Actions</i>")
    return "\n".join(lines)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--print", action="store_true", help="فقط چاپ کن، تلگرام نفرست")
    args = parser.parse_args()

    history = load_history()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    recent = [r for r in history if r.get("hitTime") and r["hitTime"] >= cutoff.isoformat()]

    report = build_report(recent)
    if args.print:
        print(report)
    else:
        await send_telegram(report)


if __name__ == "__main__":
    asyncio.run(main())
