"""Daily Telegram performance report for the previous Tehran day.

Run by nightly.yml right after the OPEN-resolution pass, so the report
reflects every trade that closed during the night. Also runnable locally:

    python daily_report.py --print            # print yesterday's report
    python daily_report.py --date 2026-09-19  # report for a specific Tehran day
"""
import argparse
import asyncio
import logging
from datetime import timedelta
from zoneinfo import ZoneInfo

from signal_store import load_signals_for_date, load_open_signals, tehran_date_str
from telegram_ui import daily_report_message

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def previous_tehran_date():
    """Tehran calendar date (YYYY-MM-DD) of yesterday."""
    return (tehran_date_str_as_date() - timedelta(days=1)).isoformat()


def tehran_date_str_as_date():
    from datetime import datetime
    return datetime.now(ZoneInfo("Asia/Tehran")).date()


def build_report(date_str):
    """Return (message, stats) for a Tehran day; message may be None."""
    rows = load_signals_for_date(date_str)
    today = tehran_date_str_as_date().isoformat()
    older_open = 0
    if date_str == today:
        # Signals from earlier days that are still open (informational line).
        for row in load_open_signals():
            issued_day = (row.get("issued_at_tehran") or "")[:10]
            if issued_day and issued_day < date_str:
                older_open += 1
    msg = daily_report_message(date_str, rows, older_open_count=older_open)
    stats = {
        "date": date_str,
        "total": len(rows),
        "tp": sum(1 for r in rows if r.get("status") == "TP_HIT"),
        "sl": sum(1 for r in rows if r.get("status") == "STOP_HIT"),
        "open": sum(1 for r in rows if r.get("status") == "OPEN"),
        "older_open": older_open,
        "pnl": round(sum(float(r.get("final_pnl_usd") or 0.0) for r in rows), 4),
    }
    return msg, stats


async def send_report(msg):
    from rules import send_to_telegram
    message_id = await send_to_telegram(msg)
    if message_id is None:
        # Never abort the nightly workflow here: prune + commit must still run,
        # otherwise resolved trades stay OPEN in the repo and resolution
        # messages would be re-sent on the next run. send_to_telegram already
        # retried internally (5 attempts, 429-aware); if it still failed,
        # Telegram is down and the report is skipped for this night only.
        logger.error("Daily report send failed after retries "
                     "(check TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID); "
                     "continuing nightly maintenance so resolved trades persist")
        return None
    logger.info("Daily report sent, telegram_message_id=%s", message_id)
    return message_id


async def run(date_str=None, send=True):
    date_str = date_str or previous_tehran_date()
    msg, stats = build_report(date_str)
    logger.info("Daily report for %s: %s", date_str, stats)
    if msg is None:
        logger.info("Nothing to report for %s (no signals, no open trades); skipping send", date_str)
        return stats
    if send:
        await send_report(msg)
    else:
        print(msg)
    return stats


def main():
    parser = argparse.ArgumentParser(description="Khosro daily Telegram report")
    parser.add_argument("--print", dest="print_only", action="store_true",
                        help="print the report instead of sending it")
    parser.add_argument("--date", default=None, help="override Tehran date (YYYY-MM-DD)")
    args = parser.parse_args()
    asyncio.run(run(date_str=args.date, send=not args.print_only))


if __name__ == "__main__":
    main()
