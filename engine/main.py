"""
Khosro Signal Engine v12 (Python standalone)

Mوتور سبک سیگنال‌دهی کریپتو بر اساس KhosroSignalAnalyzerBot v11.3.
اجرا در GitHub Actions هر ۳۰ دقیقه.

خروجی: signals.json (commit به گیت‌هاب) + ارسال تلگرام

وابستگی‌ها:
    pip install aiohttp requests
"""
import os
import json
import time
import asyncio
import logging
from datetime import datetime, timezone

import aiohttp

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("engine")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
TIMEFRAMES = ["5m", "15m", "30m", "1h", "4h"]

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# AI Committee (اختیاری) — فقط Groq به‌عنوان نمونه. بدون کلید، rule-based.
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

# Thresholds (میراث v11.3)
LONG_SIGNAL_SCORE = 62
SHORT_SIGNAL_SCORE = 72
SHORT_HTF_MIN = 0.60
LONG_HTF_MIN = 0.35
MAX_ATR_PCT = 0.06
STOP_MAX_PCT = 0.030
STOP_MIN_PCT = 0.005
STRUCT_STOP_MAX_ATR = 2.5
SIGNAL_MAX_AGE_HOURS = 96

RISK_PARAMS = {
    "LOW": {"atr_sl": 1.4, "rr": 2.4},
    "MEDIUM": {"atr_sl": 1.6, "rr": 2.1},
    "HIGH": {"atr_sl": 1.9, "rr": 2.0},
}

WEIGHTS = {
    "ai_committee": 25, "order_flow": 14, "volume_profile": 11, "sweep": 10,
    "liquidity": 8, "fvg": 7, "fibonacci": 6, "supply_demand": 5,
    "moving_average": 5, "macd": 4, "rsi": 3, "pattern": 2,
}

BINANCE_REST = "https://api.binance.com/api/v3"

# ---------------------------------------------------------------------------
# Indicators (مهاجرت از v11.3 indicators.py)
# ---------------------------------------------------------------------------
def ema_series(prices, period):
    if period <= 0 or len(prices) < period:
        return [None] * len(prices)
    k = 2.0 / (period + 1.0)
    out = [None] * (period - 1)
    value = sum(prices[:period]) / period
    out.append(value)
    for p in prices[period:]:
        value = p * k + value * (1 - k)
        out.append(value)
    return out

def calc_ema(prices, period):
    s = ema_series(prices, period)
    return s[-1] if s else None

def calc_rsi(prices, period=14):
    if len(prices) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(prices)):
        d = prices[i] - prices[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    if avg_gain == 0:
        return 0.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)

def calc_macd(prices, fast=12, slow=26, sig=9):
    if len(prices) < slow + sig:
        return {"macd": None, "signal": None, "histogram": None}
    fs = ema_series(prices, fast)
    ss = ema_series(prices, slow)
    macd = [None if a is None or b is None else a - b for a, b in zip(fs, ss)]
    valid = [x for x in macd if x is not None]
    sig_v = ema_series(valid, sig)
    signal_value = sig_v[-1] if sig_v else None
    line_value = valid[-1] if valid else None
    hist = line_value - signal_value if line_value is not None and signal_value is not None else None
    return {"macd": line_value, "signal": signal_value, "histogram": hist}

def calc_atr(candles, period=14):
    if len(candles) < period + 1:
        return None
    tr = []
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i - 1]
        tr.append(max(c["h"] - c["l"], abs(c["h"] - p["c"]), abs(c["l"] - p["c"])))
    atr = sum(tr[:period]) / period
    for x in tr[period:]:
        atr = (atr * (period - 1) + x) / period
    return atr

def calc_adx(candles, period=14):
    if len(candles) < period * 2 + 1:
        return None, None, None
    trs, plus_dm, minus_dm = [], [], []
    for i in range(1, len(candles)):
        cur, prev = candles[i], candles[i - 1]
        up = cur["h"] - prev["h"]
        down = prev["l"] - cur["l"]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        trs.append(max(cur["h"] - cur["l"], abs(cur["h"] - prev["c"]), abs(cur["l"] - prev["c"])))
    atr = sum(trs[:period]) / period
    p = sum(plus_dm[:period]) / period
    m = sum(minus_dm[:period]) / period
    dx = []
    for i in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[i]) / period
        p = (p * (period - 1) + plus_dm[i]) / period
        m = (m * (period - 1) + minus_dm[i]) / period
        pdi = 100.0 * p / atr if atr else 0
        mdi = 100.0 * m / atr if atr else 0
        dx.append(100.0 * abs(pdi - mdi) / (pdi + mdi) if pdi + mdi else 0)
    if len(dx) < period:
        return None, None, None
    adx = sum(dx[:period]) / period
    for x in dx[period:]:
        adx = (adx * (period - 1) + x) / period
    return adx, 100.0 * p / atr if atr else 0, 100.0 * m / atr if atr else 0

def find_pivots(candles, left=2, right=2):
    highs, lows = [], []
    for i in range(left, len(candles) - right):
        h, l = candles[i]["h"], candles[i]["l"]
        if all(candles[j]["h"] <= h for j in range(i - left, i + right + 1) if j != i):
            highs.append((i, h))
        if all(candles[j]["l"] >= l for j in range(i - left, i + right + 1) if j != i):
            lows.append((i, l))
    return highs, lows

# ---------------------------------------------------------------------------
# Binance REST fetch
# ---------------------------------------------------------------------------
async def fetch_klines(session, symbol, interval, limit=200):
    """Fetch klines from Binance REST."""
    url = f"{BINANCE_REST}/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        async with session.get(url, timeout=15) as r:
            if r.status != 200:
                log.warning(f"Binance {symbol} {interval} HTTP {r.status}")
                return []
            raw = await r.json()
            return [{
                "t": int(k[0] // 1000),
                "o": float(k[1]), "h": float(k[2]),
                "l": float(k[3]), "c": float(k[4]), "v": float(k[5]),
            } for k in raw]
    except Exception as e:
        log.error(f"fetch_klines {symbol} {interval} error: {e}")
        return []

async def fetch_symbol_data(session, symbol):
    out = {}
    tasks = [fetch_klines(session, symbol, tf, 200) for tf in TIMEFRAMES]
    results = await asyncio.gather(*tasks)
    for tf, candles in zip(TIMEFRAMES, results):
        if candles:
            out[tf] = candles
    return out

# ---------------------------------------------------------------------------
# SMC components (نسخه سبک — فقط چند مؤلفه کلیدی برای اجرای سریع)
# ---------------------------------------------------------------------------
NEUTRAL = 0.35

def order_flow_score(candles, direction, lookback=12):
    if len(candles) < lookback:
        return 0.5, "insufficient"
    win = candles[-lookback:]
    buy = sum(c["v"] for c in win if c["c"] >= c["o"])
    sell = sum(c["v"] for c in win if c["c"] < c["o"])
    total = buy + sell
    delta = (buy - sell) / total if total else 0
    cp_list = [(c["c"] - c["l"]) / max(c["h"] - c["l"], 1e-12) for c in win]
    avg_cp = sum(cp_list) / len(cp_list)
    flow = 0.5 + 0.30 * delta + 0.40 * (avg_cp - 0.5) * 2 * 0.5
    flow = max(0, min(1, flow))
    if direction == "SHORT":
        flow = 1 - flow
    return flow, f"delta={delta:+.2f} cp={avg_cp:.2f}"

def volume_profile_score(candles, direction, lookback=120, bins=24):
    if len(candles) < 60:
        return 0.5, "insufficient"
    win = candles[-lookback:]
    lo = min(c["l"] for c in win)
    hi = max(c["h"] for c in win)
    if hi <= lo:
        return 0.5, "flat"
    step = (hi - lo) / bins
    vols = [0.0] * bins
    for c in win:
        tp = (c["h"] + c["l"] + c["c"]) / 3
        idx = min(bins - 1, max(0, int((tp - lo) / step)))
        vols[idx] += c["v"]
    poc_idx = max(range(bins), key=lambda i: vols[i])
    poc = lo + (poc_idx + 0.5) * step
    price = win[-1]["c"]
    if direction == "LONG":
        score = 0.95 if price > poc else 0.45 if price >= lo + (poc_idx - 1) * step else 0.15
    else:
        score = 0.95 if price < poc else 0.45 if price <= lo + (poc_idx + 1) * step else 0.15
    return score, f"POC={poc:.4g}"

def rsi_score(candles, direction):
    closes = [c["c"] for c in candles]
    rsi = calc_rsi(closes)
    if rsi is None:
        return 0.5, "no RSI"
    if direction == "LONG":
        s = 0.55 if 50 <= rsi <= 68 else 0.35 if 45 <= rsi < 50 else 0.15
    else:
        s = 0.55 if 32 <= rsi <= 50 else 0.35 if 50 < rsi <= 55 else 0.15
    return s, f"RSI={rsi:.1f}"

def macd_score(candles, direction):
    closes = [c["c"] for c in candles]
    m = calc_macd(closes)
    if m["histogram"] is None:
        return 0.5, "no MACD"
    aligned = m["histogram"] > 0 if direction == "LONG" else m["histogram"] < 0
    return (0.7 if aligned else 0.2), f"hist={m['histogram']:.4g}"

def moving_average_score(candles, direction):
    closes = [c["c"] for c in candles]
    if len(closes) < 60:
        return 0.5, "insufficient"
    e21 = calc_ema(closes, 21)
    e50 = calc_ema(closes, 50)
    if e21 is None or e50 is None:
        return 0.5, "no EMA"
    price = closes[-1]
    s = 0
    if direction == "LONG":
        if e21 > e50: s += 0.45
        if price > e21: s += 0.30
        if e21 > calc_ema(closes[:-7], 21) if len(closes) > 7 else False: s += 0.25
    else:
        if e21 < e50: s += 0.45
        if price < e21: s += 0.30
        if e21 < calc_ema(closes[:-7], 21) if len(closes) > 7 else False: s += 0.25
    return s, f"EMA21={e21:.4g} EMA50={e50:.4g}"

def sweep_score(candles, direction):
    if len(candles) < 30:
        return NEUTRAL, "insufficient"
    highs, lows = find_pivots(candles[-90:])
    last = candles[-8:]
    best = 0
    found = "none"
    if direction == "LONG" and lows:
        for lvl in [l[1] for l in lows[:-1]]:
            for j, c in enumerate(last):
                if c["l"] < lvl and c["c"] > lvl:
                    depth = (lvl - c["l"]) / max(lvl, 1e-12)
                    recency = 1 - (len(last) - 1 - j) / 16
                    s = min(1, 0.55 + min(depth * 40, 0.25) + recency * 0.20)
                    if s > best: best, found = s, f"swept low {lvl:.4g}"
    elif direction == "SHORT" and highs:
        for lvl in [h[1] for h in highs[:-1]]:
            for j, c in enumerate(last):
                if c["h"] > lvl and c["c"] < lvl:
                    depth = (c["h"] - lvl) / max(lvl, 1e-12)
                    recency = 1 - (len(last) - 1 - j) / 16
                    s = min(1, 0.55 + min(depth * 40, 0.25) + recency * 0.20)
                    if s > best: best, found = s, f"swept high {lvl:.4g}"
    return (best or NEUTRAL), found

# ---------------------------------------------------------------------------
# Hard gates
# ---------------------------------------------------------------------------
def tf_state(candles, direction, ef=21, es=55):
    if len(candles) < es + 5:
        return 0, "insufficient"
    closes = [c["c"] for c in candles]
    ema_fast = calc_ema(closes, ef) or 0
    ema_slow = calc_ema(closes, es) or 0
    price = closes[-1]
    adx, pdi, mdi = calc_adx(candles)
    s = 0
    if direction == "LONG":
        if ema_fast > ema_slow: s += 0.5
        if price > ema_fast: s += 0.35
        if pdi and mdi and pdi > mdi: s += 0.15
    else:
        if ema_fast < ema_slow: s += 0.5
        if price < ema_fast: s += 0.35
        if pdi and mdi and mdi > pdi: s += 0.15
    return s, f"ema{ef}={ema_fast:.4g} ema{es}={ema_slow:.4g}"

# ---------------------------------------------------------------------------
# Stop pipeline (mirror v11.3 _apply_stop_caps)
# ---------------------------------------------------------------------------
def apply_stop_caps(sl, price, atr, direction, risk):
    dist = abs(price - sl) if sl else 0
    if dist <= 0:
        atr_fallback = atr if (atr and atr > 0) else price * 0.02
        dist = atr_fallback * RISK_PARAMS[risk]["atr_sl"]
    max_dist = price * STOP_MAX_PCT
    if atr and atr > 0:
        max_dist = min(max_dist, atr * STRUCT_STOP_MAX_ATR)
    min_dist = price * STOP_MIN_PCT
    capped_dist = max(min_dist, min(dist, max_dist))
    capped = price - capped_dist if direction == "LONG" else price + capped_dist
    return capped, abs(capped_dist - dist) > 1e-12

# ---------------------------------------------------------------------------
# Main analyzer
# ---------------------------------------------------------------------------
def analyze_market(symbol, data, direction, risk_hint="MEDIUM", ai_score=0.5, ai_detail="no AI"):
    base = data.get("30m", [])
    if len(base) < 80:
        return {"status": "NO_SIGNAL", "reason": "insufficient 30m", "symbol": symbol, "direction": direction}

    # HTF gate
    htf_min = SHORT_HTF_MIN if direction == "SHORT" else LONG_HTF_MIN
    trend4, _ = tf_state(data.get("4h", []), direction)
    trend1, _ = tf_state(data.get("1h", []), direction)
    if trend4 < htf_min or trend1 < htf_min:
        return {"status": "NO_SIGNAL", "reason": "htf gate", "symbol": symbol, "direction": direction}

    # Volatility gate
    atr = calc_atr(base)
    price = base[-1]["c"]
    atr_pct = (atr / price) if (atr and price) else 999
    if atr_pct > MAX_ATR_PCT:
        return {"status": "NO_SIGNAL", "reason": "volatility gate", "symbol": symbol, "direction": direction}

    # Components (سبک)
    components = []
    s, d = order_flow_score(base, direction); components.append(("order_flow", s, WEIGHTS["order_flow"], d))
    s, d = volume_profile_score(base, direction); components.append(("volume_profile", s, WEIGHTS["volume_profile"], d))
    s, d = sweep_score(base, direction); components.append(("sweep", s, WEIGHTS["sweep"], d))
    s, d = rsi_score(base, direction); components.append(("rsi", s, WEIGHTS["rsi"], d))
    s, d = macd_score(base, direction); components.append(("macd", s, WEIGHTS["macd"], d))
    s, d = moving_average_score(base, direction); components.append(("moving_average", s, WEIGHTS["moving_average"], d))
    # AI (neutral if no key)
    components.append(("ai_committee", ai_score, WEIGHTS["ai_committee"], ai_detail))

    # Score
    rule_w = sum(w for n, _, w, _ in components if n != "ai_committee")
    rule_score = 100 * sum(s * w for n, s, w, _ in components if n != "ai_committee") / rule_w
    total_w = sum(w for _, _, w, _ in components)
    score = 100 * sum(s * w for _, s, w, _ in components) / total_w
    risk = "LOW" if score >= 78 else "MEDIUM" if score >= 68 else "HIGH"

    # Stop
    p = RISK_PARAMS[risk]
    sl = price - atr * p["atr_sl"] if direction == "LONG" else price + atr * p["atr_sl"]
    sl, _ = apply_stop_caps(sl, price, atr, direction, risk)
    tp = price + (price - sl) * p["rr"] if direction == "LONG" else price - (sl - price) * p["rr"]

    threshold = SHORT_SIGNAL_SCORE if direction == "SHORT" else LONG_SIGNAL_SCORE
    status = "SIGNAL" if score >= threshold else "NO_SIGNAL"

    return {
        "status": status, "symbol": symbol, "direction": direction,
        "risk": risk, "score": round(score, 2), "ruleScore": round(rule_score, 2),
        "entry": round(price, 6), "stopLoss": round(sl, 6), "takeProfit": round(tp, 6),
        "rr": p["rr"], "atrPct": round(atr_pct, 4) if atr else None,
        "threshold": threshold,
        "components": [{"name": n, "score": round(s, 3), "detail": d} for n, s, _, d in components],
        "issuedAt": datetime.now(timezone.utc).isoformat(),
        "issuedAtEpoch": int(time.time()),
        "version": "12.0.0",
    }

# ---------------------------------------------------------------------------
# Optional AI committee (Groq only — for speed in GitHub Actions)
# ---------------------------------------------------------------------------
async def ask_groq(symbol, candidate):
    if not GROQ_API_KEY:
        return 0.5, "no AI key"

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    prompt = f"""You are a disciplined crypto trade reviewer.
Symbol: {symbol}
Direction: {candidate['direction']}
Rule score: {candidate['ruleScore']}
Entry: {candidate['entry']} SL: {candidate['stopLoss']} TP: {candidate['takeProfit']}
R:R: {candidate['rr']} ATR%: {candidate['atrPct']}
Components: {candidate['components']}

Approve only if evidence clearly supports the trade. Reply ONLY JSON:
{{"decision":"approve"|"reject","confidence":0-100,"reason":"short"}}"""

    payload = {
        "model": "openai/gpt-oss-20b",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 200,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload, timeout=30) as r:
                if r.status != 200:
                    return 0.5, f"AI HTTP {r.status}"
                data = await r.json()
                content = data["choices"][0]["message"]["content"]
                # strip markdown fences
                content = content.replace("```json", "").replace("```", "").strip()
                parsed = json.loads(content)
                decision = parsed.get("decision", "").lower()
                conf = float(parsed.get("confidence", 50))
                if decision.startswith("app"):
                    return conf / 100, f"AI approve {conf:.0f}%"
                if decision.startswith("rej"):
                    return (100 - conf) / 100 * 0.5, f"AI reject {conf:.0f}%"
                return 0.5, f"AI ambiguous"
    except Exception as e:
        return 0.5, f"AI error: {type(e).__name__}"

# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
async def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(url, json=payload, timeout=10)
    except Exception as e:
        log.error(f"Telegram send error: {e}")

def signal_to_telegram(s):
    dir_text = "🟢 LONG · خرید" if s["direction"] == "LONG" else "🔴 SHORT · فروش"
    sl_pct = (abs(s["entry"] - s["stopLoss"]) / s["entry"]) * 100
    return (
        f"<b>╔═ KHOSRO SIGNAL ═╗</b>\n"
        f"<b>{s['symbol']}</b>  ·  <b>{dir_text}</b>\n"
        f"<code>v{s['version']}</code>\n"
        f"<b>╚════════════════════╝</b>\n\n"
        f"🎯 <b>امتیاز:</b> <code>{s['score']:.1f}%</code> (rule: {s['ruleScore']:.0f})\n"
        f"📊 آستانه: <code>{s['threshold']}</code>\n\n"
        f"<b>╭─ نقشه معامله ─╮</b>\n"
        f"  ├─ ورود       <code>{s['entry']}</code>\n"
        f"  ├─ حد ضرر     <code>{s['stopLoss']}</code>\n"
        f"  ├─ فاصله SL   <code>{sl_pct:.2f}%</code>\n"
        f"  ├─ هدف        <code>{s['takeProfit']}</code>\n"
        f"  └─ نسبت R:R   <b>1 : {s['rr']:.2f}</b>\n"
        f"<b>╰────────────────╯</b>\n"
        f"<i>🔗 مانیتور Wispbyte پایش می‌کند · v{s['version']}</i>"
    )

# ---------------------------------------------------------------------------
# Main — produce signals.json
# ---------------------------------------------------------------------------
async def main():
    log.info("🚀 Khosro Engine v12 — GitHub Actions mode")
    signals = {}
    async with aiohttp.ClientSession() as session:
        for symbol in SYMBOLS:
            try:
                data = await fetch_symbol_data(session, symbol)
                if "30m" not in data or len(data["30m"]) < 80:
                    log.info(f"{symbol}: insufficient data")
                    continue

                candidates = []
                for d in ("LONG", "SHORT"):
                    pre = analyze_market(symbol, data, d, "MEDIUM")
                    if pre["status"] != "SIGNAL":
                        continue
                    # Optional AI consult
                    ai_score, ai_detail = await ask_groq(symbol, pre)
                    pre["components"].append({"name": "ai_committee", "score": round(ai_score, 3), "detail": ai_detail})
                    # Recompute final score with AI
                    pre = analyze_market(symbol, data, d, "MEDIUM", ai_score=ai_score, ai_detail=ai_detail)
                    if pre["status"] == "SIGNAL":
                        candidates.append(pre)

                if not candidates:
                    log.info(f"{symbol}: no signal")
                    continue

                best = max(candidates, key=lambda x: x["score"])
                log.info(f"✅ SIGNAL {symbol} {best['direction']} score={best['score']} entry={best['entry']}")

                signals[symbol] = {
                    "direction": best["direction"],
                    "entry": best["entry"],
                    "stopLoss": best["stopLoss"],
                    "takeProfit": best["takeProfit"],
                    "tp1": best["takeProfit"],   # backward-compat with old main.py
                    "tp2": best["takeProfit"],
                    "sl": best["stopLoss"],
                    "rr": best["rr"],
                    "score": best["score"],
                    "ruleScore": best["ruleScore"],
                    "risk": best["risk"],
                    "status": "OPEN",
                    "issuedAt": best["issuedAt"],
                    "issuedAtEpoch": best["issuedAtEpoch"],
                    "version": best["version"],
                    "components": best["components"],
                }

                await send_telegram(signal_to_telegram(best))

            except Exception as e:
                log.error(f"{symbol} error: {e}", exc_info=True)

    # Write signals.json
    with open("signals.json", "w") as f:
        json.dump(signals, f, indent=2, ensure_ascii=False)
    log.info(f"💾 signals.json نوشته شد: {len(signals)} سیگنال فعال")

if __name__ == "__main__":
    asyncio.run(main())
