import argparse,csv,os
from data_fetcher import fetch_kucoin_klines

def main():
    p=argparse.ArgumentParser(); p.add_argument('--symbol',default='BTC-USDT'); p.add_argument('--days',type=int,default=180); p.add_argument('--out-dir',default='backtest_data'); a=p.parse_args()
    os.makedirs(a.out_dir,exist_ok=True)
    rows=fetch_kucoin_klines(a.symbol,'5m',a.days)
    if not rows: raise SystemExit('No data returned')
    path=os.path.join(a.out_dir,a.symbol.replace('-','')+'.csv')
    with open(path,'w',newline='',encoding='utf-8') as f:
        w=csv.writer(f); w.writerow(['open_time','open','high','low','close','volume'])
        for x in rows: w.writerow([x['t'],x['o'],x['h'],x['l'],x['c'],x['v']])
    print(f'Saved {len(rows)} candles to {path}')
if __name__=='__main__': main()
