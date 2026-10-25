"""config.py — Titan Signal Configuration (V4.2.0 OperationalWindows)

Trailing stop system with 10x leverage, $10 margin.
V3.1: RSI zone filter applied ONLY on S1 Prometheus.
V4.2.0: S3 Cronus → LONG-only; Operational time windows (7:00–18:00 / 18:00–02:00 / 02:00 force-close)
"""
import os

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')

# ============================================================
# Financial Constants — MUST match backtest exactly
# ============================================================
LEVERAGE = 10
MARGIN_USD = 10.0
POSITION_USD = MARGIN_USD * LEVERAGE  # $100
FEE_PER_SIDE = 0.001  # 0.1%
FEE_ROUND = FEE_PER_SIDE * 2  # 0.2% round-trip on position
# Fee per trade = POSITION_USD * FEE_ROUND = $0.20
FEE_PER_TRADE = POSITION_USD * FEE_ROUND

SYMBOLS = [
    'BTC-USDT', 'ETH-USDT', 'BNB-USDT', 'SOL-USDT', 'XRP-USDT',
    'XAUT-USDT', 'LTC-USDT', 'DOGE-USDT', 'SUI-USDT', 'NEAR-USDT',
    'DOT-USDT', 'ADA-USDT', 'LINK-USDT', 'AVAX-USDT', 'ATOM-USDT',
    'FIL-USDT', 'INJ-USDT', 'SEI-USDT', 'TIA-USDT', 'POL-USDT', 'OP-USDT',
    'PEPE-USDT', 'SHIB-USDT',
]

RISK_LEVELS = [
    {'key': 'LOW', 'name': 'Low Risk', 'emoji': '∞', 'rules': {
        'trend_4h_emas': [21, 55, 200], 'trend_1h_emas': [21, 55],
        'candle_15m_strength': 0.6, 'candle_5m_strength': 0.6,
        'rsi_threshold_count': 5, 'macd_threshold_count': 5, 'entry_break_threshold': 0.0,
    }},
    {'key': 'MEDIUM', 'name': 'Medium Risk', 'emoji': '♿', 'rules': {
        'trend_4h_emas': [21, 55], 'trend_1h_emas': [21, 55],
        'candle_15m_strength': 0.48, 'candle_5m_strength': 0.48,
        'rsi_threshold_count': 4, 'macd_threshold_count': 4, 'entry_break_threshold': 0.003,
    }},
    {'key': 'HIGH', 'name': 'High Risk', 'emoji': '⛔', 'rules': {
        'trend_4h_emas': [21], 'trend_1h_emas': [21, 55],
        'candle_15m_strength': 0.35, 'candle_5m_strength': 0.35,
        'rsi_threshold_count': 3, 'macd_threshold_count': 3, 'entry_break_threshold': 0.003,
    }},
]

RISK_PARAMS = {'atr_multiplier': 1.2, 'rr_target': 2.0, 'swing_lookback': 10, 'rr_fallback': 2.0}

RISK_FACTORS = {
    'LOW': {'ADX': 3, 'CCI': 2, 'SAR': 3, 'Stoch': 2, 'TF_Big': 4, 'Patterns': 2, 'RiskMgmt': 4, 'Volume': 2, 'Candles': 2, 'EMA': 2, 'Confirm': 3, 'Pressure': 3},
    'MEDIUM': {'ADX': 2, 'CCI': 3, 'SAR': 2, 'Stoch': 3, 'TF_Big': 3, 'Patterns': 3, 'RiskMgmt': 3, 'Volume': 2, 'Candles': 2, 'EMA': 2, 'Confirm': 3, 'Pressure': 3},
    'HIGH': {'ADX': 1, 'CCI': 4, 'SAR': 1, 'Stoch': 4, 'TF_Big': 1, 'Patterns': 4, 'RiskMgmt': 2, 'Volume': 3, 'Candles': 3, 'EMA': 1, 'Confirm': 2, 'Pressure': 4},
}

INDICATOR_THRESHOLDS = {'adx_min': 22, 'rsi_oversold': 30, 'rsi_overbought': 70}

# ============================================================
# V3.1 RSI Zone Filter — applied ONLY on S1
# ============================================================
RSI_LONG_ZONE = (40, 65)
RSI_SHORT_ZONE = (35, 55)

# ============================================================
# SCENARIOS — Trailing Stop Config (from backtest optimization)
# ============================================================
# CRITICAL: sl_pct, trail_activate, trail_lock, max_hold_candles
# must match the backtest EXACTLY.
# Exit logic in monitor.py uses these values.
# ============================================================

SCENARIOS = {
    'S1': {
        'id': 'S1',
        'name_fa': 'پرومتئوس', 'name_en': 'Prometheus',
        'behavior_fa': 'سوار ترند صعودی', 'behavior_en': 'Long Trend Rider',
        'titan_desc_fa': 'تایتان پیش‌اندیشی - با دید آینده‌نگر ترندهای صعودی را شکار می‌کند',
        # --- Trailing stop params (optimized, from backtest) ---
        'sl_pct': 0.040,
        'trail_activate': 0.003,  # 0.3% move activates trail
        'trail_lock': 0.90,       # lock 90% of max favorable excursion
        'max_hold_candles': 72,  # 72 × 30min = 36 hours
        'score_min': 50,
        # --- Signal generation ---
        'direction_only': 'LONG', 'rule_profile': 'strict', 'range_filter_mode': 'AND',
        'extra_filters': {'min_adx': 25, 'require_di_align': True},
        'cooldown_seconds': 14400, 'symbols_list': SYMBOLS,
        'weight_threshold': 0.40, 'min_passed_rules': 5,
        'leverage': LEVERAGE, 'margin_usd': MARGIN_USD,
        # --- V3.1 filter ---
        'allowed_filters': ['rsi'],  # only RSI zone filter on S1
    },
    'S2': {
        'id': 'S2',
        'name_fa': 'ایاپتوس', 'name_en': 'Iapetus',
        'behavior_fa': 'شکارنده نزولی', 'behavior_en': 'Short Hunter',
        'titan_desc_fa': 'تایتان تیرانداز - با دقت ترندهای نزولی را هدف قرار می‌دهد',
        'sl_pct': 0.040,
        'trail_activate': 0.006,
        'trail_lock': 0.90,
        'max_hold_candles': 72,
        'score_min': 50,
        'direction_only': 'SHORT', 'rule_profile': 'strict', 'range_filter_mode': 'AND',
        'extra_filters': {'min_adx': 25, 'require_di_align': True},
        'cooldown_seconds': 14400, 'symbols_list': SYMBOLS,
        'weight_threshold': 0.40, 'min_passed_rules': 5,
        'leverage': LEVERAGE, 'margin_usd': MARGIN_USD,
        'allowed_filters': [],  # NO V3.1 filter — pure V1
    },
    'S3': {
        'id': 'S3',
        'name_fa': 'کرونوس', 'name_en': 'Cronus',
        'behavior_fa': 'فرمانروای صعودی', 'behavior_en': 'Long-Only Ruler',
        'titan_desc_fa': 'پادشاه تایتان‌ها - فقط ترندهای صعودی را شکار می‌کند (SHORT غیرفعال)',
        'sl_pct': 0.040,
        'trail_activate': 0.003,
        'trail_lock': 0.90,
        'max_hold_candles': 120,  # 120 × 30min = 60 hours
        'score_min': 50,
        'direction_only': 'LONG', 'rule_profile': 'strict', 'range_filter_mode': 'AND',
        'extra_filters': None,
        'cooldown_seconds': 21600, 'symbols_list': SYMBOLS,
        'weight_threshold': 0.40, 'min_passed_rules': 5,
        'leverage': LEVERAGE, 'margin_usd': MARGIN_USD,
        'allowed_filters': [],
    },
    'B1': {
        'id': 'B1',
        'name_fa': 'هایپریون', 'name_en': 'Hyperion',
        'behavior_fa': 'نورافکن صعودی', 'behavior_en': 'Bullish Light Bringer',
        'titan_desc_fa': 'تایتان نور - با درک وسیع مسیرهای صعودی را روشن می‌کند',
        'sl_pct': 0.040,
        'trail_activate': 0.003,
        'trail_lock': 0.90,
        'max_hold_candles': 96,  # 96 × 30min = 48 hours
        'score_min': 50,
        'direction_only': 'LONG', 'rule_profile': 'strict', 'range_filter_mode': 'OR',
        'extra_filters': None,
        'cooldown_seconds': 14400, 'symbols_list': SYMBOLS,
        'weight_threshold': 0.40, 'min_passed_rules': 5,
        'leverage': LEVERAGE, 'margin_usd': MARGIN_USD,
        'allowed_filters': [],
    },
    'B2': {
        'id': 'B2',
        'name_fa': 'اطلس', 'name_en': 'Atlas',
        'behavior_fa': 'ستون سنگی نزولی', 'behavior_en': 'Short Pillar',
        'titan_desc_fa': 'تایتان حامل آسمان - با استقامت در پوزیشن‌های نزولی می‌ماند',
        'sl_pct': 0.040,
        'trail_activate': 0.020,  # 2% activation — wider for shorts
        'trail_lock': 0.90,
        'max_hold_candles': 120,  # 120 × 30min = 60 hours
        'score_min': 50,
        'direction_only': 'SHORT', 'rule_profile': 'balanced', 'range_filter_mode': 'OR',
        'extra_filters': {'min_body_strength': 0.5, 'min_adx': 22, 'require_di_align': True},
        'cooldown_seconds': 14400, 'symbols_list': SYMBOLS,
        'weight_threshold': 0.40, 'min_passed_rules': 5,
        'leverage': LEVERAGE, 'margin_usd': MARGIN_USD,
        'allowed_filters': [],
    },
}
ACTIVE_SCENARIOS = ['S1', 'S3', 'B1']  # S2/B2 disabled; S3 LONG-only

# ============================================================
# V4.2.0 Operational Time Windows (Tehran timezone)
# ============================================================
# Signal issuance:   07:00 – 18:00 Tehran
# Management only:   18:00 – 02:00+1 Tehran
# Force close:       02:00+1 Tehran (in monitor.py final mode)
SIGNAL_WINDOW_START = 7    # 07:00 Tehran — start issuing new signals
SIGNAL_WINDOW_END = 18     # 18:00 Tehran — stop issuing, management only
FORCE_CLOSE_HOUR = 2       # 02:00 Tehran — force-close all remaining opens


def scenario_display_name(sc):
    return f"{sc['name_fa']} ({sc['name_en']})"
