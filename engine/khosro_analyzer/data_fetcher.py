import requests, time
from datetime import datetime, timedelta, timezone

KUCOIN_URL='https://api.kucoin.com/api/v1/market/candles'
INTERVAL_MAP={'5m':'5min','15m':'15min','30m':'30min','1h':'1hour','4h':'4hour'}

def fetch_kucoin_klines(symbol, interval='5m', days=3):
    """Fetch paginated KuCoin candles. KuCoin returns at most 1500 rows/request."""
    if interval not in INTERVAL_MAP: raise ValueError('unsupported interval')
    step={'5m':300,'15m':900,'30m':1800,'1h':3600,'4h':14400}[interval]
    end=int(datetime.now(timezone.utc).timestamp()); start=end-days*86400
    all_rows=[]; cursor=start
    while cursor<end:
        page_end=min(end,cursor+step*1490)
        params={'symbol':symbol,'type':INTERVAL_MAP[interval],'startAt':cursor,'endAt':page_end}
        for attempt in range(4):
            try:
                r=requests.get(KUCOIN_URL,params=params,timeout=30)
                if r.status_code==429: time.sleep(2**attempt); continue
                r.raise_for_status(); raw=r.json().get('data',[]); break
            except Exception:
                raw=[]
                if attempt==3: raise
                time.sleep(2**attempt)
        rows=[{'t':int(x[0]),'o':float(x[1]),'c':float(x[2]),'h':float(x[3]),'l':float(x[4]),'v':float(x[5])} for x in raw]
        all_rows.extend(rows)
        if not rows: break
        newest=max(x['t'] for x in rows)
        if newest < cursor+step: break
        cursor=newest+step
    unique={x['t']:x for x in all_rows}
    return [unique[t] for t in sorted(unique)]


def fetch_all_timeframes(symbol, days=180):
    return {tf:fetch_kucoin_klines(symbol,tf,days) for tf in INTERVAL_MAP}
