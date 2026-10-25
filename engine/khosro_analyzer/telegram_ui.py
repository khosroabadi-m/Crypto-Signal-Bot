from html import escape


def _fmt_price(value):
    value = float(value)
    if value >= 1000:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:,.4f}"
    return f"{value:.8f}"


def _ai_section(result):
    """Build readable AI vote lines for Telegram."""
    votes = result.get("ai_votes") or []
    report = result.get("ai_report") or ""
    if not votes and not report:
        # fallback: find ai_committee component detail
        for c in result.get("components") or []:
            if c.get("name") == "ai_committee":
                detail = escape(str(c.get("detail") or "")[:200])
                return f"<b>🤖 کمیته AI</b>\n  {detail}\n"
        return ""
    lines = ["<b>🤖 کمیته AI — رأی هر API</b>"]
    if votes:
        for v in votes:
            name = escape(str(v.get("provider", "?")))
            if v.get("ok"):
                dec = "✅ APPROVE" if v.get("approved") else "❌ REJECT"
                conf = float(v.get("confidence") or 0)
                reason = escape(str(v.get("reason") or "")[:60])
                model = escape(str(v.get("model") or "")[:40])
                lines.append(f"  ├─ <b>{name}</b>: {dec} <code>{conf:.0f}%</code>")
                if model:
                    lines.append(f"  │    model=<code>{model}</code>")
                if reason:
                    lines.append(f"  │    {reason}")
            else:
                err = escape(str(v.get("error") or "?")[:70])
                lines.append(f"  ├─ <b>{name}</b>: ⚠️ FAIL — {err}")
    else:
        lines.append(f"  {escape(report[:200])}")
    approved = result.get("ai_approved")
    if approved is True:
        lines.append("  └─ <b>نتیجه کمیته: APPROVE</b>")
    elif approved is False:
        lines.append("  └─ <b>نتیجه کمیته: REJECT</b>")
    else:
        lines.append("  └─ نتیجه کمیته: خنثی / در دسترس نبود")
    return "\n".join(lines) + "\n"


def signal_message(result, version, margin, leverage):
    direction = result["direction"]
    long = direction == "LONG"
    icon = "🟢" if long else "🔴"
    side = "LONG · خرید" if long else "SHORT · فروش"
    rr = result.get("rr") or 0
    confidence = float(result.get("confidence", result.get("score", 0)))
    components = result.get("components", [])
    top = sorted(
        [c for c in components if c.get("name") != "ai_committee"],
        key=lambda x: x.get("score", 0),
        reverse=True,
    )[:4]
    factors = "\n".join(
        f"  ├─ {escape(str(c['name']))}: <b>{float(c['score']):.2f}</b>"
        for c in top
    ) or "  └─ تأییدهای کافی ثبت نشده است"
    ai_block = _ai_section(result)
    try:
        entry_f, sl_f = float(result["price"]), float(result["stop_loss"])
        sl_pct = abs(entry_f - sl_f) / entry_f * 100 if entry_f else 0.0
    except (TypeError, ValueError, KeyError):
        sl_pct = 0.0
    return (
        f"<b>╔═ {icon} KHOSRO SIGNAL ═╗</b>\n"
        f"<b>{escape(result['symbol'])}</b>  ·  <b>{side}</b>\n"
        f"<code>v{escape(str(version))}</code>\n"
        f"╚════════════════════╝\n\n"
        f"🎯 <b>Confidence</b>  <code>{confidence:.1f}%</code>\n"
        f"📊 Rule score  <code>{float(result.get('rule_score') or 0):.1f}</code>\n\n"
        f"💼 <b>سرمایه:</b> ${margin:.2f}  ×  <b>{leverage:g}x</b>\n"
        f"📦 <b>حجم اسمی:</b> ${margin*leverage:.2f}\n\n"
        f"<b>╭─ نقشه معامله ─╮</b>\n"
        f"  ├─ ورود       <code>{_fmt_price(result['price'])}</code>\n"
        f"  ├─ حد ضرر     <code>{_fmt_price(result['stop_loss'])}</code>\n"
        f"  ├─ فاصله SL   <code>{sl_pct:.2f}%</code>\n"
        f"  ├─ هدف        <code>{_fmt_price(result['take_profit'])}</code>\n"
        f"  └─ نسبت R:R   <b>1 : {rr:.2f}</b>\n"
        f"<b>╰────────────────╯</b>\n\n"
        f"<b>🔎 مهم‌ترین تأییدها</b>\n{factors}\n\n"
        f"{ai_block}\n"
        f"<i>⏱ نتیجه با کندل 1m پایش می‌شود · v{escape(str(version))}</i>"
    )


def resolution_message(row, outcome, hit_price, pnl_usd, fee_usd, margin, leverage, version=None):
    from config import VERSION
    ver = version or VERSION
    if outcome == "TP_HIT":
        win, icon, title = True, "🏆", "TAKE PROFIT · معامله موفق"
    elif outcome == "EXPIRED":
        win, icon, title = False, "⏹", "EXPIRED · انقضای معامله"
    else:
        win, icon, title = False, "🛑", "STOP LOSS · معامله بسته شد"
    sign = "+" if pnl_usd >= 0 else ""
    ret = (pnl_usd / margin) * 100 if margin else 0.0
    return (
        f"<b>╔═ {icon} TRADE RESULT ═╗</b>\n"
        f"<b>{escape(row.get('symbol',''))}</b>  ·  <b>{escape(row.get('direction',''))}</b>\n"
        f"<code>v{escape(str(ver))}</code>\n"
        f"╚════════════════════╝\n\n"
        f"↩️ <b>{title}</b>\n\n"
        f"📍 Entry  <code>{_fmt_price(row.get('entry_price', 0))}</code>\n"
        f"🏁 Exit   <code>{_fmt_price(hit_price)}</code>\n"
        f"💼 Margin <b>${margin:.2f}</b>  ·  <b>{leverage:g}x</b>\n"
        f"📦 Notional <b>${margin*leverage:.2f}</b>\n\n"
        f"<b>╭─ نتیجه مالی ─╮</b>\n"
        f"  ├─ PnL خالص     <b>{sign}${pnl_usd:.4f}</b>\n"
        f"  ├─ کارمزد        <b>${fee_usd:.4f}</b>\n"
        f"  └─ بازده سرمایه  <b>{sign}{ret:.2f}%</b>\n"
        f"<b>╰────────────────╯</b>\n\n"
        f"<i>کندل 1m · کارمزد رفت‌وبرگشت · v{escape(str(ver))}</i>"
    )


def _dollar(value):
    value = float(value)
    sign = "+" if value >= 0 else "-"
    return f"{sign}${abs(value):.2f}"


def daily_report_message(date_str, rows, older_open_count=0):
    """Compose the previous-day performance report sent by nightly.yml.

    rows: every signal issued on `date_str` (Tehran). Trades still OPEN are
    shown as pending; win-rate uses only closed trades. Returns None when
    there is nothing to report at all (no signals and no older open trades).
    """
    total = len(rows)
    if total == 0 and older_open_count == 0:
        return None

    wins = [r for r in rows if r.get("status") == "TP_HIT"]
    losses = [r for r in rows if r.get("status") == "STOP_HIT"]
    opens = [r for r in rows if r.get("status") == "OPEN"]
    closed = wins + losses

    pnl_total = sum(float(r.get("final_pnl_usd") or 0.0) for r in rows)
    fee_total = sum(float(r.get("broker_fee_usd") or 0.0) for r in rows)

    def _side(direction):
        sel = [r for r in rows if r.get("direction", "").upper() == direction]
        w = sum(1 for r in sel if r.get("status") == "TP_HIT")
        p = sum(float(r.get("final_pnl_usd") or 0.0) for r in sel)
        return len(sel), w, p

    long_n, long_w, long_p = _side("LONG")
    short_n, short_w, short_p = _side("SHORT")

    best = max(closed, key=lambda r: float(r.get("final_pnl_usd") or 0.0), default=None)
    worst = min(closed, key=lambda r: float(r.get("final_pnl_usd") or 0.0), default=None)

    lines = [
        f"<b>╔═ 📋 DAILY REPORT ═╗</b>",
        f"<b>گزارش سیگنال‌های {escape(date_str)}</b>",
        f"╚════════════════════╝",
        "",
        f"📊 <b>سیگنال‌ها:</b> <b>{total}</b> عدد",
        f"  ├─ 🏆 تیک‌پروفت: <b>{len(wins)}</b>",
        f"  ├─ 🛑 حد ضرر: <b>{len(losses)}</b>",
        f"  └─ ⏳ هنوز باز: <b>{len(opens)}</b>",
    ]
    if closed:
        wr = 100.0 * len(wins) / len(closed)
        lines.append(f"📈 <b>نرخ برد:</b> <code>{wr:.1f}%</code> ({len(wins)}/{len(closed)} معامله بسته‌شده)")
    lines += [
        f"💰 <b>PnL خالص روز:</b> <b>{_dollar(pnl_total)}</b>",
        f"🧾 کارمزد کل: <b>${fee_total:.2f}</b>",
        "",
        f"🟢 لانگ: {long_n} سیگنال · {long_w} برد · <b>{_dollar(long_p)}</b>",
        f"🔴 شورت: {short_n} سیگنال · {short_w} برد · <b>{_dollar(short_p)}</b>",
    ]
    if best is not None and float(best.get("final_pnl_usd") or 0.0) > 0:
        lines.append(f"⭐ بهترین: {escape(best.get('symbol',''))} {escape(best.get('direction',''))} <b>{_dollar(float(best['final_pnl_usd']))}</b>")
    if worst is not None and float(worst.get("final_pnl_usd") or 0.0) < 0:
        lines.append(f"💀 بدترین: {escape(worst.get('symbol',''))} {escape(worst.get('direction',''))} <b>{_dollar(float(worst['final_pnl_usd']))}</b>")
    if older_open_count > 0:
        lines.append(f"⏳ باز از روزهای قبل: <b>{older_open_count}</b> معامله (نتیجه پس از بسته‌شدن ریپلای می‌شود)")
    lines.append("")
    lines.append("<i>گزارش خودکار شبانه · کندل 1m · کارمزد و اسلیپیج لحاظ شده است</i>")
    return "\n".join(lines)
