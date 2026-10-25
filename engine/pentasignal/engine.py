# engine.py — هسته اجرایی: هر بسته‌شدن کندل 30m یک تیک
#
#  • تعیین تکلیف سیگنال‌های باز: در هر تیک (شبانه‌روز)
#  • صدور سیگنال جدید: فقط 07:00 ≤ ساعت تهران < 20:00
#  • 24:00 (00:00 تهران): گزارش کامل شبانه + نگهداری CSV 90 روزه
#
#  همه منطق «قطعی و بدون آینده‌نگری» است تا شبیه‌ساز و ربات زنده یک رفتار داشته باشند.

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import settings, store, scenarios, state
from .exit_engine import (resolve, pnl_usd, be_price_for,
                          STATUS_TP, STATUS_SL, STATUS_BE, STATUS_TRAIL,
                          STATUS_CM, STATUS_OPEN)
from . import messages as msg
from .utils import ts_to_tehran, tehran_str, tehran_date, parse_tehran, fmt_price

logger = logging.getLogger("ps.engine")


@dataclass
class MsgLog:
    ts: str                 # تهران
    kind: str               # SIGNAL / BE_ARMED / SETTLE / REPORT
    text: str
    reply_to: str = ""
    message_id: str = ""
    signal_id: str = ""
    meta: dict = field(default_factory=dict)


class Engine:
    def __init__(self, sender=None, entry_price_provider=None, light=False):
        # sender(text, reply_to) → object with .message_id؛ None = حالت شبیه‌سازی بی‌صدا
        # light=True → آپدیت‌های صرفاً اطلاعاتی CSV (last_check_ts) انجام نمی‌شود (شبیه‌سازی بلند)
        self.sender = sender
        self.entry_price_provider = entry_price_provider
        self.light = light
        self._auto_id = 500_000

    # ---------- ارسال ----------
    def _send(self, log: MsgLog):
        if self.sender is not None:
            res = self.sender(log.text, int(log.reply_to) if log.reply_to else None)
            log.message_id = str(getattr(res, "message_id", "") or "")
        else:
            self._auto_id += 1
            log.message_id = str(self._auto_id)
        return log

    # ---------- ابزار ----------
    @staticmethod
    def _candle_index_at(candles, close_ts: int, bar_seconds: int = 1800):
        """اندیس آخرین کندل کاملاً بسته‌شده تا close_ts."""
        want = close_ts - bar_seconds
        for i in range(len(candles) - 1, -1, -1):
            if candles[i]["t"] == want:
                return i
            if candles[i]["t"] < want:
                return i
        return -1

    @staticmethod
    def _entry_row(sig, entry: float, close_dt: datetime, entry_candle_ts: int):
        """ساخت ردیف CSV برای سیگنال جدید — SL/TP نسبت به ورود واقعی بازمحاسبه می‌شود."""
        sc = scenarios.SCENARIOS[sig["scenario_id"]]
        atr_val = sig["atr"]
        long = sig["direction"] == "LONG"
        # اگر دیتکتور (مثل F1/Khosro) SL/TP داده، همان را نگه دار
        if sig.get("stop_loss") is not None:
            sl = float(sig["stop_loss"])
        else:
            sl = entry - sc["sl_atr"] * atr_val if long else entry + sc["sl_atr"] * atr_val
        tp = ""
        exit_param = ""
        if sig.get("take_profit") is not None and sig.get("take_profit") != "":
            tp = f"{float(sig['take_profit']):.10f}"
        elif sc["exit_mode"] == "FIXED":
            # LONG: هدف بالای ورود · SHORT: هدف پایین ورود
            tp_px = entry + sc["tp_atr"] * atr_val if long else entry - sc["tp_atr"] * atr_val
            tp = f"{tp_px:.10f}"
        if sc["exit_mode"] == "TRAIL":
            exit_param = sc["trail_atr"]
        elif sc["exit_mode"] == "BK":
            exit_param = f"arm={sc['bk_arm_r']}"
        risk_pct = abs(entry - sl) / entry * 100.0
        sid = sig["scenario_id"]
        sym = sig["symbol"]
        return {
            "signal_id": f"{sid}-{sym}-{close_dt.strftime('%Y%m%d-%H%M')}",
            "issued_at_tehran": tehran_str(close_dt),
            "candle_close_tehran": tehran_str(close_dt),
            "symbol": sym,
            "direction": sig["direction"],
            "scenario_id": sid,
            "entry_price": f"{entry:.10f}",
            "stop_loss": f"{sl:.10f}",
            "take_profit": tp,
            "exit_mode": sc["exit_mode"],
            "exit_param": exit_param,
            "sl_atr_mult": sc["sl_atr"],
            "cm_candles": sc["cm_candles"],
            "atr": f"{atr_val:.10f}",
            "position_size_usd": f"{settings.POSITION_SIZE_USD:.2f}",
            "risk_pct": f"{risk_pct:.4f}",
            "reason": sig.get("reason", ""),
            "status": STATUS_OPEN,
            "entry_candle_ts": entry_candle_ts,
            "be_armed": "0",
            "be_price": "",
            "last_check_ts": "",
            "notes": "",
        }

    # ---------- صدور سیگنال جدید ----------
    def detect_new(self, close_dt: datetime, ctx: scenarios.MarketContext) -> list:
        """اسکن همه نمادها روی آخرین کندل بسته‌شده؛ خروجی لیست MsgLog سیگنال.

        در پایان یک گزارش تشخیصی در لاگ می‌نویسد تا در GitHub Actions
        مشخص شود چرا سیگنالی صادر نشده (داده کم، عدم تطبیق دیتکتور،
        پوزیشن باز، کول‌داون، نبود قیمت ورود).
        """
        logs = []
        close_ts = int(close_dt.timestamp())
        stats = {
            "symbols_scanned": 0,
            "short_history": 0,
            "no_setup": 0,
            "has_open": 0,
            "cooldown": 0,
            "no_entry_price": 0,
            "cap_blocked": 0,
            "issued": 0,
        }
        skip_details = []  # حداکثر چند نمونه برای خوانایی لاگ

        # ----- گاردهای پرتفویی W2 (از v3.3.0) — هم‌معنا با RiskEngine خوسرو -----
        guards = {"open_count": None, "daily_r": None, "halted": ""}
        if settings.PORTFOLIO_GUARDS:
            guards["open_count"] = len(store.open_signals())
            guards["daily_r"] = store.realized_r_on(close_dt.strftime("%Y-%m-%d"))
            if guards["daily_r"] <= -abs(settings.MAX_DAILY_LOSS_R):
                guards["halted"] = "circuit_breaker"
            elif guards["open_count"] >= settings.MAX_OPEN_TRADES:
                guards["halted"] = "max_open_trades"

        def _note(reason: str, symbol: str, sid: str = "-", detail: str = ""):
            if len(skip_details) < 200:
                skip_details.append(f"{symbol} | {sid} | {reason}" + (f" | {detail}" if detail else ""))

        if guards["halted"]:
            # مدار قطع/سقف پوزیشن فعال است — هیچ اسکن و صدوری انجام نمی‌شود؛
            # تسویه پوزیشن‌های باز در settle_all (که قبل از detect_new اجرا می‌شود) ادامه دارد.
            stats["halted_by_guard"] = guards["halted"]
            logger.info(
                "PORTFOLIO GUARD ACTIVE (%s): open=%s/%s daily_r=%+.2f — no new signals this tick",
                guards["halted"], guards["open_count"], settings.MAX_OPEN_TRADES,
                guards["daily_r"] if guards["daily_r"] is not None else 0.0,
            )
        else:
            scan_symbols = list(scenarios.UNION_SYMBOLS)
            extra_syms = sorted(getattr(ctx, "f1_extra_symbols", None) or [])
            for ex in extra_syms:
                if ex not in scan_symbols:
                    scan_symbols.append(ex)
            for symbol in scan_symbols:
                candles = ctx.candles30.get(symbol) or []
                i = self._candle_index_at(candles, close_ts, 1800)
                stats["symbols_scanned"] += 1
                if i < 60:
                    stats["short_history"] += 1
                    _note("داده_ناکافی", symbol, detail=f"bars={len(candles)} idx={i}")
                    continue

                # برای هر سناریوی مجاز این نماد، وضعیت را جدا گزارش کن
                issued_sigs, reject_list = scenarios.run_detectors_detailed(candles, i, ctx, symbol)
                matched = {sig["scenario_id"]: sig for sig in issued_sigs}
                for rj in reject_list:
                    stats["no_setup"] += 1
                    _note(rj.get("reason") or "شرایط_دیتکتور_برقرار_نیست",
                          rj.get("symbol") or symbol, rj.get("scenario_id") or "?")
                for sid in scenarios.ACTIVE_SCENARIOS:
                    if symbol not in scenarios.SCENARIO_SYMBOLS.get(sid, []):
                        continue
                    if sid not in matched:
                        continue

                    sig = matched[sid]
                    sc = scenarios.SCENARIOS[sid]

                    if store.has_open(symbol, sid):
                        stats["has_open"] += 1
                        _note("پوزیشن_باز_موجود", symbol, sid)
                        continue

                    # گارد W2 — بررسی مجدد در حین اسکن: صدور سیگنال‌های همین تیک
                    # هم در سقف پوزیشن باز حساب می‌شود (فیکس v3.5.5 — تیک 15:30
                    # روز 2026-09-21 ده سیگنال یکجا صادر کرده بود و سقف ۵ را دور زد)
                    if settings.PORTFOLIO_GUARDS and guards["open_count"] is not None:
                        open_now = guards["open_count"] + stats["issued"]
                        if open_now >= settings.MAX_OPEN_TRADES:
                            stats["cap_blocked"] += 1
                            _note("سقف_پوزیشن_باز", symbol, sid,
                                  detail=f"open={open_now}/{settings.MAX_OPEN_TRADES}")
                            continue

                    last_t = store.last_signal_time(symbol, sid)
                    if last_t and (close_dt - last_t).total_seconds() < sc["cooldown_h"] * 3600:
                        stats["cooldown"] += 1
                        left_h = sc["cooldown_h"] - (close_dt - last_t).total_seconds() / 3600
                        _note("کول‌داون", symbol, sid, detail=f"باقی≈{left_h:.1f}h")
                        continue

                    if self.entry_price_provider:
                        entry = self.entry_price_provider(symbol, close_ts)
                    else:
                        entry = float(sig["entry_hint"])
                    if not entry or entry <= 0:
                        stats["no_entry_price"] += 1
                        _note("قیمت_ورود_نامعتبر", symbol, sid)
                        continue

                    row = self._entry_row(sig, entry, close_dt, entry_candle_ts=close_ts)
                    store.append_signal(row)
                    store.append_event(row["issued_at_tehran"], row["signal_id"],
                                       "SIGNAL", f"entry={entry:.10f}")
                    sig2 = dict(sig)
                    sig2.update({
                        "entry_price": row["entry_price"], "stop_loss": row["stop_loss"],
                        "take_profit": row["take_profit"], "exit_mode": row["exit_mode"],
                        "exit_param": row["exit_param"], "candle_close_tehran": row["candle_close_tehran"],
                    })
                    log = MsgLog(ts=row["issued_at_tehran"], kind="SIGNAL",
                                 text=msg.format_signal(sig2), signal_id=row["signal_id"],
                                 meta={"symbol": symbol, "scenario_id": sid,
                                       "direction": sig["direction"]})
                    self._send(log)
                    store.update_signal(row["signal_id"],
                                        telegram_message_id=log.message_id)
                    logs.append(log)
                    stats["issued"] += 1

        # ----- گزارش تشخیصی برای لاگ Actions -----
        lines = [
            "===== گزارش صدور سیگنال =====",
            f"زمان کندل: {close_dt.strftime('%Y-%m-%d %H:%M')} تهران",
            f"نمادهای اسکن‌شده: {stats['symbols_scanned']}",
            f"صادر شده: {stats['issued']}",
            f"رد — داده ناکافی: {stats['short_history']}",
            f"رد — شرایط دیتکتور برقرار نیست: {stats['no_setup']}",
            f"رد — پوزیشن باز موجود: {stats['has_open']}",
            f"رد — کول‌داون فعال: {stats['cooldown']}",
            f"رد — قیمت ورود نامعتبر: {stats['no_entry_price']}",
        ]
        if stats.get("cap_blocked"):
            lines.append(
                f"رد — سقف پوزیشن باز ({settings.MAX_OPEN_TRADES}): {stats['cap_blocked']}"
            )
        if settings.PORTFOLIO_GUARDS:
            lines.append(
                f"گارد پرتفویی W2: پوزیشن باز {guards['open_count']}/{settings.MAX_OPEN_TRADES}"
                f" · R امروز {guards['daily_r']:+.2f} (آستانه {-abs(settings.MAX_DAILY_LOSS_R):+.1f}R)"
            )
        di = getattr(ctx, "discovery_info", None)
        if di:
            sel = di.get("selected") or {}
            lines.append(
                f"کشف ترند F1: حالت {sel.get('mode')} · منابع "
                f"{','.join(di.get('sources_ok') or []) or '-'} "
                f"(شکست: {','.join((di.get('sources_failed') or {}).keys()) or '-'})"
            )
            rows = sel.get("rows") or []
            if rows:
                lines.append("انتخاب rules: " + " | ".join(
                    f"{r['symbol']} score={r.get('confidence') if r.get('confidence') is not None else '-'}"
                    for r in rows[:12] if r.get('selected')
                ))
            lines.append(
                f"universe F1 ({len(sel.get('universe') or [])}): "
                f"[{','.join(sorted(sel.get('universe') or []))}]"
            )
            te = sel.get("trend_extra") or []
            if te:
                lines.append(
                    f"ترند CG خارج‌استخر (فقط F1): [{','.join(te)}]"
                )
        if guards["halted"] == "circuit_breaker":
            lines.append(
                f"⛔ مدار قطع روزانه فعال: R امروز {guards['daily_r']:+.2f} ≤ "
                f"{-abs(settings.MAX_DAILY_LOSS_R):+.1f}R — تا پایان روز هیچ سیگنال جدیدی صادر نمی‌شود"
            )
        elif guards["halted"] == "max_open_trades":
            lines.append(
                f"⛔ سقف پوزیشن باز پر است: {guards['open_count']}/{settings.MAX_OPEN_TRADES}"
                " — تا آزاد شدن پوزیشن‌ها هیچ سیگنال جدیدی صادر نمی‌شود"
            )
        if skip_details:
            # خلاصه پرتکرارترین دلایل به‌ازای هر سناریو
            from collections import Counter
            by_sc = {}
            for row in skip_details:
                parts = [x.strip() for x in row.split("|")]
                if len(parts) >= 3:
                    sid, reason = parts[1], parts[2]
                    by_sc.setdefault(sid, Counter())[reason] += 1
            if by_sc:
                lines.append("--- پرتکرارترین دلایل رد (به‌ازای سناریو) ---")
                for sid in sorted(by_sc):
                    top = by_sc[sid].most_common(5)
                    lines.append(f"  {sid}: " + " ؛ ".join(f"{r} ×{n}" for r, n in top))
            lines.append("--- جزئیات رد سناریوها ---")
            lines.extend(skip_details)
        lines.append("===== پایان گزارش صدور =====")
        logger.info("\n".join(lines))
        return logs

    # ---------- تعیین تکلیف ----------
    def settle_all(self, close_dt: datetime, ctx: scenarios.MarketContext) -> list:
        """بررسی همه سیگنال‌های باز با کندل‌های تازه (بدون آینده‌نگری)."""
        logs = []
        close_ts = int(close_dt.timestamp())
        for row in store.open_signals():
            symbol = row["symbol"]
            # خروج زنده با وضوح 1m؛ داده در data/ohlcv همزمان آرشیو می‌شود.
            candles = ctx.candles1m.get(symbol) or ctx.candles30.get(symbol) or []
            bar_minutes = 1 if ctx.candles1m.get(symbol) else 30
            if not candles:
                continue
            entry_candle_ts = int(row.get("entry_candle_ts") or 0) or \
                (int(parse_tehran(row["issued_at_tehran"]).timestamp()))
            # در داده 1m، از دقیقه صدور/ورود به بعد بررسی می‌کنیم؛ کندل باز هرگز در upto وارد نمی‌شود.
            bar_seconds = 60 if bar_minutes == 1 else 1800
            # اولین کندل بعد/در زمان ورود را پیدا کن؛ لازم نیست t دقیقاً برابر باشد.
            entry_idx = next((j for j, c in enumerate(candles)
                              if int(c["t"]) >= entry_candle_ts), None)
            if entry_idx is None or entry_idx >= len(candles):
                continue
            # فقط کندل‌هایی که تا close_ts کاملاً بسته شده‌اند.
            upto = self._candle_index_at(candles, close_ts, bar_seconds)
            if upto is None or upto < entry_idx:
                continue

            sc = scenarios.SCENARIOS.get(row["scenario_id"])
            if not sc:
                continue
            exit_mode = row["exit_mode"]
            trail_atr = sc.get("trail_atr") if exit_mode == "TRAIL" else None
            bk_arm = sc.get("bk_arm_r", 1.0) if exit_mode == "BK" else 1.0
            tp = float(row["take_profit"]) if row.get("take_profit") else None
            entry = float(row["entry_price"])
            sl = float(row["stop_loss"])
            atr_entry = float(row["atr"])
            direction = row["direction"]

            res = resolve(entry_idx, entry, direction, sl, tp, exit_mode,
                          trail_atr, atr_entry, candles[:upto + 1],
                          cm_candles=(int(row["cm_candles"]) * 30 if bar_minutes == 1 else int(row["cm_candles"])),
                          bk_arm_r=bk_arm)

            be_armed = row.get("be_armed") == "1"
            new_events = []
            if exit_mode == "BK" and not be_armed:
                for ev in res.events:
                    if ev["event"] == "BE_ARMED":
                        be_px = be_price_for(entry, direction)
                        row["be_armed"] = "1"
                        row["be_price"] = f"{be_px:.10f}"
                        be_at = tehran_str(ts_to_tehran(ev["ts"]))
                        store.update_signal(row["signal_id"], be_armed="1",
                                            be_price=f"{be_px:.10f}",
                                            be_armed_at_tehran=be_at)
                        store.append_event(be_at, row["signal_id"], "BE_ARMED", f"{be_px:.10f}")
                        sig2 = self._row_to_sig(row)
                        sig2["be_price"] = be_px
                        sig2["bk_arm_r"] = bk_arm
                        log = MsgLog(ts=tehran_str(ts_to_tehran(ev["ts"])), kind="BE_ARMED",
                                     text=msg.format_be_armed(sig2),
                                     reply_to=row.get("telegram_message_id", ""),
                                     signal_id=row["signal_id"])
                        self._send(log)
                        store.update_signal(row["signal_id"],
                                            settle_message_id=log.message_id)
                        logs.append(log)
                        new_events.append(ev)

            if res.status != STATUS_OPEN:
                exit_px = res.exit_price
                pos = float(row.get("position_size_usd") or settings.POSITION_SIZE_USD)
                net, ret_pct, fee = pnl_usd(direction, entry, exit_px, pos)
                held_minutes = res.candles_held * bar_minutes
                # زمان پایان کندل خروج: برای 1m = +60s و برای 30m = +1800s
                exit_dt = ts_to_tehran(res.exit_candle_ts + bar_seconds)
                reason = {STATUS_TP: "TP", STATUS_SL: "SL",
                          STATUS_BE: "BE", STATUS_TRAIL: "TRAIL",
                          STATUS_CM: "CM"}[res.status]
                store.update_signal(
                    row["signal_id"], status=res.status,
                    exit_price=f"{exit_px:.10f}",
                    exit_time_tehran=tehran_str(exit_dt),
                    exit_reason=reason,
                    pnl_usd=f"{net:.6f}", return_pct=f"{ret_pct:.4f}",
                    fee_usd=f"{fee:.6f}", r_multiple=f"{res.r_multiple:.4f}",
                    last_check_ts=str(candles[upto]["t"]),
                )
                store.append_event(tehran_str(exit_dt), row["signal_id"],
                                   f"SETTLE_{reason}",
                                   f"exit={exit_px:.10f};pnl={net:.6f}")
                sig2 = self._row_to_sig(row)
                log = MsgLog(ts=tehran_str(exit_dt), kind="SETTLE",
                             text=msg.format_settle(sig2, res.status, exit_px,
                                                    net, ret_pct, held_minutes),
                             reply_to=row.get("telegram_message_id", ""),
                             signal_id=row["signal_id"],
                             meta={"status": res.status, "pnl_usd": net})
                self._send(log)
                store.update_signal(row["signal_id"],
                                    settle_message_id=log.message_id)
                logs.append(log)
            else:
                if not self.light:
                    store.update_signal(row["signal_id"],
                                        last_check_ts=str(candles[upto]["t"]))
        return logs

    @staticmethod
    def _row_to_sig(row) -> dict:
        return {
            "signal_id": row["signal_id"],
            "symbol": row["symbol"],
            "direction": row["direction"],
            "scenario_id": row["scenario_id"],
            "entry_price": row["entry_price"],
            "stop_loss": row["stop_loss"],
            "take_profit": row.get("take_profit") or "",
            "exit_mode": row["exit_mode"],
            "exit_param": row.get("exit_param"),
            "candle_close_tehran": row.get("candle_close_tehran") or row["issued_at_tehran"],
        }

    # ---------- گزارش شبانه ----------
    def nightly(self, close_dt: datetime) -> MsgLog:
        """اجرای 24:00: گزارش کامل روز گذشته + نگهداری CSV

        ضدتکرار: کلید last_nightly_date در state.json — هر تاریخ فقط یک‌بار
        گزارش می‌فرستد (محافظت در برابر اجرای هم‌زمان/تکراری Actions)."""
        from . import report as rpt
        report_date = tehran_date(close_dt - timedelta(days=1))
        if state.get("last_nightly_date") == report_date:
            logger.info("nightly report %s already sent — skip", report_date)
            return None
        text = rpt.build_report_message(report_date)
        log = MsgLog(ts=tehran_str(close_dt), kind="REPORT", text=text)
        self._send(log)
        # کلید را بلافاصله بعد از ارسال موفق ثبت کن؛ خطای احتمالی save/rotate
        # در تیک بعدی جبران می‌شود ولی پیام تکراری تولید نمی‌شود
        state.set_key("last_nightly_date", report_date)
        path = rpt.save_report_json(report_date)
        moved = store.rotate_90d()
        logger.info("nightly report %s → %s | archive moved: %s",
                    report_date, path, moved)
        return log

    # ---------- تیک کامل ----------
    def on_candle_close(self, close_dt: datetime, ctx: scenarios.MarketContext,
                        allow_new: bool | None = None, run_nightly: bool = False) -> list:
        """یک تیک کامل: اول تعیین تکلیف، بعد (در پنجره مجاز) صدور سیگنال؛ گزارش شبانه فقط در تیک اختصاصی اجرا می‌شود"""
        logs = []
        # 1) تعیین تکلیف — همیشه
        logs += self.settle_all(close_dt, ctx)
        # 2) صدور سیگنال جدید — فقط پنجره 07:00 تا 20:00
        if allow_new is None:
            allow_new = settings.NEW_SIGNAL_START_HOUR <= close_dt.hour < settings.NEW_SIGNAL_END_HOUR
        if allow_new:
            logs += self.detect_new(close_dt, ctx)
        # 3) گزارش شبانه فقط در تیک اختصاصی ساعت 02:00 تهران اجرا می‌شود.
        if run_nightly:
            rpt_log = self.nightly(close_dt)
            if rpt_log is not None:
                logs.append(rpt_log)
        return logs
