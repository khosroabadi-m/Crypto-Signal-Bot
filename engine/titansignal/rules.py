import asyncio
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict
import logging
import time

from .config import (
    RISK_LEVELS, RISK_FACTORS, SCENARIOS, RISK_PARAMS,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
)
from .indicators import (
    calculate_adx, calculate_cci, calculate_sar,
    calculate_stochastic, calculate_ema,
    calculate_atr,
)
from .patterns import ema_rejection, resistance_test, pullback, double_top_bottom
from .signal_store import tehran_time_str

logger = logging.getLogger(__name__)

# ===== Cooldown Tracker =====
_last_signal_times: Dict[str, Dict[str, float]] = {}


def reset_cooldowns():
    """Clear all cooldowns."""
    for key in _last_signal_times:
        _last_signal_times[key] = {}


@dataclass
class RuleResult:
    """Result of a single rule evaluation."""
    name: str
    passed: bool
    detail: str

    def __str__(self):
        status = "PASS" if self.passed else "FAIL"
        return f"[{status}] {self.name}: {self.detail}"


# =====================================================================
#  RULES — each returns a RuleResult
# =====================================================================

def rule_body_strength(open_15m, close_15m, high_15m, low_15m, risk_rules) -> RuleResult:
    bs = abs(close_15m - open_15m) / max(high_15m - low_15m, 1e-6)
    th = risk_rules.get("candle_15m_strength", 0.5)
    if bs > 0.8:
        ok = False
        detail = f"BS15={bs:.3f} [too high - likely end of move]"
    else:
        ok = bs >= th
        detail = f"BS15={bs:.3f} [>= {th}]"
    return RuleResult("Candle Strength 15m", ok, detail)


def rule_body_strength_5m(open_5m, close_5m, high_5m, low_5m, risk_rules) -> RuleResult:
    if open_5m is None or close_5m is None or high_5m is None or low_5m is None:
        return RuleResult("Candle Strength 5m", False, "SKIP (no 5m data)")
    bs = abs(close_5m - open_5m) / max(high_5m - low_5m, 1e-6)
    th = risk_rules.get("candle_5m_strength", 0.5)
    if bs > 0.8:
        ok = False
        detail = f"BS5={bs:.3f} [too high]"
    else:
        ok = bs >= th
        detail = f"BS5={bs:.3f} [>= {th}]"
    return RuleResult("Candle Strength 5m", ok, detail)


def rule_trend_1h(ema21_1h, ema50_1h, direction) -> RuleResult:
    if ema21_1h is None or ema50_1h is None:
        return RuleResult("EMA Trend 1h", False, "No data")
    ok = (ema21_1h > ema50_1h) if direction == "LONG" else (ema21_1h < ema50_1h)
    return RuleResult("EMA Trend 1h", ok, f"EMA21={ema21_1h:.2f}, EMA50={ema50_1h:.2f}")


def rule_trend_4h(ema21_4h, ema50_4h, ema200_4h, direction) -> RuleResult:
    if ema21_4h is None or ema50_4h is None or ema200_4h is None:
        return RuleResult("EMA Trend 4h", False, "No data")
    if direction == "LONG":
        ok = ema21_4h > ema50_4h and ema50_4h > ema200_4h
    else:
        ok = ema21_4h < ema50_4h and ema50_4h < ema200_4h
    return RuleResult("EMA Trend 4h", ok, f"EMA21={ema21_4h:.2f}, EMA50={ema50_4h:.2f}, EMA200={ema200_4h:.2f}")


def rule_rsi(rsi_30m, direction, risk_level) -> RuleResult:
    if rsi_30m is None:
        return RuleResult("RSI 30m", False, "No data")
    if direction == "LONG":
        if rsi_30m > 75:
            ok = False
        elif risk_level == "LOW":
            ok = 50 <= rsi_30m <= 65
        elif risk_level == "MEDIUM":
            ok = 45 <= rsi_30m <= 70
        else:
            ok = 40 <= rsi_30m <= 75
    else:
        if rsi_30m < 35:
            ok = False
        elif rsi_30m > 70:
            ok = False
        elif risk_level == "LOW":
            ok = 35 <= rsi_30m <= 48
        elif risk_level == "MEDIUM":
            ok = 35 <= rsi_30m <= 50
        else:
            ok = 35 <= rsi_30m <= 55
    return RuleResult("RSI 30m", ok, f"RSI={rsi_30m:.2f}")


def rule_macd(macd_hist, direction, risk_level) -> RuleResult:
    if macd_hist is None:
        return RuleResult("MACD 30m", False, "No data")
    if isinstance(macd_hist, list):
        macd_hist = macd_hist[-1] if macd_hist else 0.0
    if direction == "LONG":
        if risk_level == "LOW":
            ok = macd_hist > 0.002
        elif risk_level == "MEDIUM":
            ok = macd_hist > 0.001
        else:
            ok = macd_hist > 0.0005
    else:
        if risk_level == "LOW":
            ok = macd_hist < -0.002
        elif risk_level == "MEDIUM":
            ok = macd_hist < -0.0015
        else:
            ok = macd_hist < -0.001
    return RuleResult("MACD 30m", ok, f"MACD_hist={macd_hist:.4f}")


def rule_smart_pullback_entry(price_30m, ema21_30m, rsi_30m, open_15m, close_15m, high_15m, low_15m, direction) -> RuleResult:
    if price_30m is None or ema21_30m is None or rsi_30m is None:
        return RuleResult("Smart Pullback", False, "No data")
    bs = abs(close_15m - open_15m) / max(high_15m - low_15m, 1e-8)
    candle_strong = 0.40 <= bs <= 0.85
    if direction == "LONG":
        pullback_ok = price_30m < ema21_30m * 0.998
        rsi_ok = 45 <= rsi_30m <= 60
    else:
        pullback_ok = price_30m > ema21_30m * 1.002
        rsi_ok = 35 <= rsi_30m <= 50
    ok = pullback_ok and rsi_ok and candle_strong
    detail = f"price={price_30m:.4f} EMA={ema21_30m:.4f} RSI={rsi_30m:.1f} BS={bs:.3f}"
    return RuleResult("Smart Pullback", ok, detail)


def rule_adx(candles: list, direction: str) -> RuleResult:
    adx, di_plus, di_minus = calculate_adx(candles)
    if adx is None:
        return RuleResult("ADX", False, "No ADX data")
    if direction == "LONG":
        ok = adx > 22 and di_plus > di_minus
    else:
        ok = adx > 22 and di_minus > di_plus
    detail = f"ADX={adx:.2f} [>22], DI+={di_plus:.2f}, DI-={di_minus:.2f}"
    return RuleResult("ADX", ok, detail)


def rule_cci_momentum(candles, direction) -> RuleResult:
    cci = calculate_cci(candles)
    if cci is None:
        return RuleResult("CCI Momentum", False, "No data")
    if direction == "LONG":
        ok = False if cci > 100 else cci > -20
    else:
        ok = False if cci < -100 else cci < 20
    return RuleResult("CCI Momentum", ok, f"CCI={cci:.2f}")


def rule_sar(candles: list, direction: str) -> RuleResult:
    sar = calculate_sar(candles)
    if sar is None:
        return RuleResult("SAR", False, "No data")
    last_close = candles[-1]['c']
    ok = (last_close > sar) if direction == "LONG" else (last_close < sar)
    return RuleResult("SAR", ok, f"SAR={sar:.4f}, price={last_close:.4f}")


def rule_stochastic_momentum(candles, direction) -> RuleResult:
    k, d = calculate_stochastic(candles)
    if k is None or d is None:
        return RuleResult("Stochastic", False, "No data")
    if direction == "LONG":
        ok = False if k > 80 else (k > d and 20 < k < 70)
    else:
        ok = False if k < 20 else (k < d and 25 < k < 80)
    return RuleResult("Stochastic", ok, f"K={k:.2f} D={d:.2f}")


def rule_ema_rejection(prices_series_30m: list, ema21_30m: float) -> RuleResult:
    if not prices_series_30m or ema21_30m is None:
        return RuleResult("EMA Rejection", False, "No data")
    rejected = ema_rejection(prices_series_30m, ema21_30m)
    return RuleResult("EMA Rejection", rejected, "Rejected" if rejected else "No rejection")


def rule_resistance_test(prices_series_30m: list, ema50_30m: float) -> RuleResult:
    if not prices_series_30m or ema50_30m is None:
        return RuleResult("Resistance Test", False, "No data")
    tested = resistance_test(prices_series_30m, ema50_30m)
    return RuleResult("Resistance Test", tested, "Tested" if tested else "No test")


def rule_pullback(prices_series_30m: list, direction: str) -> RuleResult:
    if not prices_series_30m:
        return RuleResult("Pullback", False, "No data")
    pb = pullback(prices_series_30m, direction)
    return RuleResult("Pullback", pb, "Detected" if pb else "No pullback")


def rule_double_top_bottom(prices_series_30m: list) -> RuleResult:
    if not prices_series_30m:
        return RuleResult("Double Top/Bottom", False, "No data")
    pattern = double_top_bottom(prices_series_30m)
    ok = pattern is not None
    return RuleResult("Double Top/Bottom", ok, f"Pattern={pattern}" if ok else "No pattern")


def rule_range_filter(ema21_30m, ema50_30m, price_30m) -> RuleResult:
    if ema21_30m is None or ema50_30m is None or not price_30m:
        return RuleResult("Range Filter", False, "No data")
    diff = abs(ema21_30m - ema50_30m) / price_30m
    ok = diff > 0.005
    return RuleResult("Range Filter", ok, f"EMA diff={diff:.4f} [>0.005]")


def rule_combined_range_filter(diff: float, adx: float, direction: str, mode: str = "OR") -> RuleResult:
    if mode == "AND":
        bad = (diff < 0.003 and adx < 22)
    else:
        bad = (diff < 0.003 or adx < 22)
    ok = not bad
    return RuleResult("Combined Range Filter", ok, f"diff={diff:.4f}, ADX={adx:.2f}, mode={mode}")


# =====================================================================
#  RULE GROUP WEIGHT MAP
# =====================================================================
RULE_GROUP_MAP = {
    "Candle Strength 15m": "Candles",
    "Candle Strength 5m": "Candles",
    "EMA Trend 1h": "EMA",
    "EMA Trend 4h": "TF_Big",
    "RSI 30m": "Confirm",
    "MACD 30m": "Confirm",
    "Smart Pullback": "Confirm",
    "ADX": "ADX",
    "CCI Momentum": "CCI",
    "SAR": "SAR",
    "Stochastic": "Stoch",
    "EMA Rejection": "Patterns",
    "Resistance Test": "Patterns",
    "Pullback": "Patterns",
    "Double Top/Bottom": "Patterns",
    "Range Filter": "RiskMgmt",
    "Combined Range Filter": "RiskMgmt",
}

RULE_DISPLAY_FA = {
    "Candle Strength 15m": "قدرت کندل ۱۵م",
    "Candle Strength 5m": "قدرت کندل ۵م",
    "EMA Trend 1h": "روند EMA ۱س",
    "EMA Trend 4h": "روند EMA ۴س",
    "RSI 30m": "RSI ۳۰م",
    "MACD 30m": "MACD ۳۰م",
    "Smart Pullback": "ورود هوشمند پولبک",
    "ADX": "ADX",
    "CCI Momentum": "CCI عبور از صفر",
    "SAR": "SAR",
    "Stochastic": "Stochastic cross",
    "EMA Rejection": "رد EMA",
    "Resistance Test": "تست مقاومت",
    "Pullback": "پولبک",
    "Double Top/Bottom": "Double Top/Bottom",
    "Range Filter": "فیلتر رنج",
    "Combined Range Filter": "فیلتر رنج ترکیبی",
}


# =====================================================================
#  EVALUATE ALL RULES
# =====================================================================
def evaluate_rules(
    symbol: str, direction: str, risk: str, risk_rules: dict,
    price_30m: float,
    open_15m: float, close_15m: float, high_15m: float, low_15m: float,
    open_5m: float, close_5m: float, high_5m: float, low_5m: float,
    open_1m: float, close_1m: float, high_1m: float, low_1m: float,
    ema21_30m: float, ema50_30m: float, ema8_30m: float,
    ema21_1h: float, ema50_1h: float,
    ema21_4h: float, ema50_4h: float, ema200_4h: float,
    macd_hist_30m: float, rsi_30m: float,
    vol_spike_factor: float, divergence_detected: bool,
    candles: list, prices_series_30m: list, closes_by_tf: dict,
    adx_value: float,
    range_filter_mode: str = "OR",
) -> Tuple[List[RuleResult], float, float]:
    diff = abs(ema21_30m - ema50_30m) / price_30m if price_30m and price_30m != 0 else 0
    rule_results = [
        rule_body_strength(open_15m, close_15m, high_15m, low_15m, risk_rules),
        rule_body_strength_5m(open_5m, close_5m, high_5m, low_5m, risk_rules),
        rule_trend_1h(ema21_1h, ema50_1h, direction),
        rule_trend_4h(ema21_4h, ema50_4h, ema200_4h, direction),
        rule_rsi(rsi_30m, direction, risk),
        rule_macd(macd_hist_30m, direction, risk),
        rule_smart_pullback_entry(price_30m, ema21_30m, rsi_30m, open_15m, close_15m, high_15m, low_15m, direction),
        rule_adx(candles, direction),
        rule_cci_momentum(candles, direction),
        rule_sar(candles, direction),
        rule_stochastic_momentum(candles, direction),
        rule_ema_rejection(prices_series_30m, ema21_30m),
        rule_resistance_test(prices_series_30m, ema50_30m),
        rule_pullback(prices_series_30m, direction),
        rule_double_top_bottom(prices_series_30m),
        rule_range_filter(ema21_30m, ema50_30m, price_30m),
        rule_combined_range_filter(diff, adx_value, direction, mode=range_filter_mode),
    ]
    weights = RISK_FACTORS.get(risk, {})
    active_results = [r for r in rule_results if not r.detail.startswith('SKIP')]
    passed_weight = sum(weights.get(RULE_GROUP_MAP.get(r.name, "Other"), 0) for r in active_results if r.passed)
    total_weight = sum(weights.get(RULE_GROUP_MAP.get(r.name, "Other"), 0) for r in active_results)
    return rule_results, passed_weight, total_weight


# =====================================================================
#  SIGNAL GENERATION
# =====================================================================
async def generate_signal(
    symbol: str, direction: str, prefer_risk: str,
    price_30m: float,
    open_15m: float, close_15m: float, high_15m: float, low_15m: float,
    open_5m: float, close_5m: float, high_5m: float, low_5m: float,
    open_1m: float, close_1m: float, high_1m: float, low_1m: float,
    ema21_30m: float, ema50_30m: float, ema8_30m: float,
    ema21_1h: float, ema50_1h: float,
    ema21_4h: float, ema50_4h: float, ema200_4h: float,
    macd_line_30m: float, hist_30m: float,
    rsi_30m: float, atr_val_30m: float,
    curr_vol: float, avg_vol_30m: float,
    divergence_detected: bool,
    candles: list, prices_series_30m: list, closes_by_tf: dict,
    scenario_id: str = None,
) -> Optional[dict]:
    import aiohttp
    time_str = tehran_time_str()
    scenario = SCENARIOS.get(scenario_id) if scenario_id else None

    if scenario and scenario.get("direction_only") and direction != scenario["direction_only"]:
        return None
    if scenario and scenario.get("symbols_list") and symbol not in scenario["symbols_list"]:
        return None

    if scenario:
        sc_cooldown = scenario["cooldown_seconds"]
        if scenario_id not in _last_signal_times:
            _last_signal_times[scenario_id] = {}
        last_ts = _last_signal_times[scenario_id].get(symbol, 0)
        now_ts = time.time()
        if now_ts - last_ts < sc_cooldown:
            return None

    adx, di_p, di_m = calculate_adx(candles)
    adx_val = adx or 0

    if scenario and scenario.get("extra_filters"):
        ef = scenario["extra_filters"]
        bs15 = abs(close_15m - open_15m) / max(high_15m - low_15m, 1e-8)
        if ef.get("min_body_strength") and bs15 < ef["min_body_strength"]:
            return None
        if ef.get("min_adx") and adx_val < ef["min_adx"]:
            return None
        if ef.get("require_di_align"):
            if direction == "LONG" and not (di_p > di_m):
                return None
            if direction == "SHORT" and not (di_m > di_p):
                return None
        if ef.get("require_close_extreme"):
            rng = max(high_15m - low_15m, 1e-12)
            close_pos = (close_15m - low_15m) / rng
            if direction == "LONG" and close_pos < ef.get("close_long_min", 0.82):
                return None
            if direction == "SHORT" and close_pos > ef.get("close_short_max", 0.18):
                return None

    # V3.1 RSI zone filter for S1 (Prometheus LONG)
    if scenario_id == 'S1' and rsi_30m is not None:
        if not (45 <= rsi_30m <= 65):
            return None

    if scenario:
        profile = scenario.get("rule_profile", "strict")
        internal_risk = "LOW" if profile == "strict" else "MEDIUM"
        range_mode = scenario.get("range_filter_mode", "OR")
    else:
        internal_risk = prefer_risk or "MEDIUM"
        range_mode = "OR"

    risk_rules = next((r["rules"] for r in RISK_LEVELS if r["key"] == internal_risk), RISK_LEVELS[1]["rules"])

    rule_results, passed_weight, total_weight = evaluate_rules(
        symbol=symbol, direction=direction, risk=internal_risk,
        risk_rules=risk_rules, price_30m=price_30m,
        open_15m=open_15m, close_15m=close_15m, high_15m=high_15m, low_15m=low_15m,
        open_5m=open_5m, close_5m=close_5m, high_5m=high_5m, low_5m=low_5m,
        open_1m=open_1m, close_1m=close_1m, high_1m=high_1m, low_1m=low_1m,
        ema21_30m=ema21_30m, ema50_30m=ema50_30m, ema8_30m=ema8_30m,
        ema21_1h=ema21_1h, ema50_1h=ema50_1h,
        ema21_4h=ema21_4h, ema50_4h=ema50_4h, ema200_4h=ema200_4h,
        macd_hist_30m=hist_30m, rsi_30m=rsi_30m,
        vol_spike_factor=1.0, divergence_detected=divergence_detected,
        candles=candles, prices_series_30m=prices_series_30m,
        closes_by_tf=closes_by_tf, adx_value=adx_val,
        range_filter_mode=range_mode,
    )

    strength_ratio = passed_weight / total_weight if total_weight > 0 else 0
    active_rule_results = [r for r in rule_results if not r.detail.startswith('SKIP')]
    passed_count = sum(1 for r in active_rule_results if r.passed)

    # SL = price * (1 - sl_pct), NO fixed TP
    if scenario:
        sl_pct = scenario.get('sl_pct', 0.040)
    else:
        sl_pct = 0.040
    if direction == "LONG":
        stop_loss = price_30m * (1 - sl_pct)
    else:
        stop_loss = price_30m * (1 + sl_pct)
    take_profit = 0.0

    if scenario:
        sc_wt = scenario["weight_threshold"]
        sc_mr = scenario["min_passed_rules"]
        status = "SIGNAL" if (passed_weight >= total_weight * sc_wt and passed_count >= sc_mr) else "NO_SIGNAL"
    else:
        status = "SIGNAL" if passed_weight >= total_weight * 0.50 else "NO_SIGNAL"

    log_prefix = f"[{scenario_id}]" if scenario else "[Default]"
    total_active = len(active_rule_results)
    logger.info(f"{log_prefix} {symbol} {direction} | weight={passed_weight}/{total_weight} | rules={passed_count}/{total_active} | {status}")

    signal_dict = {
        "symbol": symbol, "direction": direction, "status": status,
        "strength": passed_weight / total_weight if status == "SIGNAL" else None,
        "price": price_30m, "stop_loss": stop_loss, "take_profit": take_profit,
        "time": time_str, "passed_weight": passed_weight, "total_weight": total_weight,
        "passed_rules_count": passed_count, "total_rules": total_active,
    }
    if scenario:
        signal_dict["scenario_id"] = scenario_id
        signal_dict["scenario_name"] = f"{scenario['name_fa']} ({scenario['name_en']})"

    if status == "SIGNAL":
        if scenario and scenario_id:
            if scenario_id not in _last_signal_times:
                _last_signal_times[scenario_id] = {}
            _last_signal_times[scenario_id][symbol] = time.time()

        # CSV write happens once in bot.save_signal (with trail params + telegram_message_id)
        from .telegram_util import send_telegram, fmt_price
        dir_emoji = "🟢 LONG" if direction == "LONG" else "🔴 SHORT"
        name_fa = scenario.get("name_fa", "") if scenario else ""
        name_en = scenario.get("name_en", "") if scenario else ""
        behavior = scenario.get("behavior_fa", "") if scenario else ""
        titan_desc = scenario.get("titan_desc_fa", "") if scenario else ""
        cooldown_h = (scenario.get("cooldown_seconds", 0) / 3600) if scenario else 0
        wt_pct = int(round((scenario.get("weight_threshold", 0.5) if scenario else 0.5) * 100))
        min_rules = scenario.get("min_passed_rules", 0) if scenario else 0
        trail_act = scenario.get("trail_activate", 0.003) if scenario else 0.003
        trail_lock_val = scenario.get("trail_lock", 0.90) if scenario else 0.90
        passed_lines, failed_lines = [], []
        for r in active_rule_results:
            title = RULE_DISPLAY_FA.get(r.name, r.name)
            line = f"{'✅' if r.passed else '❌'} {title} → {r.detail}"
            (passed_lines if r.passed else failed_lines).append(line)
        header = (
            f"──────────────────\n"
            f"🏆 <b>سیگنال جدید</b>\n"
            f"سناریو: {name_fa} ({name_en})\n"
            f"🎭 {behavior}\n"
        )
        if titan_desc:
            header += f"📖 {titan_desc}\n"
        header += (
            f"🎯 SL={sl_pct*100:.1f}% | تریل از {trail_act*100:.1f}% | قفل {trail_lock_val*100:.0f}%\n"
            f"آستانه وزن≥{wt_pct}% | حداقل قانون≥{min_rules} | کول‌داون={cooldown_h:.0f}س\n"
            f"──────────────────\n"
            f"📊 <b>{symbol}</b> | {dir_emoji}\n"
            f"ورود: <code>{fmt_price(price_30m, symbol)}</code>\n"
            f"استاپ اولیه: <code>{fmt_price(stop_loss, symbol)}</code>\n"
            f"خروج: تریلینگ (بدون TP ثابت)\n"
            f"زمان: {time_str}\n"
            f"───────────\n"
            f"📋 وزن <b>{passed_weight}/{total_weight}</b> | قوانین <b>{passed_count}/{total_active}</b>\n"
        )
        body = "\n".join(passed_lines[:12]) if passed_lines else "—"
        if len(passed_lines) > 12:
            body += f"\n… +{len(passed_lines)-12} قانون پاس دیگر"
        if failed_lines:
            body += f"\n❌ ردشده ({len(failed_lines)}):\n" + "\n".join(failed_lines[:8])
        _ok, tg_msg_id = await send_telegram(header + body)
        if tg_msg_id is not None:
            signal_dict["telegram_message_id"] = tg_msg_id
        # short pause to reduce Telegram 429 without long CI sleeps
        await asyncio.sleep(0.9)

    return signal_dict


async def generate_signal_scenario(scenario_id: str, **kwargs) -> Optional[dict]:
    return await generate_signal(scenario_id=scenario_id, prefer_risk="LOW", **kwargs)
