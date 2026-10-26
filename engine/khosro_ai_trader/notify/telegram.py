"""Telegram notifier: pushes the trending board as a formatted HTML message.

Secrets (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID) come from the environment —
on GitHub Actions set them as repo Secrets; locally use a .env file.
If they are missing, notifications are skipped gracefully (never fails a run).
"""

from __future__ import annotations

import requests

from ..config import Config
from ..logger import get_logger
from ..models import TrendingSnapshot
from ..signals.base import Signal

API_URL = "https://api.telegram.org/bot{token}/sendMessage"
MAX_LEN = 3900  # Telegram hard limit is 4096 — keep headroom

log = get_logger("notify.telegram")


def _fmt_volume(value: float | None) -> str:
    if not value:
        return "—"
    for div, suf in ((1e9, "B$"), (1e6, "M$"), (1e3, "K$")):
        if value >= div:
            return f"{value / div:,.2f}{suf}"
    return f"{value:,.0f}$"


def _fmt_price(value: float | None) -> str:
    if value is None:
        return "—"
    if value >= 1:
        return f"${value:,.2f}"
    if value >= 0.01:
        return f"${value:,.4f}"
    return f"${value:.8f}"


_DIR_BADGE = {"long": "🟢", "short": "🔴", "watch": "🟡", "avoid": "⚪"}
_DIR_FA = {"long": "Long", "short": "Short", "watch": "Watch", "avoid": "Avoid"}


def _ai_block(snapshot: TrendingSnapshot) -> list[str]:
    """AI aggregation-layer section of the Telegram message."""
    ai = snapshot.ai
    if not ai:
        return []
    lines = ["", f"🤖 <b>تحلیل هوش مصنوعی</b> — مدل: <code>{ai.model}</code>"]
    if ai.market_summary:
        lines.append(f"📌 {ai.market_summary}")
    sent_fa = {"risk-on": "Risk-On 📈", "risk-off": "Risk-Off 📉", "mixed": "Mixed 🌐"}
    lines.append(
        f"🎨 مود بازار: <b>{sent_fa.get(ai.sentiment, ai.sentiment)}</b> "
        f"| اطمینان: {ai.sentiment_confidence}%"
    )
    verdicts = [ai.verdict_for(c.symbol) for c in snapshot.coins[:8]]
    verdicts = [v for v in verdicts if v]
    if verdicts:
        lines.append("─" * 20)
        for v in verdicts:
            badge = _DIR_BADGE.get(v.direction, "🟡")
            label = _DIR_FA.get(v.direction, v.direction)
            reason = v.reasons[0] if v.reasons else v.risk_note
            lines.append(
                f"{badge} <b>{v.symbol}</b> → {label} ({v.confidence}%)"
                + (f" — {reason}" if reason else "")
            )
    return lines


def build_message(snapshot: TrendingSnapshot, top_n: int = 10) -> str:
    lines: list[str] = [
        "🔥 <b>KhosroAiTrader — ارزهای ترند بازار</b>",
        f"🕐 <code>{snapshot.run_at_utc}</code> UTC | {snapshot.run_at_tehran} تهران",
        f"📦 دیتا از: {', '.join(snapshot.sources_ok) or '—'}",
        "────────────────────",
    ]

    for coin in snapshot.coins[:top_n]:
        change = coin.stats.change_24h_pct
        arrow = "🟢" if (change or 0) >= 0 else "🔴"
        change_s = f"{change:+.1f}%" if change is not None else "—"
        vol_s = _fmt_volume(coin.stats.volume_24h_usd)
        mcap_s = _fmt_volume(coin.stats.market_cap_usd)
        lines.append(
            f"{coin.rank}. <b>{coin.symbol}</b> — امتیاز <b>{coin.score:.1f}</b> "
            f"{arrow} <code>{change_s}</code>"
        )
        lines.append(
            f"   💰 {_fmt_price(coin.stats.price_usd)} | 📊 حجم: <code>{vol_s}</code> "
            f"| 🏦 مارکت‌کپ: <code>{mcap_s}</code>"
        )
        if coin.binance_pair:
            lines.append(f"   🔗 جفت: <code>{coin.binance_pair}</code>")

    if len(snapshot.coins) > top_n:
        rest = ", ".join(c.symbol for c in snapshot.coins[top_n:])
        lines.append(f"… سایر: <code>{rest}</code>")

    lines.extend(_ai_block(snapshot))

    if snapshot.sources_failed:
        failed = ", ".join(snapshot.sources_failed)
        lines.append(f"⚠️ منابع در دسترس نبودند: <code>{failed}</code>")

    msg = "\n".join(lines)
    if len(msg) > MAX_LEN:
        msg = msg[: MAX_LEN - 20] + "\n…"
    return msg


def build_signal_message(signals: list[Signal], snapshot: TrendingSnapshot) -> str:
    """Actionable signal card: direction, entry/stop/TPs, sizing, reasons."""
    lines = [
        "🎯 <b>KhosroAiTrader — سیگنال جدید</b>",
        f"🕐 <code>{snapshot.run_at_utc}</code> UTC | بازه: <code>{signals[0].timeframe}</code>",
        "────────────────────",
    ]
    for s in signals:
        badge = "🟢 LONG" if s.direction == "long" else "🔴 SHORT"
        lines.append(f"{badge} <b>{s.symbol}</b> — اطمینان <b>{s.confidence}%</b>")
        lines.append(
            f"   ورود: <code>{_fmt_price(s.entry)}</code> | استاپ: "
            f"<code>{_fmt_price(s.stop_loss)}</code>"
        )
        tps = " · ".join(f"TP{i+1}: {_fmt_price(tp)}" for i, tp in enumerate(s.take_profits))
        lines.append(f"   اهداف: <code>{tps}</code> | R/R@TP2: <code>{s.rr:.1f}</code>")
        lines.append(
            f"   💵 ریسک: <code>{(s.position_size_usd or 0):.2f}$</code> "
            f"(1R) | حجم: <code>{(s.notional_usd or 0):.2f}$</code>"
        )
        if s.reasons:
            lines.append(f"   📋 {'; '.join(s.reasons[:3])}")
        md = s.meta or {}
        extra = []
        if md.get("funding_8h_pct") is not None:
            extra.append(f"فاندینگ {md['funding_8h_pct']:+.3f}%")
        if md.get("fear_greed") is not None:
            extra.append(f"ترس‌وطمع {md['fear_greed']}")
        if md.get("ai"):
            extra.append(md["ai"])
        if extra:
            lines.append(f"   ℹ️ {' · '.join(str(e) for e in extra)}")
        lines.append("─" * 20)
    lines.append("⚠️ سیگنال کاغذی است — نه توصیه سرمایه‌گذاری")
    msg = "\n".join(lines)
    if len(msg) > MAX_LEN:
        msg = msg[: MAX_LEN - 20] + "\n…"
    return msg


def build_daily_report(stats: dict, open_trades: list[dict], circuit_note: str = "") -> str:
    """Daily paper-account report: equity, R, win rate, open positions."""
    ret = stats.get("return_pct", 0)
    arrow = "📈" if ret >= 0 else "📉"
    lines = [
        "📊 <b>KhosroAiTrader — گزارش روزانه حساب کاغذی</b>",
        "────────────────────",
        f"{arrow} سرمایه: <b>{stats.get('equity', 0):,.2f}$</b> "
        f"({ret:+.2f}٪)",
        f"🧮 مجموع R: <code>{stats.get('total_r', 0):+.2f}R</code> "
        f"| امروز: <code>{stats.get('today_r', 0):+.2f}R</code>",
        f"🎯 وین‌ریت: <b>{stats.get('win_rate') or '—'}</b> "
        f"| PF: <code>{stats.get('profit_factor') or '—'}</code>",
        f"📁 معاملات: <b>{stats.get('open_trades', 0)}</b> باز / "
        f"<b>{stats.get('closed_trades', 0)}</b> بسته",
    ]
    if stats.get("unrealized_r"):
        lines.append(f"⏳ سود/زیان باز: <code>{stats['unrealized_r']:+.2f}R</code>")
    if open_trades:
        lines.append("─" * 20)
        for t in open_trades[:6]:
            badge = "🟢" if t["direction"] == "long" else "🔴"
            lines.append(
                f"{badge} <b>{t['symbol']}</b> — {t.get('realized_r', 0):+.2f}R "
                f"(باقی‌مانده {t.get('remaining_pct', 0):.0f}٪)"
            )
    if circuit_note:
        lines.append(f"🛑 {circuit_note}")
    lines.append("─" * 20)
    lines.append("🔔 داشبورد: docs/index.html در ریپو (GitHub Pages)")
    msg = "\n".join(lines)
    return msg[:MAX_LEN]


class TelegramNotifier:
    def __init__(self, config: Config) -> None:
        self.cfg = config

    @property
    def configured(self) -> bool:
        return bool(self.cfg.telegram_bot_token and self.cfg.telegram_chat_id)

    def send(self, text: str) -> bool:
        if not self.cfg.telegram.enabled:
            log.info("telegram disabled in config — skipped")
            return False
        if not self.configured:
            log.info(
                "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — notification skipped"
            )
            return False
        try:
            resp = requests.post(
                API_URL.format(token=self.cfg.telegram_bot_token),
                json={
                    "chat_id": self.cfg.telegram_chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_notification": self.cfg.telegram.disable_notification,
                    "link_preview_options": {"is_disabled": True},
                },
                timeout=self.cfg.run.request_timeout,
            )
            if resp.status_code == 200:
                log.info("telegram message sent ✔")
                return True
            log.error("telegram send failed: HTTP %s %s", resp.status_code, resp.text[:200])
        except requests.RequestException as exc:
            log.error("telegram send error: %s", exc)
        return False

    def send_snapshot(self, snapshot: TrendingSnapshot) -> bool:
        return self.send(build_message(snapshot, self.cfg.telegram.top_coins_in_message))

    def send_signals(self, signals: list[Signal], snapshot: TrendingSnapshot) -> bool:
        """Signal message — sent only when there is something actionable."""
        if not signals:
            return False
        return self.send(build_signal_message(signals, snapshot))

    def send_daily_report(self, stats: dict, open_trades: list[dict],
                          circuit_note: str = "") -> bool:
        """Once-per-day equity/PnL summary (Tehran date)."""
        return self.send(build_daily_report(stats, open_trades, circuit_note))
