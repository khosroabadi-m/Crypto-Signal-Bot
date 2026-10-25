# telegram.py — کلاینت ارسال تلگرام با پشتیبانی کامل ریپلای + حالت DRY_RUN

import asyncio
import logging
import os
import time

import requests

from . import settings

logger = logging.getLogger("ps.telegram")

API = "https://api.telegram.org/bot{token}/sendMessage"

# در DRY_RUN شناسه پیام ساختگی صادر می‌کنیم تا زنجیره ریپلای در تست هم قابل ردیابی باشد
_fake_id = {"n": 100_000}


class SendResult:
    def __init__(self, ok, message_id=None, error=None):
        self.ok = ok
        self.message_id = message_id
        self.error = error


def send_sync(text: str, reply_to_message_id=None) -> SendResult:
    """ارسال پیام (parse_mode=HTML). اگر reply_to_message_id داده شود، ریپلای می‌شود."""
    if settings.DRY_RUN or not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        _fake_id["n"] += 1
        mid = _fake_id["n"]
        logger.info("[DRY-RUN] message_id=%s reply_to=%s\n%s\n%s",
                    mid, reply_to_message_id, text, "-" * 60)
        return SendResult(True, message_id=mid)

    payload = {
        "chat_id": settings.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_to_message_id:
        payload["reply_to_message_id"] = int(reply_to_message_id)
        payload["allow_sending_without_reply"] = True

    for attempt in range(3):
        try:
            r = requests.post(API.format(token=settings.TELEGRAM_BOT_TOKEN),
                              json=payload, timeout=25)
            if r.status_code == 200:
                mid = r.json().get("result", {}).get("message_id")
                return SendResult(True, message_id=mid)
            if r.status_code == 429:
                retry = r.json().get("parameters", {}).get("retry_after", 2)
                time.sleep(min(retry, 10))
                continue
            logger.warning("telegram HTTP %s: %s", r.status_code, r.text[:200])
            return SendResult(False, error=f"HTTP {r.status_code}")
        except Exception as e:
            logger.error("telegram error: %s", e)
            time.sleep(1 + attempt)
    return SendResult(False, error="retries exhausted")


async def send(text: str, reply_to_message_id=None) -> SendResult:
    """نسخه async برای استفاده در لوپ"""
    return await asyncio.to_thread(send_sync, text, reply_to_message_id)


def send_log_file() -> str:
    """در DRY_RUN همه پیام‌ها برای بازبینی در این فایل هم ذخیره می‌شوند"""
    return os.path.join(settings.DATA_DIR, "sent_messages.log")
