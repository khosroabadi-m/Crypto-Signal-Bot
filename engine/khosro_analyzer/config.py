import os

VERSION = "11.3.0"
STRATEGY_NAME = "Khosro Confluence Engine + AI Committee"
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

SYMBOLS = [
    'BTC-USDT','ETH-USDT','BNB-USDT','SOL-USDT','XRP-USDT','ADA-USDT','DOGE-USDT',
    'DOT-USDT','POL-USDT','LTC-USDT','TRX-USDT','AVAX-USDT','ATOM-USDT','XLM-USDT',
    'NEAR-USDT','APT-USDT','ARB-USDT','OP-USDT','SUI-USDT','FIL-USDT','XAUT-USDT'
]

TIMEFRAMES = ('5m','15m','30m','1h','4h')
RESOLUTION_TIMEFRAME = '1m'
RESOLUTION_LOOKBACK_DAYS = 10
# Rolling local SQLite market-data cache for later 1m backtests.
CANDLE_DB_PATH = os.getenv('CANDLE_DB_PATH', 'market_data.db')
# v11.3: 1m candles are only needed for trade resolution (10d lookback) and
# short-horizon backtests. 90 days made market_data.db grow ~3.7MB/day until it
# crossed the 100MB GitHub push limit. 12 days ≈ 45MB steady-state.
CANDLE_RETENTION_DAYS = 12
# Hard size guard: if the SQLite file exceeds this, retention is temporarily
# tightened (DB_EMERGENCY_RETENTION_DAYS) and the file is VACUUMed.
DB_MAX_FILE_MB = 80
DB_EMERGENCY_RETENTION_DAYS = 5
TELEGRAM_MIN_INTERVAL_SECONDS = 3.20
TELEGRAM_MAX_RETRIES = 5
BASE_TIMEFRAME = '30m'
FORBIDDEN_HOURS_START = 0
FORBIDDEN_HOURS_END = 4
MAX_DAILY_SIGNALS = 30

# ---------------------------------------------------------------------------
# v11.2 stop-loss risk caps (fixes -35%/-68% margin blowups of the structural
# stop). Applies to BOTH the ATR stop and the structure-aware stop:
#   stop distance ∈ [STOP_MIN_PCT, min(STOP_MAX_PCT, STRUCT_STOP_MAX_ATR × ATR)]
# Worst-case loss at SL ≈ LEVERAGE × STOP_MAX_PCT = 10 × 3% = 30% of margin.
# ---------------------------------------------------------------------------
STOP_MAX_PCT = 0.030        # hard cap: SL never farther than 3% from entry
STOP_MIN_PCT = 0.005        # floor: 0.5% — protects against noise stopouts
STRUCT_STOP_MAX_ATR = 2.5   # structural stop may reach at most 2.5×ATR

# v11.3: stale-OPEN guard. Signals still OPEN after this many hours are closed
# as EXPIRED at the current 1m price so the symbol is not muted forever.
# Set 0 to disable.
SIGNAL_MAX_AGE_HOURS = 96

# v11 weight model — user-defined priority (highest to lowest):
# AI committee > order flow > volume profile > sweep > liquidity > FVG >
# fibonacci > supply/demand > moving average > MACD > RSI > patterns.
# Sum is exactly 100. HTF trend (4h/1h) and volatility act as hard gates,
# not as weighted components.
WEIGHTS = {
    'ai_committee': 25,
    'order_flow': 14,
    'volume_profile': 11,
    'sweep': 10,
    'liquidity': 8,
    'fvg': 7,
    'fibonacci': 6,
    'supply_demand': 5,
    'moving_average': 5,
    'macd': 4,
    'rsi': 3,
    'pattern': 2,
}
assert abs(sum(WEIGHTS.values()) - 100) < 1e-9, 'WEIGHTS must sum to 100'

# ---------------------------------------------------------------------------
# AI committee — each provider votes independently; weights sum to 100
# ---------------------------------------------------------------------------
AI_PROVIDERS = [
    {
        'name': 'openrouter',
        'url': 'https://openrouter.ai/api/v1/chat/completions',
        'model': 'nvidia/nemotron-3-super-120b-a12b:free',
        'fallback_models': [
            'z-ai/glm-5.2:free',
            'google/gemma-4-31b-it:free',
            'nex-agi/nex-n2.5-mini:free',
        ],
        'key_env': 'OPENROUTER_API_KEY',
        'weight': 18,
        'retry_429': True,
        'enabled': True,
    },
    {
        'name': 'mistral',
        'url': 'https://api.mistral.ai/v1/chat/completions',
        'model': 'open-mistral-nemo',
        'key_env': 'MISTRAL_API_KEY',
        'weight': 14,
        'retry_429': True,
        'enabled': True,
    },
    {
        'name': 'cerebras',
        'url': 'https://api.cerebras.ai/v1/chat/completions',
        'model': 'llama3.1-8b',
        'fallback_models': ['llama-3.3-70b', 'qwen-3-32b'],
        'key_env': 'CEREBRAS_API_KEY',
        'weight': 14,
        'retry_429': False,
        'enabled': True,
    },
    {
        'name': 'groq',
        'url': 'https://api.groq.com/openai/v1/chat/completions',
        'model': 'openai/gpt-oss-20b',
        'fallback_models': ['llama-3.3-70b-versatile', 'llama-3.1-8b-instant'],
        'key_env': 'GROQ_API_KEY',
        'weight': 16,
        'retry_429': True,
        'enabled': True,
    },
    {
        'name': 'sambanova',
        'url': 'https://api.sambanova.ai/v1/chat/completions',
        'model': 'Meta-Llama-3.3-70B-Instruct',
        'key_env': 'SAMBANOVA_API_KEY',
        'weight': 14,
        'retry_429': True,
        'enabled': True,
    },
    {
        'name': 'siliconflow',
        'url': 'https://api.siliconflow.com/v1/chat/completions',
        'model': 'Qwen/Qwen2.5-7B-Instruct',
        'key_env': 'SILICONFLOW_API_KEY',
        'weight': 12,
        'retry_429': False,
        'enabled': True,
    },
    {
        # Workers AI — needs CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID
        'name': 'cloudflare',
        'url': None,  # built at runtime from account id
        'url_template': 'https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1/chat/completions',
        'model': '@cf/meta/llama-3.1-8b-instruct-fast',
        'fallback_models': ['@cf/meta/llama-3.3-70b-instruct-fp8-fast', '@cf/meta/llama-4-scout-17b-16e-instruct'],
        'key_env': 'CLOUDFLARE_API_TOKEN',
        'extra_env': {'account_id': 'CLOUDFLARE_ACCOUNT_ID'},
        'weight': 12,
        'retry_429': True,
        'enabled': True,
    },
]


AI_SYSTEM_PROMPT = (
    'You are a disciplined crypto trade reviewer. You receive one proposed trade '
    'with component scores and evidence. Approve only when the evidence clearly '
    'supports the trade AND respects the trader rules. Be strict: low-quality '
    'setups must be rejected. Reply ONLY with a single JSON object, no prose.'
)
AI_RULES_TEXT = (
    '1) Never approve against the higher-timeframe trend. '
    '2) Reject when volatility (ATR%) is extreme. '
    '3) Demand confluence: order flow or volume profile must agree. '
    '4) Reject when R:R is below 2. '
    '5) Prefer setups where price interacts with an unmitigated zone (FVG, '
    'supply/demand, sweep) instead of chasing extended moves. '
    '6) Be extra skeptical of SHORT setups: live ledger shows shorts losing; '
    'approve a SHORT only with strong bearish order flow AND volume profile '
    'AND a clear lower-high structure.'
)

# Rule-only score (0..100, AI excluded) required BEFORE the AI committee is consulted.
# Keeps the free API quotas safe: weak candidates never reach the committee.
AI_TRIGGER_RULE_SCORE = 60
# Committee combined score (0..1) required for the AI component to count as approving.
AI_MIN_COMMITTEE_SCORE = 0.55
# A rejected vote contributes (100 - confidence)/100 * AI_REJECT_DECAY to the score,
# so an uncertain reject still leaves some room while a confident reject is near zero.
AI_REJECT_DECAY = 0.5
AI_TEMPERATURE = 0.2
AI_MAX_TOKENS = 700    # reasoning-style free models burn tokens before the JSON
AI_TIMEOUT_SECONDS = 35
AI_MAX_CALLS_PER_RUN = 6
AI_DAILY_BUDGET = 50   # committee calls per Tehran day

THRESHOLDS = {
    'low_score': 78,
    'medium_score': 68,
    'signal_score': 62,
    'strong_adx': 25,
    'weak_adx': 18,
    'volume_ratio': 1.20,
    'level_tolerance': 0.004,
    'max_atr_pct': 0.06,
    'min_rr': 2.0,
    # v11.2 short-only hard gates (live ledger: SHORT WR 0-14%, net negative).
    # Shorts need both HTF trends clearly bearish and a higher final score.
    'short_htf_min': 0.60,
    'short_signal_score': 72,
}

RISK_PARAMS = {
    'LOW': {'atr_sl': 1.4, 'rr': 2.4},
    'MEDIUM': {'atr_sl': 1.6, 'rr': 2.1},
    'HIGH': {'atr_sl': 1.9, 'rr': 2.0},
}

BROKER_FEE_RATE = 0.001
SLIPPAGE_PCT = 0.0005
POSITION_SIZE_USD = 10.0
# Per-signal capital model: $10 margin opened with 10x leverage = $100 notional.
MARGIN_USD = 10.0
LEVERAGE = 10.0
NOTIONAL_USD = MARGIN_USD * LEVERAGE

# Sanity guards for the v11.2 risk model.
assert 0 < STOP_MIN_PCT < STOP_MAX_PCT < 0.10, 'stop caps out of sane range'
assert STRUCT_STOP_MAX_ATR >= 1.0, 'structural stop cap must allow >= 1xATR'
assert DB_EMERGENCY_RETENTION_DAYS < CANDLE_RETENTION_DAYS
