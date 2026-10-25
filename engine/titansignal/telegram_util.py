"""Telegram helpers — rate limits, HTML, chunking, reply support."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional, Tuple

import aiohttp

from .config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, SCENARIOS

logger = logging.getLogger(__name__)

MAX_LEN = 3900
MIN_INTERVAL_SEC = 1.2


def fmt_price(price: Optional[float], symbol: str = "") -> str:
    if price is None:
        return "—"
    try:
        p = float(price)
    except (TypeError, ValueError):
        return "—"
    if p == 0:
        return "0"
    if abs(p) < 0.001:
        return f"{p:.8f}"
    if abs(p) < 1:
        return f"{p:.6f}"
    if abs(p) < 100:
        return f"{p:.4f}"
    return f"{p:.2f}"


def scenario_full_name(scenario_id: str = None, scenario_name: str = None) -> str:
    """Full display name: فارسی (English). Falls back to stored name / id."""
    sc = SCENARIOS.get(scenario_id or "") if scenario_id else None
    if sc:
        fa = sc.get("name_fa") or ""
        en = sc.get("name_en") or ""
        if fa and en:
            return f"{fa} ({en})"
        return fa or en or (scenario_id or "—")
    if scenario_name and scenario_name.strip():
        # if only English was stored, try enrich from SCENARIOS by matching name_en
        for sid, s in SCENARIOS.items():
            if s.get("name_en") == scenario_name or s.get("name_fa") == scenario_name:
                return f"{s.get('name_fa', '')} ({s.get('name_en', '')})".strip()
        return scenario_name
    return scenario_id or "—"


def chunk_text(text: str, max_len: int = MAX_LEN) -> list:
    if not text:
        return []
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        cut = text.rfind("\n", 0, max_len)
        if cut < max_len // 4:
            cut = max_len
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


async def send_telegram(
    text: str,
    parse_mode: str = "HTML",
    disable_preview: bool = True,
    reply_to_message_id: Optional[int] = None,
) -> Tuple[bool, Optional[int]]:
    """Send message(s). Returns (ok, first_message_id)."""
    if not text:
        return True, None
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.info("[Telegram skipped — no token] %s", text[:300].replace("\n", " | "))
        return False, None

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    ok_all = True
    first_msg_id = None
    chunks = chunk_text(text)

    async with aiohttp.ClientSession() as session:
        for i, ch in enumerate(chunks):
            if i:
                await asyncio.sleep(MIN_INTERVAL_SEC)
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": ch,
                "disable_web_page_preview": disable_preview,
            }
            if parse_mode:
                payload["parse_mode"] = parse_mode
            if i == 0 and reply_to_message_id:
                try:
                    payload["reply_to_message_id"] = int(reply_to_message_id)
                except (TypeError, ValueError):
                    pass

            for attempt in range(2):  # max 1 retry, don't burn CI minutes
                try:
                    async with session.post(url, json=payload, timeout=25) as resp:
                        body = await resp.text()
                        if resp.status == 200:
                            logger.info("Telegram OK (%d chars)", len(ch))
                            try:
                                data = json.loads(body)
                                mid = (data.get("result") or {}).get("message_id")
                                if mid is not None and first_msg_id is None:
                                    first_msg_id = int(mid)
                                    logger.info("Telegram message_id=%s", first_msg_id)
                            except Exception as e:
                                logger.warning("Could not parse message_id: %s", e)
                            break
                        if resp.status == 429:
                            wait = 5
                            try:
                                data = json.loads(body)
                                wait = int((data.get("parameters") or {}).get("retry_after") or 5)
                            except Exception:
                                pass
                            wait = min(max(wait, 1), 8)  # cap 8s — save Actions minutes
                            logger.warning("Telegram rate limit: retry after %ss (attempt %s)", wait, attempt + 1)
                            await asyncio.sleep(wait)
                            continue
                        # other errors
                        ok_all = False
                        if reply_to_message_id and i == 0 and "reply" in body.lower():
                            payload.pop("reply_to_message_id", None)
                            continue
                        if parse_mode and "parse" in body.lower():
                            payload.pop("parse_mode", None)
                            continue
                        logger.error("Telegram HTTP %s %s", resp.status, body[:300])
                        break
                except Exception as e:
                    ok_all = False
                    logger.error("Telegram error: %s", e)
                    break
            else:
                ok_all = False
                logger.error("Telegram failed after retries")
    return ok_all, first_msg_id



def outcome_label(outcome: str) -> str:
    m = {
        "STOP_HIT": "🔴 استاپ اولیه",
        "TRAIL_STOP": "🟢 خروج تریل",
        "MAX_HOLD": "⏰ سقف زمان",
        "EOD_FORCE_CLOSE": "⚠️ بستن اجباری پایان‌روز",
        "MARGIN_CLOSE": "⚠️ بستن مارجین",
        "OPEN": "🟡 باز",
    }
    return m.get(outcome or "", outcome or "—")


def build_exit_message(item: dict) -> str:
    """فاخر exit card — full scenario name with V4.2.0 details."""
    from .version import VERSION_LABEL
    direction = item.get("direction") or ""
    dir_e = "🟢 LONG" if direction == "LONG" else "🔴 SHORT"
    pnl = item.get("net_pnl") or 0
    roi = item.get("margin_roi") or 0
    pnl_e = "📈" if pnl >= 0 else "📉"
    sc_name = scenario_full_name(item.get("scenario_id"), item.get("scenario_name"))
    symbol = item.get("symbol") or "—"
    hold = item.get("hold", "—")
    trail_on = item.get("trail_activated", False)
    trail_status = "✅ فعال" if trail_on else "❌ غیرفعال"
    return (
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📤 <b>نتیجه سیگنال</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"نماد: <b>{symbol}</b>\n"
        f"جهت: {dir_e}\n"
        f"سناریو: <b>{sc_name}</b>\n"
        f"کد: <code>{item.get('scenario_id') or '—'}</code>\n"
        f"نتیجه: <b>{outcome_label(item.get('outcome', ''))}</b>\n"
        f"───────────\n"
        f"ورود: <code>{fmt_price(item.get('entry_price'), symbol)}</code>\n"
        f"خروج: <code>{fmt_price(item.get('exit_price'), symbol)}</code>\n"
        f"{pnl_e} PnL: <b>{pnl:+.4f}$</b>\n"
        f"ROI مارجین: <b>{roi:+.1f}%</b>\n"
        f"تریل: {trail_status}\n"
        f"کندل‌های نگهداری: {hold}\n"
        f"زمان ورود: {item.get('issued_at_tehran') or '—'}\n"
        f"───────────\n"
        f"<i>{VERSION_LABEL}</i>\n"
    )
