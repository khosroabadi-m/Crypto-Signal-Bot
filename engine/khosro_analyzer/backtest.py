"""Event-driven backtest for KhosroSignalAnalyzerBot v10.2.

Strategy decisions are made from 5m-derived 15m/30m/1h/4h candles. Trade
execution is resolved on 1m candles, matching the live bot. Input can be either
5m CSV (legacy mode) or the local market_data.db 1m SQLite cache.
"""
from __future__ import annotations
import argparse, csv, os, math
from collections import defaultdict

from rules import analyze_market
from config import RISK_PARAMS, BROKER_FEE_RATE, SLIPPAGE_PCT
from candle_store import load_candles


def load_csv(path):
    rows=[]
    with open(path,newline='',encoding='utf-8') as f:
        reader=csv.reader(f); first=next(reader)
        named=first and any(x.lower() in ('open','close','timestamp','open_time') for x in first)
        if named:
            f.seek(0); dr=csv.DictReader(f)
            for r in dr:
                t=r.get('timestamp') or r.get('open_time') or r.get('time')
                rows.append({'t':int(float(t))/1000 if float(t)>10**11 else int(float(t)), 'o':float(r['open']),'h':float(r['high']),'l':float(r['low']),'c':float(r['close']),'v':float(r['volume'])})
        else:
            vals=[first]+list(reader)
            for r in vals:
                if len(r)<6: continue
                rows.append({'t':int(float(r[0]))/1000 if float(r[0])>10**11 else int(float(r[0])), 'o':float(r[1]),'h':float(r[2]),'l':float(r[3]),'c':float(r[4]),'v':float(r[5])})
    rows.sort(key=lambda x:x['t']); return rows


def resample(candles, minutes):
    bucket=minutes*60; groups=defaultdict(list)
    for c in candles: groups[(int(c['t'])//bucket)*bucket].append(c)
    out=[]
    for t,grp in sorted(groups.items()):
        out.append({'t':t,'o':grp[0]['o'],'h':max(x['h'] for x in grp),'l':min(x['l'] for x in grp),'c':grp[-1]['c'],'v':sum(x['v'] for x in grp)})
    return out


def prepare(c1m):
    c5=resample(c1m,5)
    return {'5m':c5,'15m':resample(c5,15),'30m':resample(c5,30),'1h':resample(c5,60),'4h':resample(c5,240)}


def prefix(data, t):
    return {tf:[c for c in cs if c['t'] < t] for tf,cs in data.items()}


def run_symbol(symbol, candles, fee=BROKER_FEE_RATE, slippage=SLIPPAGE_PCT, max_bars=None):
    data=prepare(candles)
    # SQLite mode supplies raw 1m candles. Legacy CSV mode supplies 5m candles.
    # Keep legacy execution semantics for 5m CSVs while using true 1m execution
    # whenever the input cadence is 1 minute.
    c1m=candles
    execution_1m = False
    if len(candles) >= 3:
        deltas=[int(candles[i]['t']-candles[i-1]['t']) for i in range(1,min(len(candles),20)) if candles[i]['t']>candles[i-1]['t']]
        execution_1m = bool(deltas) and min(deltas) <= 60
    base=data['30m']; trades=[]
    if max_bars: base=base[-max_bars:]
    i=0
    while i < len(base)-1:
        cur=base[i]; snapshot=prefix(data,cur['t'])
        if any(len(snapshot.get(tf,[])) < req for tf,req in [('30m',80),('1h',60),('4h',60),('15m',40),('5m',40)]):
            i+=1; continue
        results=[analyze_market(symbol,snapshot,d,'MEDIUM') for d in ('LONG','SHORT')]
        results=[x for x in results if x.get('status')=='SIGNAL']
        if not results: i+=1; continue
        r=max(results,key=lambda x:x['score'])
        entry_candle=base[i+1]
        entry=entry_candle['o']*(1+slippage if r['direction']=='LONG' else 1-slippage)
        risk=r['price']-r['stop_loss'] if r['direction']=='LONG' else r['stop_loss']-r['price']
        if risk<=0: i+=1; continue
        rr=RISK_PARAMS[r['risk']]['rr']; sl=r['stop_loss']; tp=entry+risk*rr if r['direction']=='LONG' else entry-risk*rr
        entry_t=entry_candle['t']; exit_t=None; exit_price=None; outcome=None
        execution_candles = c1m if execution_1m else base
        for future in execution_candles:
            if future['t'] < entry_t: continue
            if r['direction']=='LONG': hit_sl=future['l']<=sl; hit_tp=future['h']>=tp
            else: hit_sl=future['h']>=sl; hit_tp=future['l']<=tp
            if hit_sl and hit_tp: outcome='LOSS'; exit_price=sl; exit_t=future['t']; break
            if hit_sl: outcome='LOSS'; exit_price=sl; exit_t=future['t']; break
            if hit_tp: outcome='WIN'; exit_price=tp; exit_t=future['t']; break
        if outcome is None: break
        exit_price*= (1-slippage if r['direction']=='LONG' else 1+slippage)
        ret=(exit_price-entry)/entry if r['direction']=='LONG' else (entry-exit_price)/entry
        net=ret-fee*2
        trades.append({'symbol':symbol,'time':cur['t'],'direction':r['direction'],'risk':r['risk'],'score':r['score'],'ret':net,'outcome':outcome,'entry_t':entry_t,'exit_t':exit_t})
        # Resume after the 30m bucket containing the exit.
        i=next((k for k,c in enumerate(base) if c['t'] > exit_t), len(base))
    return trades


def report(trades):
    if not trades: return {'trades':0}
    wins=sum(x['outcome']=='WIN' for x in trades); losses=len(trades)-wins; rets=[x['ret'] for x in trades]
    equity=1.0; peak=1.0; maxdd=0.0
    for x in rets: equity*=1+x; peak=max(peak,equity); maxdd=max(maxdd,(peak-equity)/peak)
    avg=sum(rets)/len(rets); gross_win=sum(x for x in rets if x>0); gross_loss=-sum(x for x in rets if x<0); pf=gross_win/gross_loss if gross_loss else math.inf
    byrisk={}
    for risk in ('LOW','MEDIUM','HIGH'):
        q=[x for x in trades if x['risk']==risk]; byrisk[risk]={'trades':len(q),'win_rate':sum(x['outcome']=='WIN' for x in q)/len(q) if q else 0,'return':sum(x['ret'] for x in q)}
    return {'trades':len(trades),'wins':wins,'losses':losses,'win_rate':wins/len(trades),'total_return':equity-1,'avg_trade':avg,'profit_factor':pf,'max_drawdown':maxdd,'by_risk':byrisk}


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--data-dir',default='backtest_data'); ap.add_argument('--db',default=''); ap.add_argument('--symbol',default='BTC-USDT'); ap.add_argument('--max-bars',type=int,default=0); ap.add_argument('--out',default='backtest_report.txt'); args=ap.parse_args()
    if args.db:
        candles=load_candles(args.symbol, db_path=args.db)
        if not candles: raise SystemExit(f'No 1m candles for {args.symbol} in {args.db}.')
    else:
        path=os.path.join(args.data_dir,args.symbol.replace('-','')+'.csv')
        if not os.path.exists(path): path=os.path.join(args.data_dir,args.symbol+'.csv')
        if not os.path.exists(path): raise SystemExit(f'Missing {path}. Use --db market_data.db or provide OHLCV CSV.')
        candles=load_csv(path)
        # Legacy CSV may be 5m; normalize to 1m-equivalent execution input only when truly 1m.
    trades=run_symbol(args.symbol,candles,max_bars=args.max_bars or None)
    rep=report(trades)
    with open(args.out,'w',encoding='utf-8') as f: f.write(str(rep))
    print(rep)

if __name__=='__main__': main()
