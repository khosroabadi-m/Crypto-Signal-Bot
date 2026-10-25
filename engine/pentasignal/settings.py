# settings.py — PentaSignal v3.5.5
# تنظیمات سراسری: نسخه، مسیرها، پنجره‌های زمانی تهران، کارمزد و سایز پوزیشن

import os

VERSION = "3.5.5"

# ---------- مسیرها ----------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # root پروژه
# برای تست/شبیه‌سازی می‌توان دایرکتوری داده ایزوله تعریف کرد: PS_DATA_DIR=/path
DATA_DIR = os.environ.get("PS_DATA_DIR") or os.path.join(BASE_DIR, "data")
SIGNALS_DIR = os.path.join(DATA_DIR, "signals")
# سازگاری نام‌های قدیمی (دیگر استفاده عملی ندارند)
SIGNALS_CSV = os.path.join(DATA_DIR, "signals.csv")
EVENTS_CSV = os.path.join(DATA_DIR, "events.csv")
EVENTS_DIR = os.path.join(DATA_DIR, "events")
REPORTS_DIR = os.path.join(DATA_DIR, "reports")
ARCHIVE_DIR = os.path.join(DATA_DIR, "archive")
STATE_JSON = os.path.join(DATA_DIR, "state.json")
CACHE_DIR = os.path.join(DATA_DIR, "cache")
LOG_FILE = os.path.join(DATA_DIR, "bot_log.txt")

for _d in (DATA_DIR, SIGNALS_DIR, REPORTS_DIR, ARCHIVE_DIR, CACHE_DIR):
    os.makedirs(_d, exist_ok=True)

# ---------- تلگرام ----------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
# DRY_RUN=1 → پیام‌ها به‌جای ارسال واقعی، در کنسول و فایل لاگ می‌روند
DRY_RUN = os.getenv("DRY_RUN", "1") == "1"

# ---------- بازار / KuCoin ----------
KUCOIN_BASE = "https://api.kucoin.com"
KUCOIN_CANDLES = "/api/v1/market/candles"
KUCOIN_TICKER = "/api/v1/market/orderbook/level1"
CANDLE_TYPE_30M = "30min"
CANDLE_TYPE_4H = "4hour"

# ---------- زمان‌بندی (تهران) ----------
NEW_SIGNAL_START_HOUR = 7    # inclusive
NEW_SIGNAL_END_HOUR = 20     # exclusive → آخرین کندلِ مجاز: بسته‌شده در 19:30
NIGHTLY_REPORT_HOUR = 2      # گزارش شبانه دقیقاً ساعت 02:00 تهران
SETTLE_ONLY_START_HOUR = 20  # از 20:00 تا 01:00 فقط تعیین تکلیف
SETTLE_ONLY_END_HOUR = 1     # پایان پنجره تسویه در 01:00 روز بعد
SETTLE_ONLY_AFTER_HOUR = 20  # سازگاری با کدهای قدیمی

# ---------- ریسک ----------
POSITION_SIZE_USD = 10.0
FEE_RT = 0.002               # کارمزد رفت‌وبرگشت 0.2٪
LOOKBACK_DAYS_30M = 32
LOOKBACK_DAYS_4H = 35

# ---------- گاردهای پرتفویی W2 (از v3.3.0) ----------
PORTFOLIO_GUARDS = os.getenv("PORTFOLIO_GUARDS", "1") == "1"
MAX_OPEN_TRADES = int(os.getenv("MAX_OPEN_TRADES", "5"))
MAX_DAILY_LOSS_R = float(os.getenv("MAX_DAILY_LOSS_R", "3.0"))

# ---------- کشف ترند F1 بدون AI (از v3.5.1) ----------
# CoinGecko /search/trending + مومنتوم/حجم KuCoin → top-N ∪ ارزهای اصلی
F1_DISCOVERY = os.getenv("F1_DISCOVERY", "1") == "1"
F1_MAIN_COINS = [s.strip().upper() for s in os.getenv(
    "F1_MAIN_COINS", "BTC,ETH,BNB,SOL,XRP").split(",") if s.strip()]
F1_DISCOVERY_TOP_N = int(os.getenv("F1_DISCOVERY_TOP_N", "8"))
F1_DISCOVERY_TIMEOUT = int(os.getenv("F1_DISCOVERY_TIMEOUT", "8"))
# چند نماد فقط‌از‌ترند CoinGecko (خارج از استخر F2–F5) برای F1
F1_TREND_EXTRA_N = int(os.getenv("F1_TREND_EXTRA_N", "5"))
# لبه depth/funding بایننس (اختیاری). روی GitHub Actions معمولاً 451 است → پیش‌فرض خاموش
_F1_MD_DEFAULT = "0" if os.getenv("GITHUB_ACTIONS") == "true" else "1"
F1_BINANCE_MD = os.getenv("F1_BINANCE_MD", _F1_MD_DEFAULT) == "1"

# ---------- نگهداری CSV ----------
CSV_KEEP_DAYS = 90

# ---------- آرشیو OHLCV یک‌دقیقه‌ای ----------
OHLCV_RECENT_MINUTES = 45
OHLCV_RETENTION_DAYS = 90
OHLCV_BACKFILL_CHUNK_MINUTES = 720

# ---------- اجرا ----------
LOOP_TICK_SECONDS = 20
CANDLE_CLOSE_SKEW = 4
