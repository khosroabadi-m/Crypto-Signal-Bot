from typing import Dict, Optional
from datetime import datetime, timedelta, timezone
import requests
import time

KUCOIN_URL = "https://api.kucoin.com/api/v1/market/candles"

INTERVAL_MAP = {
    '5m': '5min', '15m': '15min', '30m': '30min',
    '1h': '1hour', '4h': '4hour',
}

DEFAULT_DAYS = {'5m': 3, '15m': 5, '30m': 7, '1h': 14, '4h': 45}


def fetch_kucoin_klines(symbol: str, interval: str = '5min', days: int = 3, retries: int = 3) -> Optional[list]:
    """Fetch kline data from KuCoin API."""
    kucoin_interval = INTERVAL_MAP.get(interval, interval)
    end_time = int(datetime.now(timezone.utc).timestamp())
    start_time = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    params = {'symbol': symbol, 'type': kucoin_interval, 'startAt': start_time, 'endAt': end_time}

    for attempt in range(retries):
        try:
            r = requests.get(KUCOIN_URL, params=params, timeout=20)
            if r.status_code == 200:
                data = r.json().get('data', [])
                candles = [
                    {'t': int(c[0]), 'o': float(c[1]), 'c': float(c[2]),
                     'h': float(c[3]), 'l': float(c[4]), 'v': float(c[5])}
                    for c in data
                ]
                return list(reversed(candles))
            elif r.status_code == 429:
                time.sleep(10 * (attempt + 1))
            else:
                return None
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(5)
    return None


def fetch_all_timeframes(symbol: str, days_config: Dict[str, int] = None) -> dict:
    """Fetch all required timeframes for a symbol."""
    settings = days_config or DEFAULT_DAYS
    data = {}
    for tf, days in settings.items():
        candles = fetch_kucoin_klines(symbol, tf, days)
        if candles and len(candles) >= 50:
            data[tf] = candles
        time.sleep(0.3)
    return data
