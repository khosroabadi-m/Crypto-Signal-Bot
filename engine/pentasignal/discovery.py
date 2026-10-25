# discovery.py — کشف ترند برای F1 بدون AI
#
# مسیر:
#   ① CoinGecko /search/trending
#   ② مومنتوم/حجم از KuCoin allTickers (نه بایننس)
#   ③ امتیاز چندمنبعی → انتخاب top-N داخل استخر پنتا ∪ ارزهای اصلی
#   ④ ذخیره data/discovery/latest.json برای لاگ Actions
#
# AI به‌طور کامل حذف شده است.

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests

from . import scenarios, settings

log_name = "ps.discovery"
log = logging.getLogger(log_name)

CG_BASE = "https://api.coingecko.com/api/v3"
KUCOIN_TICKERS = settings.KUCOIN_BASE + "/api/v1/market/allTickers"

_STABLE = {
    "USDT", "USDC", "DAI", "TUSD", "FDUSD", "USDE", "USD1", "BUSD", "EUR", "USD",
}
_LEVERAGED_SUFFIX = ("3L", "3S", "2L", "2S", "UP", "DOWN", "BULL", "BEAR")


@dataclass
class DiscoveryResult:
    universe: set[str] | None
    analysis: object | None = None  # همیشه None — سازگاری با engine قدیمی
    mode: str = "disabled"
    info: dict = field(default_factory=dict)
    extra_bases: set[str] = field(default_factory=set)  # ترند CG خارج از استخر


def _timeout() -> int:
    return int(getattr(settings, "F1_DISCOVERY_TIMEOUT", 8) or 8)


def _get_json(url: str, params: dict | None = None, headers: dict | None = None) -> Any:
    try:
        h = {"User-Agent": "PentaSignal/3.5 (discovery)", "Accept": "application/json"}
        if headers:
            h.update(headers)
        # کلید دموی رایگان CoinGecko اگر باشد
        key = os.getenv("COINGECKO_API_KEY") or ""
        if key and "coingecko.com" in url:
            h["x-cg-demo-api-key"] = key
        r = requests.get(url, params=params or {}, headers=h, timeout=_timeout())
        if r.status_code == 200:
            return r.json()
        log.warning("GET %s -> HTTP %s", url, r.status_code)
    except Exception as exc:
        log.warning("GET %s failed: %s", url, exc)
    return None



def fetch_kucoin_usdt_bases() -> set[str]:
    """همهٔ baseهای *-USDT روی KuCoin (بدون فیلتر حجم) برای اعتبارسنجی ترند."""
    payload = _get_json(KUCOIN_TICKERS) or {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    tickers = (data or {}).get("ticker") or []
    bases: set[str] = set()
    for row in tickers:
        symbol = row.get("symbol") or ""
        if not symbol.endswith("-USDT"):
            continue
        base = symbol[: -len("-USDT")].upper()
        if not base or base in _STABLE:
            continue
        if any(base.endswith(suf) for suf in _LEVERAGED_SUFFIX):
            continue
        bases.add(base)
    return bases

def fetch_cg_trending() -> list[dict]:
    """https://api.coingecko.com/api/v3/search/trending"""
    data = _get_json(f"{CG_BASE}/search/trending") or {}
    out = []
    for i, row in enumerate(data.get("coins") or []):
        item = row.get("item") or {}
        sym = (item.get("symbol") or "").upper()
        if not sym or sym in _STABLE:
            continue
        out.append({
            "symbol": sym,
            "name": item.get("name") or sym,
            "rank": i + 1,
            "score_hint": max(0.0, 1.0 - i / 15.0),
            "market_cap_rank": item.get("market_cap_rank"),
            "price_btc": item.get("price_btc"),
        })
    return out


def fetch_kucoin_board() -> dict[str, dict]:
    """allTickers کوکوین → {BASE: stats} فقط جفت‌های *-USDT غیرلوریج."""
    payload = _get_json(KUCOIN_TICKERS) or {}
    # پاسخ KuCoin: {code, data: {time, ticker: [...]}}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    tickers = (data or {}).get("ticker") or []
    best: dict[str, dict] = {}
    for row in tickers:
        symbol = row.get("symbol") or ""
        if not symbol.endswith("-USDT"):
            continue
        base = symbol[: -len("-USDT")].upper()
        if not base or base in _STABLE:
            continue
        if any(base.endswith(suf) for suf in _LEVERAGED_SUFFIX):
            continue
        try:
            change = float(row.get("changeRate") or 0.0) * 100.0  # درصد
            vol = float(row.get("volValue") or 0.0)
            last = float(row.get("last") or 0.0)
        except (TypeError, ValueError):
            continue
        if last <= 0 or vol < 50_000:  # فیلتر جفت خیلی کم‌عمق
            continue
        prev = best.get(base)
        if prev is None or vol > prev["volume_24h_usd"]:
            best[base] = {
                "pair": symbol.replace("-", ""),  # BTCUSDT-style for meta
                "kucoin_symbol": symbol,
                "price_usd": last,
                "change_24h_pct": change,
                "volume_24h_usd": vol,
            }
    return best


def build_candidates(board: dict, trending: list[dict]) -> list[dict]:
    """امتیاز چندمنبعی: ترند CG قوی‌تر + مومنتوم/حجم KuCoin."""
    scores: dict[str, dict] = {}

    def _put(sym: str, source: str, weight: float, extra: dict | None = None):
        sym = sym.upper()
        if not sym or sym in _STABLE:
            return
        slot = scores.setdefault(sym, {
            "symbol": sym, "components": {}, "sources": [], "stats": {},
        })
        slot["components"][source] = max(slot["components"].get(source, 0.0), weight)
        if source not in slot["sources"]:
            slot["sources"].append(source)
        if extra:
            slot["stats"].update({k: v for k, v in extra.items() if v is not None})

    # CoinGecko trending ranks
    for item in trending:
        _put(
            item["symbol"], "coingecko_trending", float(item.get("score_hint") or 0.5),
            {"name": item.get("name"), "cg_rank": item.get("rank")},
        )

    # KuCoin: top gainers / losers / volume among pool-relevant liquid USDT
    liquid = sorted(board.values(), key=lambda x: x["volume_24h_usd"], reverse=True)
    gainers = sorted(liquid, key=lambda x: x["change_24h_pct"], reverse=True)[:15]
    losers = sorted(liquid, key=lambda x: x["change_24h_pct"])[:15]
    by_vol = liquid[:15]

    for i, b in enumerate(gainers):
        base = b["kucoin_symbol"].split("-")[0].upper()
        _put(base, "kucoin_gainer", 1.0 - i / 15.0, b)
    for i, b in enumerate(losers):
        base = b["kucoin_symbol"].split("-")[0].upper()
        _put(base, "kucoin_loser", 1.0 - i / 15.0, b)
    for i, b in enumerate(by_vol):
        base = b["kucoin_symbol"].split("-")[0].upper()
        _put(base, "kucoin_volume", 1.0 - i / 15.0, b)

    W = {
        "coingecko_trending": 0.50,
        "kucoin_gainer": 0.20,
        "kucoin_loser": 0.15,
        "kucoin_volume": 0.15,
    }
    out = []
    for sym, slot in scores.items():
        comps = slot["components"]
        wsum = sum(W[k] for k in comps if k in W) or 1.0
        score = 100.0 * sum(W[k] * comps[k] for k in comps if k in W) / wsum
        # چندمنبعی بودن را کمی تقویت کن
        if len(slot["sources"]) >= 2:
            score = min(100.0, score * 1.08)
        out.append({
            "symbol": sym,
            "score": round(score, 2),
            "sources": slot["sources"],
            "components": comps,
            "stats": slot["stats"],
            "pair": slot["stats"].get("pair") or f"{sym}USDT",
            "kucoin_symbol": slot["stats"].get("kucoin_symbol") or f"{sym}-USDT",
        })
    out.sort(key=lambda c: c["score"], reverse=True)
    return out


def select_universe(
    candidates: list[dict],
    mains: set[str],
    trending: list[dict] | None = None,
    board: dict | None = None,
    kc_bases: set[str] | None = None,
) -> tuple[set[str], list[dict], str, set[str]]:
    """انتخاب F1:
      • مشترک با استخر: top-N از کاندیداهای داخل UNION
      • ارزهای اصلی
      • جدا از ترند CG: تا F1_TREND_EXTRA_N نماد که در استخرهای دیگر نیستند
        ولی روی KuCoin جفت USDT دارند

    خروجی: (universe_bases, rows, mode, extra_bases)
    """
    pool_bases = {s.split("-")[0].upper() for s in scenarios.UNION_SYMBOLS}
    mains = {m.upper() for m in mains} & pool_bases
    top_n = int(getattr(settings, "F1_DISCOVERY_TOP_N", 8) or 8)
    extra_n = int(getattr(settings, "F1_TREND_EXTRA_N", 5) or 5)
    board = board or {}
    trending = trending or []
    kc_bases = set(kc_bases or ()) | set(board.keys())

    # --- مشترک با استخر ---
    fb = [c for c in candidates if c["symbol"] in pool_bases]
    picked = {c["symbol"] for c in fb[:top_n]}
    rows = [{
        "symbol": c["symbol"],
        "direction": None,
        "confidence": c.get("score"),
        "trend": None,
        "reason": f"score={c.get('score')} sources={'+'.join(c.get('sources') or [])}",
        "risk_note": "",
        "tradeable": True,
        "selected": c["symbol"] in picked,
        "bucket": "pool",
        "stats": {
            "change_24h_pct": (c.get("stats") or {}).get("change_24h_pct"),
            "volume_24h_usd": (c.get("stats") or {}).get("volume_24h_usd"),
        },
    } for c in fb[: top_n + 5]]

    # --- فقط ترند CG، خارج از استخرهای F2–F5، با جفت واقعی KuCoin ---
    extra: set[str] = set()
    skipped_no_kc: list[str] = []
    for item in trending:
        if len(extra) >= extra_n:
            break
        sym = (item.get("symbol") or "").upper()
        if not sym or sym in pool_bases or sym in mains or sym in extra:
            continue
        if sym not in kc_bases:
            skipped_no_kc.append(sym)
            continue
        extra.add(sym)
        rows.append({
            "symbol": sym,
            "direction": None,
            "confidence": item.get("score_hint"),
            "trend": "cg_trending",
            "reason": f"ترند CG rank={item.get('rank')} (خارج از استخر — فقط F1)",
            "risk_note": "",
            "tradeable": True,
            "selected": True,
            "bucket": "trend_extra",
            "stats": {},
        })

    universe = picked | mains | extra
    if skipped_no_kc:
        rows.append({
            "symbol": ",".join(skipped_no_kc[:8]),
            "direction": None,
            "confidence": None,
            "trend": None,
            "reason": "ترند CG بدون جفت KuCoin — حذف شد",
            "risk_note": "",
            "tradeable": False,
            "selected": False,
            "bucket": "skipped_no_kucoin",
            "stats": {},
        })
    return universe, rows, "rules+trend_extra", extra


def run() -> DiscoveryResult:
    t0 = time.time()
    res = DiscoveryResult(universe=None, analysis=None, mode="disabled")
    info: dict = {
        "run_at_tehran": datetime.now(ZoneInfo("Asia/Tehran")).strftime("%Y-%m-%d %H:%M:%S"),
        "sources_ok": [],
        "sources_failed": {},
        "candidates_top": [],
        "selected": {},
        "ai": {"enabled": False, "model": None},
        "elapsed_ms": 0,
    }
    res.info = info
    if not getattr(settings, "F1_DISCOVERY", True):
        res.mode = "disabled"
        return res

    board, trending, kc_bases = {}, [], set()
    try:
        board = fetch_kucoin_board()
        kc_bases = fetch_kucoin_usdt_bases()
        if board or kc_bases:
            info["sources_ok"].append("kucoin")
        else:
            info["sources_failed"]["kucoin"] = "empty_board"
    except Exception as exc:
        info["sources_failed"]["kucoin"] = str(exc)[:120]

    try:
        trending = fetch_cg_trending()
        if trending:
            info["sources_ok"].append("coingecko")
        else:
            info["sources_failed"]["coingecko"] = "empty_trending"
    except Exception as exc:
        info["sources_failed"]["coingecko"] = str(exc)[:120]

    candidates = build_candidates(board, trending)
    info["candidates_top"] = [{
        "symbol": c["symbol"],
        "score": c["score"],
        "sources": c["sources"],
        "change_24h_pct": (c.get("stats") or {}).get("change_24h_pct"),
    } for c in candidates[:15]]

    mains = set(getattr(settings, "F1_MAIN_COINS", ["BTC", "ETH", "BNB", "SOL", "XRP"]))
    universe, sel_rows, mode, extra = select_universe(
        candidates, mains, trending=trending, board=board, kc_bases=kc_bases,
    )
    if not universe:
        pool_bases = {s.split("-")[0].upper() for s in scenarios.UNION_SYMBOLS}
        universe = {m for m in mains if m in pool_bases}
        extra = set()
        mode = "mains-only"

    res.mode = mode
    res.universe = universe
    res.analysis = None  # AI حذف شده
    res.extra_bases = extra  # type: ignore[attr-defined]

    info["selected"] = {
        "mode": res.mode,
        "universe": sorted(universe),
        "main_coins": sorted(mains),
        "trend_extra": sorted(extra),
        "rows": sel_rows,
    }
    info["elapsed_ms"] = int((time.time() - t0) * 1000)
    _persist(info)
    _log_summary(info)
    return res


def _discovery_dir() -> str:
    d = os.path.join(settings.DATA_DIR, "discovery")
    os.makedirs(d, exist_ok=True)
    return d


def _persist(info: dict):
    try:
        d = _discovery_dir()
        with open(os.path.join(d, "latest.json"), "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=2)
        hist = os.path.join(d, "history.jsonl")
        with open(hist, "a", encoding="utf-8") as f:
            f.write(json.dumps(info, ensure_ascii=False) + "\n")
        try:
            with open(hist, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) > 96:
                with open(hist, "w", encoding="utf-8") as f:
                    f.writelines(lines[-96:])
        except Exception:
            pass
    except Exception:
        pass


def _log_summary(info: dict):
    sel = info.get("selected") or {}
    logging.getLogger(log_name).info(
        "F1 DISCOVERY [%s] sources=%s | universe(%d)=%s | trend_extra=%s | top=%s",
        sel.get("mode"),
        ",".join(info.get("sources_ok") or []) or "-",
        len(sel.get("universe") or []),
        ",".join(sorted(sel.get("universe") or [])),
        ",".join(sel.get("trend_extra") or []) or "-",
        ",".join(
            f"{c['symbol']}:{c['score']}"
            for c in (info.get("candidates_top") or [])[:5]
        ),
    )


def apply(ctx) -> DiscoveryResult | None:
    """کشف ترند را اجرا و روی ctx سوار می‌کند. AI ست نمی‌شود."""
    if not getattr(settings, "F1_DISCOVERY", True):
        return None
    try:
        res = run()
    except Exception as exc:
        logging.getLogger(log_name).error("discovery failed — F1 unfiltered: %s", exc)
        return None
    ctx.f1_universe = res.universe
    ctx.khosro_ai = None  # AI کامل حذف
    ctx.discovery_info = res.info
    # نمادهای کامل KuCoin برای ترندهای خارج‌استخر (مثلاً M87-USDT)
    extra = getattr(res, "extra_bases", None) or set()
    ctx.f1_extra_symbols = {f"{b}-USDT" for b in extra}
    return res
