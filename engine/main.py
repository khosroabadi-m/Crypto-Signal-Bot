"""engine/main.py — Master Orchestrator (v25)

اجرا از GitHub Actions هر ۳۰ دقیقه.

این فایل یک **orchestrator** است، نه یک موتور تکی. برای هر
ACTIVE_PROFILES موتور مربوطه را (بدون تغییر در قوانین) صدا می‌زند و
خروجی را با profile_id در signals.json می‌نویسد.

═══════════════════════════════════════════════════════════════════════════
  قانون طلایی: فایل‌های زیر هرگز تغییر نمی‌کنند:
    pentasignal/scenarios.py, pentasignal/engine.py, pentasignal/settings.py
    titansignal/rules.py, titansignal/config.py
    khosro_analyzer/smc.py, khosro_analyzer/config.py
    khosro_runner.py (منطق Khosro)
  این فایل فقط آداپتور و فراخوانی است.
═══════════════════════════════════════════════════════════════════════════

Flow:
  1. همه‌ی کندل‌ها را یک‌بار از Binance بگیر (30m, 1h, 4h, 15m, 5m, 1m)
  2. برای هر (profile_id, symbol):
     - KHOSRO_SMC → khosro_runner.run_khosro(data)
     - PENTA_*   → pentasignal Engine.detect_new
     - TITAN_*   → titansignal generate_signal
  3. همه‌ی سیگنال‌ها را با profile_id در signals.json بنویسد
"""
import os, sys, json, time, asyncio, logging, traceback
from pathlib import Path
from datetime import datetime, timezone, timedelta

# اضافه‌کردن مسیر engine/ به sys.path
ENGINE_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(ENGINE_DIR))

import profiles
import aiohttp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("orchestrator")

# ─── Symbols ───────────────────────────────────────────────────────────────
# Binance USDT format (for Khosro + data fetching)
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
# Dash format (for Penta + Titan engines — their original format)
SYMBOLS_DASH = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT", "XRP-USDT"]
BINANCE_TO_DASH = dict(zip(SYMBOLS, SYMBOLS_DASH))
DASH_TO_BINANCE = dict(zip(SYMBOLS_DASH, SYMBOLS))

# Binance data API base — always ensure /api/v3 is included
_base = os.getenv("BINANCE_BASE", "https://data-api.binance.vision")
if "/api/v3" not in _base:
    _base = _base.rstrip("/") + "/api/v3"
BINANCE_REST = _base

# Tehran timezone (UTC+3:30)
TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30))


# ============================================================================
# ۱) Data fetchers — همه‌ی کندل‌ها را یک‌بار از Binance بگیر
# ============================================================================
async def fetch_klines(symbol: str, interval: str, limit: int = 200) -> list:
    """Fetch klines from Binance data-api (geo-unblocked).

    Hardcoded URL (same as khosro_runner.py) — env var BINANCE_BASE
    may strip /api/v3, so we don't rely on it.
    """
    url = f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    log.warning(f"  HTTP {r.status} for {symbol} {interval}")
                    return []
                raw = await r.json()
                # Format: [openTime(ms), open, high, low, close, volume, closeTime(ms), ...]
                candles = []
                for k in raw:
                    c = {
                        # shorthand keys (for Penta + Khosro)
                        # t در ثانیه (مثل KuCoin) — PentaSignal با ثانیه کار می‌کند
                        "t": int(k[0]) // 1000,
                        "o": float(k[1]), "h": float(k[2]),
                        "l": float(k[3]), "c": float(k[4]), "v": float(k[5]),
                        # full keys (for Titan)
                        "openTime": int(k[0]), "open": float(k[1]), "high": float(k[2]),
                        "low": float(k[3]), "close": float(k[4]), "volume": float(k[5]),
                        "closeTime": int(k[6]) // 1000,  # ثانیه
                    }
                    candles.append(c)
                return candles
    except Exception as e:
        log.error(f"  fetch_klines {symbol} {interval}: {e}")
        return []


async def fetch_all_data() -> dict:
    """Fetch ALL candle data ONCE for all symbols × all timeframes.

    Returns: {symbol_binance: {"30m": [...], "1h": [...], "4h": [...], "15m": [...], "5m": [...], "1m": [...]}}
    """
    timeframes = ["30m", "1h", "4h", "15m", "5m", "1m"]
    data = {}
    log.info("📡 Fetching all candle data from Binance...")
    for sym in SYMBOLS:
        data[sym] = {}
        for tf in timeframes:
            data[sym][tf] = await fetch_klines(sym, tf, 300 if tf == "1m" else 200)
            log.info(f"  {sym} {tf}: {len(data[sym][tf])} candles")
    log.info(f"✅ Data fetched for {len(data)} symbols × {len(timeframes)} timeframes")
    return data


# ============================================================================
# ۲) Khosro SMC adapter (via khosro_runner)
# ============================================================================
async def run_khosro_profile(data: dict) -> dict:
    """KHOSRO_SMC — uses khosro_runner.run_khosro(data) without modification.

    The khosro_runner.py is the original engine/main.py logic, moved to a
    separate file. Its run_khosro(data) function takes pre-fetched data
    and returns signals with profile_id="KHOSRO_SMC".
    """
    try:
        from khosro_runner import run_khosro
        signals = await run_khosro(data)
        # درج profile_id (در صورت نبود)
        for k, s in signals.items():
            s.setdefault("profile_id", "KHOSRO_SMC")
        log.info(f"✅ KHOSRO_SMC: {len(signals)} signals")
        return signals
    except Exception as e:
        log.error(f"❌ KHOSRO_SMC failed: {e}")
        log.error(traceback.format_exc())
        return {}


# ============================================================================
# ۳) PentaSignal adapter — uses Engine.detect_new without modification
# ============================================================================
async def run_penta_all(data: dict) -> dict:
    """PENTA_F1-F5 — Engine.detect_new را یک‌بار صدا می‌زند و نتایج را بر اساس
    scenario_id تقسیم می‌کند (Fix 5: به‌جای ۵ بار detect_new).

    Returns: dict با کلیدهای {SYMBOL}__{PENTA_F*} = {signal dict with profile_id}
    """
    signals_out = {}
    try:
        from pentasignal.scenarios import SCENARIOS, MarketContext, ACTIVE_SCENARIOS
        from pentasignal.engine import Engine

        # ساخت MarketContext با داده Binance
        candles30 = {}
        for sym_b in SYMBOLS:
            sym_d = BINANCE_TO_DASH[sym_b]
            candles30[sym_d] = data.get(sym_b, {}).get("30m", [])
        btc_4h = data.get("BTCUSDT", {}).get("4h", [])
        ctx = MarketContext(candles30=candles30, btc_4h=btc_4h)

        # ساخت Engine (light=True)
        engine = Engine(light=True)

        # زمان فعلی Tehran
        now_tehran = datetime.now(TEHRAN_TZ)

        # Fix 5: یک‌بار detect_new صدا بزن → همه‌ی سناریوها در یک پاس
        log.info("  Calling detect_new (single pass for all F1-F5)...")
        all_new_logs = engine.detect_new(now_tehran, ctx)
        log.info(f"  detect_new returned {len(all_new_logs)} raw signals")

        # تقسیم بر اساس scenario_id و ساخت کلیدهای مرکب
        # MsgLog objects have: .meta (dict with symbol/scenario_id/direction)
        #                      .signal_id (str)
        #                      .kind (str — SIGNAL/BE_ARMED/SETTLE/REPORT)
        # We only care about kind="SIGNAL"
        for log_entry in all_new_logs:
            kind = getattr(log_entry, "kind", "")
            if kind != "SIGNAL":
                continue
            meta = getattr(log_entry, "meta", {})
            sid = meta.get("scenario_id", "")
            # پیدا کردن profile_id متناظر
            profile_id = None
            for pid, p in profiles.PENTA_PROFILES.items():
                if p.get("scenario_id") == sid:
                    profile_id = pid
                    break
            if not profile_id:
                continue
            sym_d = meta.get("symbol", "")
            sym_b = DASH_TO_BINANCE.get(sym_d, sym_d.replace("-", ""))
            # داده‌ی کامل سیگنال را از store بگیر (اگر در دسترس باشد)
            entry = 0.0
            sl_val = 0.0
            tp_val = 0.0
            score_val = 0.0
            risk_val = "MEDIUM"
            try:
                from pentasignal import store as penta_store
                sig_id = getattr(log_entry, "signal_id", "")
                if sig_id:
                    row = penta_store.get_signal(sig_id)
                    if row:
                        entry = float(row.get("entry", 0) or row.get("entry_hint", 0) or 0)
                        sl_val = float(row.get("stop_loss", 0) or 0)
                        tp_val = float(row.get("tp", 0) or row.get("take_profit", 0) or 0)
                        score_val = float(row.get("score", 0) or 0)
                        risk_val = row.get("risk", "MEDIUM")
            except Exception:
                pass
            # fallback: اگر store در دسترس نبود، حداقل direction را از meta بگیر
            direction = meta.get("direction", "")
            if entry <= 0:
                # اگر entry از store نیامد، این سیگنال را رد کن
                # (چون بدون entry نمی‌توان در ledger ذخیره کرد)
                log.info(f"  ⚠️ {profile_id} {sym_b}: SIGNAL but no entry price from store — skipping")
                continue
            sc = SCENARIOS.get(sid, {})
            signals_out[f"{sym_b}__{profile_id}"] = {
                "profile_id": profile_id,
                "symbol": sym_b,
                "direction": direction,
                "entry": entry,
                "stopLoss": sl_val,
                "takeProfit": tp_val,
                "tp1": tp_val,
                "tp2": tp_val,
                "sl": sl_val,
                "rr": float(sc.get("rr", 2.0) or 2.0),
                "score": score_val,
                "risk": risk_val,
                "status": "OPEN",
                "components": [],
                "issuedAt": now_tehran.strftime("%Y-%m-%d %H:%M:%S"),
                "issuedAtEpoch": int(time.time()),
                "timestamp": int(time.time()),
                # Penta exit params (from UNCHANGED scenarios.py)
                "exit_mode": sc.get("exit_mode", "FIXED"),
            }
            log.info(f"  ✅ {profile_id} {sym_b} {direction}: entry={entry}")

        # تفکیک بر اساس profile_id برای لاگ
        for pid in profiles.PENTA_PROFILES:
            cnt = sum(1 for k in signals_out if k.endswith(pid))
            log.info(f"  ✅ {pid} ({profiles.get_profile(pid).get('scenario_id')}): {cnt} signals")
    except ImportError as e:
        log.warning(f"  Penta modules not available ({e})")
    except Exception as e:
        log.error(f"  Penta failed: {e}")
        log.error(traceback.format_exc())
    return signals_out


# ============================================================================
# ۴) Titan Signal adapter — uses generate_signal without modification
# ============================================================================
async def run_titan_profile(profile_id: str, data: dict) -> dict:
    """TITAN_S1, TITAN_S3, TITAN_B1 — uses titansignal generate_signal.

    Titan's generate_signal expects:
    - Multiple timeframes (30m, 15m, 5m, 1m, 1h, 4h)
    - Pre-computed indicators (EMA, MACD, RSI, ATR, ADX)
    - Symbol in -USDT format

    Fix 4: avg_vol_30m now calculated from real candle data.
    Fix 2: trailing params (trail_activate, trail_lock, max_hold_candles, sl_pct)
           are added to the signal dict so the Wispbyte server can use them
           for Titan-specific exit logic.
    """
    scenario_id = profiles.get_profile(profile_id).get("scenario_id", "S1")
    signals_out = {}
    try:
        from titansignal.rules import generate_signal
        from titansignal.config import SCENARIOS as TITAN_SCENARIOS
        from titansignal.indicators import (
            calculate_ema, calculate_rsi, calculate_macd, calculate_atr,
        )
        sc = TITAN_SCENARIOS.get(scenario_id)
        if not sc:
            log.warning(f"  Titan {scenario_id}: scenario not found")
            return {}

        for sym_b in SYMBOLS:
            sym_d = BINANCE_TO_DASH[sym_b]
            sym_data = data.get(sym_b, {})
            candles_30m = sym_data.get("30m", [])
            candles_15m = sym_data.get("15m", [])
            candles_5m = sym_data.get("5m", [])
            candles_1m = sym_data.get("1m", [])
            candles_1h = sym_data.get("1h", [])
            candles_4h = sym_data.get("4h", [])
            if len(candles_30m) < 50:
                continue

            # استخراج closes
            closes_30 = [c["c"] for c in candles_30m]
            closes_1h = [c["c"] for c in candles_1h]
            closes_4h = [c["c"] for c in candles_4h]

            # محاسبه اندیکاتورها (بدون تغییر در منطق Titan)
            ema21_30m = calculate_ema(closes_30, 21) if len(closes_30) >= 21 else None
            ema50_30m = calculate_ema(closes_30, 50) if len(closes_30) >= 50 else None
            ema8_30m = calculate_ema(closes_30, 8) if len(closes_30) >= 8 else None
            ema21_1h = calculate_ema(closes_1h, 21) if len(closes_1h) >= 21 else None
            ema50_1h = calculate_ema(closes_1h, 50) if len(closes_1h) >= 50 else None
            ema21_4h = calculate_ema(closes_4h, 21) if len(closes_4h) >= 21 else None
            ema50_4h = calculate_ema(closes_4h, 50) if len(closes_4h) >= 50 else None
            ema200_4h = calculate_ema(closes_4h, 200) if len(closes_4h) >= 200 else None

            rsi_30m = calculate_rsi(closes_30) if len(closes_30) >= 14 else None
            macd_30m = calculate_macd(closes_30) if len(closes_30) >= 26 else None
            atr_30m = calculate_atr(candles_30m) if len(candles_30m) >= 14 else None

            # Fix 4: محاسبه avg_vol_30m واقعی از داده‌ی کندل‌ها
            vols_30m = [c.get("v", 0) for c in candles_30m[-20:]]  # میانگین ۲۰ کندل اخیر
            avg_vol_30m = sum(vols_30m) / len(vols_30m) if vols_30m else 0.0

            # کندل‌های اخیر برای هر تایم‌فریم
            c15 = candles_15m[-1] if candles_15m else {}
            c5m = candles_5m[-1] if candles_5m else {}
            c1m = candles_1m[-1] if candles_1m else {}

            price = candles_30m[-1]["c"] if candles_30m else 0

            # تعیین direction بر اساس scenario
            direction = sc.get("direction_only", "LONG")

            try:
                signal = await generate_signal(
                    symbol=sym_d,
                    direction=direction,
                    prefer_risk="LOW",
                    price_30m=price,
                    open_15m=c15.get("o", 0), close_15m=c15.get("c", 0),
                    high_15m=c15.get("h", 0), low_15m=c15.get("l", 0),
                    open_5m=c5m.get("o", 0), close_5m=c5m.get("c", 0),
                    high_5m=c5m.get("h", 0), low_5m=c5m.get("l", 0),
                    open_1m=c1m.get("o", 0), close_1m=c1m.get("c", 0),
                    high_1m=c1m.get("h", 0), low_1m=c1m.get("l", 0),
                    ema21_30m=ema21_30m, ema50_30m=ema50_30m, ema8_30m=ema8_30m,
                    ema21_1h=ema21_1h, ema50_1h=ema50_1h,
                    ema21_4h=ema21_4h, ema50_4h=ema50_4h, ema200_4h=ema200_4h,
                    macd_line_30m=macd_30m.get("macd") if macd_30m else None,
                    hist_30m=macd_30m.get("histogram") if macd_30m else None,
                    rsi_30m=rsi_30m,
                    atr_val_30m=atr_30m or 0.0,
                    curr_vol=candles_30m[-1].get("v", 0.0),
                    avg_vol_30m=avg_vol_30m,  # Fix 4: مقدار واقعی
                    divergence_detected=False,
                    candles=candles_30m,
                    prices_series_30m=closes_30[-120:],
                    closes_by_tf={
                        "30m": candles_30m, "1h": candles_1h, "4h": candles_4h,
                        "15m": candles_15m, "5m": candles_5m, "1m": candles_1m,
                    },
                    scenario_id=scenario_id,
                )
                if signal and signal.get("status") == "SIGNAL":
                    signals_out[f"{sym_b}__{profile_id}"] = {
                        "profile_id": profile_id,
                        "symbol": sym_b,
                        "direction": signal.get("direction", direction),
                        "entry": float(signal.get("price", 0) or 0),
                        "stopLoss": float(signal.get("stop_loss", 0) or 0),
                        "takeProfit": float(signal.get("take_profit", 0) or 0),
                        "tp1": float(signal.get("take_profit", 0) or 0),
                        "tp2": float(signal.get("take_profit", 0) or 0),
                        "sl": float(signal.get("stop_loss", 0) or 0),
                        "rr": float(sc.get("rr", 2.0) or 2.0),
                        "score": float(signal.get("score", 0) or 0),
                        "risk": signal.get("risk", "LOW"),
                        "status": "OPEN",
                        "components": [],
                        "issuedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "issuedAtEpoch": int(time.time()),
                        "timestamp": int(time.time()),
                        # Fix 2: Titan exit params (from UNCHANGED config.py)
                        "sl_pct": float(sc.get("sl_pct", 0.040)),
                        "trail_activate": float(sc.get("trail_activate", 0.003)),
                        "trail_lock": float(sc.get("trail_lock", 0.90)),
                        "max_hold_candles": int(sc.get("max_hold_candles", 72)),
                        "scenario_id": scenario_id,
                    }
                    log.info(f"  ✅ {profile_id} {sym_b} {signal['direction']}: SIGNAL!")
            except Exception as e:
                log.debug(f"  {profile_id} {sym_b}: {e}")
        log.info(f"✅ {profile_id} ({scenario_id}): {len(signals_out)} signals")
    except ImportError as e:
        log.warning(f"  Titan {profile_id}: module not available ({e})")
    except Exception as e:
        log.error(f"  Titan {profile_id}: {e}")
        log.error(traceback.format_exc())
    return signals_out


# ============================================================================
# ۵) Master Orchestrator
# ============================================================================
async def run_all_profiles():
    """اجرای همه‌ی پروفایل‌های فعال.

    Flow:
    1. همه‌ی کندل‌ها را یک‌بار از Binance بگیر
    2. هر موتور را با داده از قبل fetch شده صدا بزن
    3. همه‌ی سیگنال‌ها را با profile_id در signals.json بنویسد
    """
    log.info("=" * 60)
    log.info("🚀 Khosro Multi-Profile Signal Orchestrator v25")
    log.info(f"   Active profiles: {len(profiles.ACTIVE_PROFILES)}")
    log.info(f"   Symbols: {len(SYMBOLS)}")
    log.info("=" * 60)

    # ─── Step 1: Fetch all data ONCE ─────────────────────────────────────
    data = await fetch_all_data()

    all_signals = {}
    profile_counts = {}

    # ─── Step 2: Run each engine group ───────────────────────────────────
    # Group 1: Khosro SMC
    log.info("\n" + "─" * 60)
    log.info("📋 Running KHOSRO_SMC (Khosro Confluence Engine + AI Committee)")
    khosro_sigs = await run_khosro_profile(data)
    all_signals.update(khosro_sigs)
    profile_counts["KHOSRO_SMC"] = len(khosro_sigs)

    # Group 2: PentaSignal F1-F5 (Fix 5: single detect_new call)
    log.info("\n" + "─" * 60)
    log.info("📋 Running PentaSignal (single detect_new for F1-F5)")
    penta_sigs = await run_penta_all(data)
    all_signals.update(penta_sigs)
    for pid in profiles.PENTA_PROFILES:
        cnt = sum(1 for k in penta_sigs if k.endswith(pid))
        profile_counts[pid] = cnt

    # Group 3: Titan Signal S1, S3, B1
    log.info("\n" + "─" * 60)
    log.info("📋 Running Titan Signal profiles (S1, S3, B1)")
    for pid in profiles.TITAN_PROFILES:
        log.info(f"  → {pid} ({profiles.get_profile_name_fa(pid)})")
        titan_sigs = await run_titan_profile(pid, data)
        all_signals.update(titan_sigs)
        profile_counts[pid] = len(titan_sigs)

    # ─── Step 3: Write signals.json ──────────────────────────────────────
    out_path = ENGINE_DIR / "signals.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_signals, f, indent=2, ensure_ascii=False)

    log.info("\n" + "=" * 60)
    log.info(f"💾 signals.json: {len(all_signals)} signals")
    for pid, cnt in profile_counts.items():
        icon = "✅" if cnt > 0 else "⚠️"
        log.info(f"   {icon} {pid}: {cnt} signals")
    # تست پذیرش: آیا بیش از یک profile سیگنال تولید کرده؟
    profiles_with_signals = sum(1 for c in profile_counts.values() if c > 0)
    if profiles_with_signals > 1:
        log.info(f"✅ Acceptance test PASSED: {profiles_with_signals} profiles produced signals")
    else:
        log.warning(f"⚠️ Acceptance test: only {profiles_with_signals} profile(s) produced signals")
    log.info("=" * 60)


def main():
    asyncio.run(run_all_profiles())


if __name__ == "__main__":
    main()
