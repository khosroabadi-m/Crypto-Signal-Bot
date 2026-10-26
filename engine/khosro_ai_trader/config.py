"""Configuration loading: config/config.yaml + .env overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

try:  # optional locally, not needed on CI
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*_a, **_k) -> bool:  # type: ignore[misc]
        return False

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"


@dataclass
class TrendingConfig:
    top_n: int = 12
    min_market_cap_usd: float = 50_000_000
    min_volume_24h_usd: float = 10_000_000
    exclude_stablecoins: bool = True
    exclude_symbols: list[str] = field(default_factory=list)
    require_binance_pair: bool = True
    momentum_candidates: int = 60


@dataclass
class SourcesConfig:
    coingecko_enabled: bool = True
    markets_per_page: int = 250
    binance_enabled: bool = True
    quote_asset: str = "USDT"
    coinpaprika_enabled: bool = True
    reddit_enabled: bool = False
    reddit_subreddits: list[str] = field(
        default_factory=lambda: ["CryptoCurrency", "CryptoMarkets", "altcoin"]
    )
    reddit_posts_limit: int = 50
    cryptopanic_enabled: bool = True
    cryptopanic_filter: str = "hot"


@dataclass
class StorageConfig:
    latest_path: str = "data/trending_latest.json"
    history_dir: str = "data/history"
    history_days: int = 7


@dataclass
class TelegramConfig:
    enabled: bool = True
    top_coins_in_message: int = 10
    disable_notification: bool = False


@dataclass
class RunConfig:
    user_agent: str = "KhosroAiTrader/2.0 (crypto trending + signal engine)"
    request_timeout: int = 15
    max_retries: int = 3


@dataclass
class AIConfig:
    """LLM aggregation layer (OpenAI-compatible chat/completions API).

    Activates only when AI_API_KEY is present. Works with Z.ai GLM,
    OpenAI, or any OpenAI-compatible provider.
    """

    enabled: bool = True
    base_url: str = "https://api.z.ai/api/paas/v4"
    model: str = "glm-4.6"
    temperature: float = 0.2
    max_output_tokens: int = 1500
    max_candidates: int = 15     # coins fed into the prompt (token control)
    timeout: int = 60


@dataclass
class SignalsConfig:
    """Rule-based signal engine (rule book v1 — replaceable by the user's rules)."""

    enabled: bool = True
    top_candidates: int = 8        # how many top-ranked coins get technical analysis
    max_signals_per_run: int = 3
    kline_interval: str = "1h"
    kline_limit: int = 300
    min_confidence: int = 45       # min score for a signal to be emitted
    min_score_gap: int = 15        # |long score - short score| must exceed this
    atr_sl_multiplier: float = 1.5 # stop distance = ATR * this
    min_atr_pct: float = 0.3       # skip dead-quiet coins (ATR < 0.3% of price)
    max_atr_pct: float = 8.0       # skip lottery coins (ATR > 8% of price)
    trend_gate: bool = False       # EMA-structure gate — measured -0.03R in tests; keep off
    use_ai_fusion: bool = True     # fold AI verdicts into confidence
    ai_confidence_bonus: int = 20
    ai_veto: bool = True           # AI "avoid" kills the signal
    cooldown_hours: int = 12       # no re-entry same symbol+direction within window
    max_age_hours: int = 72        # force-close paper trades older than this


@dataclass
class RiskConfig:
    """Position sizing + portfolio-level circuit breakers (paper trading)."""

    equity_usd: float = 1_000.0
    risk_per_trade_pct: float = 1.0    # % of equity risked per trade (1R)
    max_notional_pct: float = 30.0     # position notional cap, % of equity
    min_rr: float = 1.5                # RR at TP2 must be >= this
    max_open_trades: int = 5
    max_daily_loss_r: float = 3.0      # circuit breaker: stop after -3R in a day
    fee_r_per_round_trip: float = 0.06 # ~0.1% fee/slippage expressed in R
    tp_weights: list[float] = field(default_factory=lambda: [0.4, 0.4, 0.2])


@dataclass
class PaperConfig:
    enabled: bool = True
    journal_path: str = "data/paper/journal.json"
    daily_report: bool = True          # one Telegram equity report per Tehran day


@dataclass
class MarketDataConfig:
    """Professional data edge: derivatives + order-flow + sentiment (all free)."""

    enabled: bool = True
    depth_limit: int = 100             # Binance spot order-book levels
    lsr_period: str = "1h"             # long/short account ratio period
    oi_period: str = "1h"              # open-interest history period
    fng_enabled: bool = True           # alternative.me Fear & Greed
    max_workers: int = 6               # thread pool for per-coin enrichment


@dataclass
class DashboardConfig:
    enabled: bool = True
    output_path: str = "docs/index.html"


@dataclass
class BacktestConfig:
    default_pairs: list[str] = field(
        default_factory=lambda: ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    )
    days: int = 120
    interval: str = "1h"
    max_age_bars: int = 72
    warmup_bars: int = 210


@dataclass
class Config:
    weights: dict[str, float] = field(
        default_factory=lambda: {
            "coingecko_trending": 0.40,
            "market_momentum": 0.35,
            "liquidity": 0.25,
            "reddit_mentions": 0.00,
        }
    )
    trending: TrendingConfig = field(default_factory=TrendingConfig)
    sources: SourcesConfig = field(default_factory=SourcesConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    run: RunConfig = field(default_factory=RunConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    signals: SignalsConfig = field(default_factory=SignalsConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    paper: PaperConfig = field(default_factory=PaperConfig)
    market_data: MarketDataConfig = field(default_factory=MarketDataConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)

    # secrets (env)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    cryptopanic_api_key: str = ""
    coingecko_api_key: str = ""
    ai_api_key: str = ""

    @property
    def project_root(self) -> Path:
        return PROJECT_ROOT


def _dig(d: dict[str, Any], *path: str, default: Any = None) -> Any:
    cur: Any = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


def load_config(path: str | Path | None = None) -> Config:
    """Load YAML config (falling back to sane defaults) and apply env secrets."""
    load_dotenv(PROJECT_ROOT / ".env")

    cfg = Config()
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    raw: dict[str, Any] = {}
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}

    # ---- weights ----
    w = raw.get("weights") or {}
    for key in list(cfg.weights):
        if key in w:
            cfg.weights[key] = float(w[key])

    # ---- trending ----
    t = raw.get("trending") or {}
    cfg.trending = TrendingConfig(
        top_n=int(t.get("top_n", cfg.trending.top_n)),
        min_market_cap_usd=float(t.get("min_market_cap_usd", cfg.trending.min_market_cap_usd)),
        min_volume_24h_usd=float(t.get("min_volume_24h_usd", cfg.trending.min_volume_24h_usd)),
        exclude_stablecoins=bool(t.get("exclude_stablecoins", True)),
        exclude_symbols=[str(s).upper() for s in (t.get("exclude_symbols") or [])],
        require_binance_pair=bool(t.get("require_binance_pair", True)),
        momentum_candidates=int(t.get("momentum_candidates", 60)),
    )

    # ---- sources ----
    s = raw.get("sources") or {}
    cg = s.get("coingecko") or {}
    bn = s.get("binance") or {}
    cp = s.get("coinpaprika") or {}
    rd = s.get("reddit") or {}
    cpa = s.get("cryptopanic") or {}
    cfg.sources = SourcesConfig(
        coingecko_enabled=bool(cg.get("enabled", True)),
        markets_per_page=int(cg.get("markets_per_page", 250)),
        binance_enabled=bool(bn.get("enabled", True)),
        quote_asset=str(bn.get("quote_asset", "USDT")).upper(),
        coinpaprika_enabled=bool(cp.get("enabled", True)),
        reddit_enabled=bool(rd.get("enabled", False)),
        reddit_subreddits=list(rd.get("subreddits") or cfg.sources.reddit_subreddits),
        reddit_posts_limit=int(rd.get("posts_per_subreddit", 50)),
        cryptopanic_enabled=bool(cpa.get("enabled", True)),
        cryptopanic_filter=str(cpa.get("filter", "hot")),
    )

    # ---- storage ----
    st = raw.get("storage") or {}
    cfg.storage = StorageConfig(
        latest_path=str(st.get("latest_path", cfg.storage.latest_path)),
        history_dir=str(st.get("history_dir", cfg.storage.history_dir)),
        history_days=int(st.get("history_days", cfg.storage.history_days)),
    )

    # ---- telegram ----
    tg = raw.get("telegram") or {}
    cfg.telegram = TelegramConfig(
        enabled=bool(tg.get("enabled", True)),
        top_coins_in_message=int(tg.get("top_coins_in_message", 10)),
        disable_notification=bool(tg.get("disable_notification", False)),
    )

    # ---- run ----
    rn = raw.get("run") or {}
    cfg.run = RunConfig(
        user_agent=str(rn.get("user_agent", cfg.run.user_agent)),
        request_timeout=int(rn.get("request_timeout", 15)),
        max_retries=int(rn.get("max_retries", 3)),
    )

    # ---- ai ----
    ai = raw.get("ai") or {}
    cfg.ai = AIConfig(
        enabled=bool(ai.get("enabled", True)),
        base_url=str(ai.get("base_url", cfg.ai.base_url)).rstrip("/"),
        model=str(ai.get("model", cfg.ai.model)),
        temperature=float(ai.get("temperature", cfg.ai.temperature)),
        max_output_tokens=int(ai.get("max_output_tokens", cfg.ai.max_output_tokens)),
        max_candidates=int(ai.get("max_candidates", cfg.ai.max_candidates)),
        timeout=int(ai.get("timeout", cfg.ai.timeout)),
    )

    # ---- secrets from environment ----
    cfg.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    cfg.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    cfg.cryptopanic_api_key = os.getenv("CRYPTOPANIC_API_KEY", "").strip()
    cfg.coingecko_api_key = os.getenv("COINGECKO_API_KEY", "").strip()

    # env overrides for the AI layer (secret must live in env, never in YAML)
    cfg.ai_api_key = os.getenv("AI_API_KEY", "").strip()
    if os.getenv("AI_API_BASE", "").strip():
        cfg.ai.base_url = os.getenv("AI_API_BASE", "").strip().rstrip("/")
    if os.getenv("AI_MODEL", "").strip():
        cfg.ai.model = os.getenv("AI_MODEL", "").strip()

    # ---- signals ----
    sg = raw.get("signals") or {}
    cfg.signals = SignalsConfig(
        enabled=bool(sg.get("enabled", True)),
        top_candidates=int(sg.get("top_candidates", 8)),
        max_signals_per_run=int(sg.get("max_signals_per_run", 3)),
        kline_interval=str(sg.get("kline_interval", "1h")),
        kline_limit=int(sg.get("kline_limit", 300)),
        min_confidence=int(sg.get("min_confidence", 45)),
        min_score_gap=int(sg.get("min_score_gap", 15)),
        atr_sl_multiplier=float(sg.get("atr_sl_multiplier", 1.5)),
        min_atr_pct=float(sg.get("min_atr_pct", 0.3)),
        max_atr_pct=float(sg.get("max_atr_pct", 8.0)),
        trend_gate=bool(sg.get("trend_gate", True)),
        use_ai_fusion=bool(sg.get("use_ai_fusion", True)),
        ai_confidence_bonus=int(sg.get("ai_confidence_bonus", 20)),
        ai_veto=bool(sg.get("ai_veto", True)),
        cooldown_hours=int(sg.get("cooldown_hours", 12)),
        max_age_hours=int(sg.get("max_age_hours", 72)),
    )

    # ---- risk ----
    rk = raw.get("risk") or {}
    tpw = rk.get("tp_weights")
    cfg.risk = RiskConfig(
        equity_usd=float(rk.get("equity_usd", 1_000.0)),
        risk_per_trade_pct=float(rk.get("risk_per_trade_pct", 1.0)),
        max_notional_pct=float(rk.get("max_notional_pct", 30.0)),
        min_rr=float(rk.get("min_rr", 1.5)),
        max_open_trades=int(rk.get("max_open_trades", 5)),
        max_daily_loss_r=float(rk.get("max_daily_loss_r", 3.0)),
        fee_r_per_round_trip=float(rk.get("fee_r_per_round_trip", 0.06)),
        tp_weights=[float(x) for x in tpw] if tpw else cfg.risk.tp_weights,
    )

    # ---- paper ----
    pp = raw.get("paper") or {}
    cfg.paper = PaperConfig(
        enabled=bool(pp.get("enabled", True)),
        journal_path=str(pp.get("journal_path", cfg.paper.journal_path)),
        daily_report=bool(pp.get("daily_report", True)),
    )

    # ---- market data edge ----
    md = raw.get("market_data") or {}
    cfg.market_data = MarketDataConfig(
        enabled=bool(md.get("enabled", True)),
        depth_limit=int(md.get("depth_limit", 100)),
        lsr_period=str(md.get("lsr_period", "1h")),
        oi_period=str(md.get("oi_period", "1h")),
        fng_enabled=bool(md.get("fng_enabled", True)),
        max_workers=int(md.get("max_workers", 6)),
    )

    # ---- dashboard ----
    db = raw.get("dashboard") or {}
    cfg.dashboard = DashboardConfig(
        enabled=bool(db.get("enabled", True)),
        output_path=str(db.get("output_path", cfg.dashboard.output_path)),
    )

    # ---- backtest ----
    bt = raw.get("backtest") or {}
    cfg.backtest = BacktestConfig(
        default_pairs=[str(p).upper() for p in (bt.get("default_pairs") or cfg.backtest.default_pairs)],
        days=int(bt.get("days", 120)),
        interval=str(bt.get("interval", "1h")),
        max_age_bars=int(bt.get("max_age_bars", 72)),
        warmup_bars=int(bt.get("warmup_bars", 210)),
    )

    return cfg
