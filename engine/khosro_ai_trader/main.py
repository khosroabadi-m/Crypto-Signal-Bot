"""Main orchestrator: sources → candidates → enrichment → scoring → persist → notify."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import __project__, __version__
from .ai import AIAnalyst
from .config import Config, load_config
from .logger import get_logger, setup_logging
from .models import TrendingSnapshot
from .notify import TelegramNotifier
from .scoring import (
    apply_filters,
    build_candidates,
    combine_and_rank,
    compute_components,
    enrich_candidates,
)
from .dashboard import generate_dashboard
from .paper import PaperJournal
from .risk import RiskEngine
from .signals import RuleSignalEngine
from .signals.base import Signal
from .sources import (
    BinanceSource,
    CoinGeckoSource,
    CoinPaprikaSource,
    CryptoPanicSource,
    MarketDataHub,
    RedditSource,
)
from .storage import append_history, save_ai_analysis, save_latest, save_signals

log = get_logger("main")

TEHRAN_TZ = ZoneInfo("Asia/Tehran")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _compact_usd(value: float | None) -> str:
    """Human-friendly USD amount for console table: 3.2B$ / 155.0M$ / 49.2K$."""
    if not value:
        return "—"
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if value >= div:
            return f"{value / div:,.1f}{suf}$"
    return f"{value:,.0f}$"


def _print_table(snapshot: TrendingSnapshot) -> None:
    """Simple dependency-free console table for local runs / CI logs."""
    print("\n=== 🔥 KhosroAiTrader — Trending Board ===")
    print(f"UTC: {snapshot.run_at_utc}   Tehran: {snapshot.run_at_tehran}")
    print(
        f"{'#':>3}  {'SYMBOL':<12}{'SCORE':>7}{'24h%':>8}"
        f"{'VOL 24h':>12}{'MCAP':>12}  PAIR"
    )
    print("-" * 78)
    for c in snapshot.coins:
        change = c.stats.change_24h_pct
        vol_s = _compact_usd(c.stats.volume_24h_usd)
        mcap_s = _compact_usd(c.stats.market_cap_usd)
        print(
            f"{c.rank:>3}  {c.symbol:<12}{c.score:>7.1f}{(change or 0):>8.1f}"
            f"{vol_s:>12}{mcap_s:>12}  {c.binance_pair or '—'}"
        )
    print("-" * 78)
    print(f"sources ok: {', '.join(snapshot.sources_ok) or '—'}")
    if snapshot.sources_failed:
        print(f"sources failed: {snapshot.sources_failed}")
    print(f"candidates scored: {snapshot.candidate_count} → kept top {len(snapshot.coins)}\n")


def _candle_dict(row: list) -> dict:
    """Binance kline row → compact candle dict for the paper journal."""
    return {
        "open_time": int(row[0]), "close_time": int(row[6]),
        "open": float(row[1]), "high": float(row[2]),
        "low": float(row[3]), "close": float(row[4]),
    }


def _print_signals(signals: list[Signal]) -> None:
    print("\n=== 🎯 Signal Engine (rule book v1) ===")
    if not signals:
        print("no signals cleared the rule book this run\n")
        return
    for s in signals:
        arrow = "🟢 LONG " if s.direction == "long" else "🔴 SHORT"
        tps = "/".join(f"{tp:.6g}" for tp in s.take_profits)
        print(
            f"{arrow} {s.symbol:<10} conf={s.confidence:>5.1f}%  "
            f"entry={s.entry:.6g} stop={s.stop_loss:.6g} tps={tps}"
        )
        print(
            f"          risk={s.position_size_usd}$ (1R) notional={s.notional_usd}$ "
            f"RR@TP2={s.rr:.1f}"
        )
        if s.reasons:
            print(f"          {'; '.join(s.reasons[:4])}")
    print()


def _circuit_note(cfg: Config, journal: PaperJournal) -> str:
    """Human note when the daily-loss circuit breaker is engaged."""
    daily = journal.realized_r_today()
    if daily <= -abs(cfg.risk.max_daily_loss_r):
        return (
            f"مدار قطع اضطراری فعال است ({daily:+.1f}R امروز) "
            f"— تا فردا سیگنال جدید صادر نمی‌شود"
        )
    return ""


# --------------------------------------------------------------------------
# pipeline
# --------------------------------------------------------------------------

def run_pipeline(
    cfg: Config, dry_run: bool = False, top_override: int | None = None,
    no_ai: bool = False, no_signals: bool = False, no_dashboard: bool = False,
) -> int:
    """Execute one full scan. Returns process exit code."""
    root = cfg.project_root
    started = time.monotonic()
    sources_ok: list[str] = []
    sources_failed: dict[str, str] = {}

    # ---- 1) collect raw data from every source (each failure tolerated) ----
    trending: list[dict] = []
    cg_markets: dict[str, dict] = {}
    binance: dict[str, dict] = {}
    paprika: dict[str, dict] = {}
    macro: dict[str, dict] | None = None

    if cfg.sources.coingecko_enabled:
        try:
            cg_payload = CoinGeckoSource(cfg).fetch()
            trending = cg_payload["trending"]
            cg_markets = cg_payload["markets"]
            sources_ok.append("coingecko")
        except Exception as exc:  # noqa: BLE001
            sources_failed["coingecko"] = str(exc)[:160]
            log.error("coingecko failed: %s", exc)
        try:  # macro context for the AI layer — never fatal
            macro = CoinGeckoSource(cfg).fetch_global()
        except Exception as exc:  # noqa: BLE001
            log.warning("global context unavailable: %s", exc)

    if cfg.sources.binance_enabled:
        try:
            binance = BinanceSource(cfg).fetch()
            sources_ok.append("binance")
        except Exception as exc:  # noqa: BLE001
            sources_failed["binance"] = str(exc)[:160]
            log.error("binance failed: %s", exc)

    if cfg.sources.coinpaprika_enabled:
        try:
            paprika = CoinPaprikaSource(cfg).fetch()
            sources_ok.append("coinpaprika")
        except Exception as exc:  # noqa: BLE001
            sources_failed["coinpaprika"] = str(exc)[:160]
            log.warning("coinpaprika failed: %s", exc)

    if not sources_ok:
        log.critical("all primary sources failed — aborting run")
        return 2

    # ---- 2) candidates + enrichment ----
    candidates = build_candidates(
        trending, binance, cfg.trending.momentum_candidates
    )
    enrich_candidates(candidates, cg_markets, paprika, binance)

    # tag paprika usage (for provenance display)
    for cand in candidates:
        if cand["symbol"] in paprika:
            cand["_paprika"] = True

    # ---- 3) optional social signals for the candidate pool ----
    reddit_mentions: dict[str, int] | None = None
    if cfg.sources.reddit_enabled:
        try:
            reddit_mentions = RedditSource(cfg).fetch_mentions(candidates)
            sources_ok.append("reddit")
        except Exception as exc:  # noqa: BLE001
            sources_failed["reddit"] = str(exc)[:160]
            log.warning("reddit skipped: %s", exc)

    news_counts: dict[str, int] | None = None
    if cfg.sources.cryptopanic_enabled and cfg.cryptopanic_api_key:
        try:
            news_counts = CryptoPanicSource(cfg).fetch_news_counts(candidates)
            sources_ok.append("cryptopanic")
            # fold news velocity into mention counts (single social channel)
            if reddit_mentions is None:
                reddit_mentions = news_counts
            else:
                for sym, cnt in news_counts.items():
                    reddit_mentions[sym] = reddit_mentions.get(sym, 0) + cnt
        except Exception as exc:  # noqa: BLE001
            sources_failed["cryptopanic"] = str(exc)[:160]
            log.info("cryptopanic skipped: %s", exc)

    # ---- 4) filter → score → rank ----
    filtered = apply_filters(candidates, cfg)
    compute_components(filtered, reddit_mentions)
    top_n = top_override or cfg.trending.top_n
    coins, weights_used = combine_and_rank(filtered, cfg)
    coins = coins[:top_n]

    now_utc = datetime.now(timezone.utc)
    snapshot = TrendingSnapshot(
        run_at_utc=now_utc.strftime("%Y-%m-%d %H:%M UTC"),
        run_at_tehran=now_utc.astimezone(TEHRAN_TZ).strftime("%Y-%m-%d %H:%M"),
        duration_seconds=time.monotonic() - started,
        coins=coins,
        sources_ok=sources_ok,
        sources_failed=sources_failed,
        weights_used=weights_used,
        filters={
            "min_market_cap_usd": cfg.trending.min_market_cap_usd,
            "min_volume_24h_usd": cfg.trending.min_volume_24h_usd,
            "exclude_stablecoins": cfg.trending.exclude_stablecoins,
            "require_binance_pair": cfg.trending.require_binance_pair,
        },
        candidate_count=len(filtered),
        macro=macro,
    )

    _print_table(snapshot)

    if not coins:
        log.error("no coins survived filters — check liquidity thresholds in config")
        return 3

    # ---- 5) AI aggregation layer (multi-source synthesis, optional) ----
    if no_ai:
        log.info("AI layer disabled via --no-ai")
    else:
        try:
            analyst = AIAnalyst(cfg)
            if analyst.available:
                analysis = analyst.analyze(snapshot, macro=macro)
                if analysis:
                    snapshot.ai = analysis
                    sources_ok.append("ai_analyst")
                    _print_ai_summary(analysis)
                else:
                    log.warning("AI analysis discarded (unparsable/invalid response)")
            else:
                log.info("AI_API_KEY not set — AI aggregation layer skipped")
        except Exception as exc:  # noqa: BLE001 — AI must never kill the pipeline
            sources_failed["ai_analyst"] = str(exc)[:160]
            log.warning("AI layer failed: %s", exc)

    # ---- 6) professional data edge: funding/OI/LSR/depth/F&G (never fatal) ----
    hub = MarketDataHub(cfg)
    md_data: dict[str, dict] = {}
    if cfg.market_data.enabled and not no_signals:
        try:
            md_coins = [
                {"symbol": c.symbol, "pair": c.binance_pair}
                for c in coins[: cfg.signals.top_candidates] if c.binance_pair
            ]
            md_data = hub.enrich_coins(md_coins)
            if md_data:
                sources_ok.append("marketdata")
                fng = next(
                    (v.get("fear_greed") for v in md_data.values() if v.get("fear_greed")),
                    None,
                )
                if fng:
                    log.info("fear&greed index: %s (%s)", fng["value"], fng["label"])
        except Exception as exc:  # noqa: BLE001
            sources_failed["marketdata"] = str(exc)[:160]
            log.warning("market-data edge failed: %s", exc)

    # ---- 7) paper journal: mark open trades to market (fresh risk state) ----
    journal: PaperJournal | None = None
    if cfg.signals.enabled and cfg.paper.enabled:
        journal = PaperJournal(cfg, root)
        if not dry_run and journal.open_trades():
            candles_by_pair: dict[str, list[dict]] = {}
            for pair in sorted({t.get("pair") for t in journal.open_trades() if t.get("pair")}):
                try:
                    rows = hub.fetch_klines(
                        pair, cfg.signals.kline_interval, cfg.signals.max_age_hours + 2
                    )
                    candles_by_pair[pair] = [_candle_dict(r) for r in rows]
                except Exception as exc:  # noqa: BLE001
                    log.warning("candles for open trade %s failed: %s", pair, exc)
            try:
                journal.mark_to_market(candles_by_pair)
            except Exception as exc:  # noqa: BLE001
                log.warning("mark-to-market failed: %s", exc)

    # ---- 8) signal engine (rule book v1) + risk engine (sizing/breakers) ----
    signals: list[Signal] = []
    if cfg.signals.enabled and not no_signals:
        try:
            engine = RuleSignalEngine(cfg, hub)
            raw_signals = engine.evaluate(snapshot, md_data)
            risk = RiskEngine(cfg)
            signals = risk.filter_and_size(raw_signals, journal or PaperJournal(cfg, root))
            if signals:
                snapshot.meta_signals = [s.symbol for s in signals]  # provenance
        except Exception as exc:  # noqa: BLE001 — signals must never kill the scan
            sources_failed["signal_engine"] = str(exc)[:160]
            log.warning("signal engine failed: %s", exc)
        _print_signals(signals)

    # ---- 9) persist + notify ----
    if dry_run:
        log.info("dry-run mode: skipping persistence and notifications")
        return 0

    save_latest(snapshot, cfg.storage.latest_path, root)
    append_history(snapshot, cfg.storage.history_dir, cfg.storage.history_days, root)
    if snapshot.ai:
        save_ai_analysis(snapshot.ai, "data/ai_analysis.json", root)
    if signals:
        save_signals(signals, "data/signals_latest.json", root)

    # ---- 10) paper journal: equity point + open new paper trades ----
    if journal is not None:
        journal.note_equity_point()
        journal.open_from_signals(signals)
        journal.save()

    # ---- 11) dashboard (docs/index.html for GitHub Pages) ----
    if cfg.dashboard.enabled and not no_dashboard:
        try:
            generate_dashboard(cfg, root)
        except Exception as exc:  # noqa: BLE001
            log.warning("dashboard generation failed: %s", exc)

    # ---- 12) telegram: signal cards (fallback: trending board) + daily report ----
    notifier = TelegramNotifier(cfg)
    sent_signal = notifier.send_signals(signals, snapshot)
    if not sent_signal:
        notifier.send_snapshot(snapshot)

    if journal is not None and cfg.paper.daily_report:
        try:
            today_tehran = datetime.now(TEHRAN_TZ).strftime("%Y-%m-%d")
            if journal.last_report_date != today_tehran:
                notifier.send_daily_report(
                    journal.stats(), journal.open_trades(), _circuit_note(cfg, journal)
                )
                journal.last_report_date = today_tehran
        except Exception as exc:  # noqa: BLE001
            log.warning("daily report failed: %s", exc)

    log.info(
        "run finished in %.1fs ✔ (coins=%d, sources_ok=%d)",
        snapshot.duration_seconds, len(coins), len(sources_ok),
    )
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _print_ai_summary(analysis) -> None:
    """Console view of the AI verdicts."""
    print("\n=== 🤖 AI Aggregation Layer ===")
    print(f"model: {analysis.model}   sentiment: {analysis.sentiment} "
          f"({analysis.sentiment_confidence}%)")
    if analysis.market_summary:
        print(f"summary: {analysis.market_summary}")
    print("-" * 78)
    for v in analysis.verdicts:
        reasons = " | ".join(v.reasons)
        print(
            f"  {v.symbol:<12}{v.direction:>7} ({v.confidence:>3}%)  "
            f"{v.trend:<8} {reasons}"
        )
    print()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=f"{__project__} v{__version__}",
        description="Crypto trending scanner — feeds the future signal engine",
    )
    parser.add_argument(
        "--config", default=None, help="path to config.yaml (default: config/config.yaml)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="fetch + score + print only; no files written, no telegram sent",
    )
    parser.add_argument("--top", type=int, default=None, help="override trending.top_n")
    parser.add_argument(
        "--no-ai", action="store_true",
        help="skip the LLM aggregation layer even if AI_API_KEY is set",
    )
    parser.add_argument(
        "--no-signals", action="store_true",
        help="skip market-data edge + signal engine + paper trading",
    )
    parser.add_argument(
        "--no-dashboard", action="store_true",
        help="skip docs/index.html regeneration",
    )
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.log_level)
    log.info("%s v%s starting (dry_run=%s)", __project__, __version__, args.dry_run)

    cfg = load_config(args.config)
    try:
        return run_pipeline(
            cfg, dry_run=args.dry_run, top_override=args.top, no_ai=args.no_ai,
            no_signals=args.no_signals, no_dashboard=args.no_dashboard,
        )
    except KeyboardInterrupt:
        log.warning("interrupted by user")
        return 130
    except Exception:  # noqa: BLE001 — last-resort guard for CI visibility
        log.exception("unexpected failure")
        return 1


if __name__ == "__main__":
    sys.exit(main())
