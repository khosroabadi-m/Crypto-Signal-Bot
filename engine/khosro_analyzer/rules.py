from __future__ import annotations
import logging
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Optional
import aiohttp

from config import (
    VERSION, STRATEGY_NAME, WEIGHTS, THRESHOLDS, RISK_PARAMS,
    MAX_DAILY_SIGNALS, FORBIDDEN_HOURS_START, FORBIDDEN_HOURS_END,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    STOP_MAX_PCT, STOP_MIN_PCT, STRUCT_STOP_MAX_ATR,
    AI_TRIGGER_RULE_SCORE, AI_RULES_TEXT,
)
from indicators import calculate_ema, calculate_rsi, calculate_macd, calculate_atr, calculate_adx, find_pivots
from patterns import candle_pattern, ema_rejection, breakout_or_retest
from smc import rule_components
from signal_store import append_signal_row, tehran_time_str

logger = logging.getLogger(__name__)
_daily_signal_count = 0
_daily_signal_date = None

@dataclass
class Component:
    name: str
    score: float
    weight: float
    detail: str

    @property
    def contribution(self):
        return self.score * self.weight


def _reset_daily():
    global _daily_signal_count, _daily_signal_date
    d = datetime.now(ZoneInfo('Asia/Tehran')).date()
    if d != _daily_signal_date:
        _daily_signal_date, _daily_signal_count = d, 0


def can_issue_signal():
    """True if another signal may still be issued today (does not consume quota)."""
    _reset_daily()
    return _daily_signal_count < MAX_DAILY_SIGNALS


def record_signal_issued():
    """Consume one slot of the daily signal quota."""
    global _daily_signal_count
    _reset_daily()
    _daily_signal_count += 1


def is_forbidden_hour():
    h = datetime.now(ZoneInfo('Asia/Tehran')).hour
    return FORBIDDEN_HOURS_START <= h < FORBIDDEN_HOURS_END


def _tf_state(candles, direction, ema_fast=21, ema_slow=55):
    if len(candles) < ema_slow + 5:
        return 0.0, 'insufficient data'
    closes = [x['c'] for x in candles]
    ef, es = calculate_ema(closes, ema_fast), calculate_ema(closes, ema_slow)
    price = closes[-1]
    adx, pdi, mdi = calculate_adx(candles)
    score = 0.0
    if direction == 'LONG':
        score += 0.5 if ef > es else 0
        score += 0.35 if price > ef else 0
        score += 0.15 if pdi is not None and mdi is not None and pdi > mdi else 0
    else:
        score += 0.5 if ef < es else 0
        score += 0.35 if price < ef else 0
        score += 0.15 if pdi is not None and mdi is not None and mdi > pdi else 0
    return score, f'price={price:.6g} EMA{ema_fast}={ef:.6g} EMA{ema_slow}={es:.6g} ADX={adx if adx is not None else 0:.1f}'


def _levels(candles):
    highs, lows = find_pivots(candles[-120:])
    return (highs[-1][1] if highs else None), (lows[-1][1] if lows else None)


def _level_score(candles, direction):
    if len(candles) < 30:
        return 0.0, 'insufficient data', None, None
    price = candles[-1]['c']
    resistance, support = _levels(candles)
    tol = THRESHOLDS['level_tolerance']
    if direction == 'LONG':
        level = support
        if level is None:
            return 0.3, 'no support pivot', None, resistance
        dist = abs(price-level)/price
        score = 1.0 if dist <= tol else 0.65 if dist <= tol*2.0 else 0.25
        return score, f'support={level:.6g}, distance={dist:.3%}', level, resistance
    level = resistance
    if level is None:
        return 0.3, 'no resistance pivot', support, None
    dist = abs(price-level)/price
    score = 1.0 if dist <= tol else 0.65 if dist <= tol*2.0 else 0.25
    return score, f'resistance={level:.6g}, distance={dist:.3%}', support, level


def _pattern_score(candles, direction, level):
    pat = candle_pattern(candles)
    score = 0.35
    if pat:
        bullish = pat in ('HAMMER','BULLISH_ENGULFING')
        bearish = pat in ('SHOOTING_STAR','BEARISH_ENGULFING')
        if (direction == 'LONG' and bullish) or (direction == 'SHORT' and bearish):
            score = 1.0
        elif (direction == 'LONG' and bearish) or (direction == 'SHORT' and bullish):
            score = 0.0
        else:
            score = 0.5
    if level is not None and ema_rejection(candles, level, direction):
        score = min(1.0, score + 0.2)
    return score, f'pattern={pat or "NONE"}'


def _atr_levels(candles, direction, risk):
    price=candles[-1]['c']; atr=calculate_atr(candles)
    if atr is None or atr <= 0: return None,None,None
    p=RISK_PARAMS[risk]
    if direction=='LONG':
        sl=price-atr*p['atr_sl']; tp=price+(price-sl)*p['rr']
    else:
        sl=price+atr*p['atr_sl']; tp=price-(sl-price)*p['rr']
    return sl,tp,atr


def _structural_stop(candles, direction, level, risk):
    """v11.2 structure-aware stop, symmetric for LONG/SHORT.

    The ATR stop is replaced by a stop just beyond the structural pivot only
    when the structure actually ADDS room (>= 0.8x the ATR-stop distance).
    The old v11.1 logic replaced the stop unconditionally and without any cap,
    which produced 6.5%-wide stops (-35%..-68% margin loss at 10x leverage).
    The returned candidate is additionally hard-capped by _apply_stop_caps.
    """
    atr = calculate_atr(candles)
    if not level or atr is None or atr <= 0:
        return None
    price = candles[-1]['c']
    pad = atr * 0.15
    if direction == 'LONG' and level < price:
        candidate = level - pad
        if candidate >= price:
            return None
    elif direction == 'SHORT' and level > price:
        candidate = level + pad
        if candidate <= price:
            return None
    else:
        return None
    dist = abs(price - candidate)
    default_dist = atr * RISK_PARAMS[risk]['atr_sl']
    if dist < default_dist * 0.8:   # structure too close: keep the ATR stop
        return None
    return candidate


def _apply_stop_caps(sl, price, atr, direction, risk):
    """v11.2 hard risk caps applied to EVERY final stop (ATR or structural):

      max distance = min(STOP_MAX_PCT * price, STRUCT_STOP_MAX_ATR * ATR)
      min distance = STOP_MIN_PCT * price  (noise protection in dead markets)

    With 10x leverage this bounds the worst-case SL loss to
    LEVERAGE * STOP_MAX_PCT = 30% of margin (was unbounded before).
    Returns (capped_stop, was_capped).
    """
    dist = abs(price - sl) if sl else 0.0
    if dist <= 0:
        atr_fallback = atr if (atr and atr > 0) else price * 0.02
        dist = atr_fallback * RISK_PARAMS[risk]['atr_sl']
    max_dist = price * STOP_MAX_PCT
    if atr and atr > 0:
        max_dist = min(max_dist, atr * STRUCT_STOP_MAX_ATR)
    min_dist = price * STOP_MIN_PCT
    capped_dist = max(min_dist, min(dist, max_dist))
    was_capped = abs(capped_dist - dist) > 1e-12
    capped = price - capped_dist if direction == 'LONG' else price + capped_dist
    return capped, was_capped


def analyze_market(symbol: str, data: Dict[str,list], direction: str,
                   risk_hint='MEDIUM', ai_verdict: Optional[dict] = None) -> dict:
    """v11.2 confluence engine.

    Hard gates (no score): enough data, HTF trend (4h+1h — stricter for
    SHORT), volatility and (for SHORT) a higher final score threshold.
    Weighted components: 10 SMC/technical components + candlestick pattern +
    the AI committee (highest weight). With ai_verdict=None the AI component
    is neutral (0.5) so backtests stay deterministic.
    Stop distance is always hard-capped (STOP_MAX_PCT / STRUCT_STOP_MAX_ATR).
    """
    base=data.get('30m',[])
    if len(base)<80:
        return {'status':'NO_SIGNAL','reason':'not enough 30m candles','symbol':symbol,'direction':direction}
    # --- hard gate: higher-timeframe trend (4h and 1h must agree) ---
    # v11.2: SHORTs need a clearly bearish HTF on BOTH timeframes (live ledger:
    # SHORT WR 0-14% with the loose 0.35 gate that LONGs use).
    htf_min = THRESHOLDS['short_htf_min'] if direction == 'SHORT' else 0.35
    trend4,t4d=_tf_state(data.get('4h',[]),direction)
    trend1,t1d=_tf_state(data.get('1h',[]),direction)
    if trend4<htf_min or trend1<htf_min:
        return {'status':'NO_SIGNAL','reason':'htf trend gate','symbol':symbol,'direction':direction,
                'gate_4h':round(trend4,2),'gate_1h':round(trend1,2),'version':VERSION}
    # --- hard gate: volatility ---
    atr=calculate_atr(base)
    price=base[-1]['c']
    atr_pct=(atr/price) if (atr and price) else 999
    if atr_pct>THRESHOLDS['max_atr_pct']:
        return {'status':'NO_SIGNAL','reason':'volatility gate','symbol':symbol,'direction':direction,
                'atr_pct':round(atr_pct,4),'version':VERSION}
    # --- weighted components (user priority order) ---
    comps=[]
    for name,score,detail in rule_components(base,direction):
        comps.append(Component(name,score,WEIGHTS[name],detail))
    s,d,support,resistance=_level_score(base,direction)
    ps,pd=_pattern_score(base,direction,support if direction=='LONG' else resistance)
    comps.append(Component('pattern',ps,WEIGHTS['pattern'],pd))
    # --- AI committee component (highest weight) ---
    if ai_verdict is None:
        ai_score,ai_detail=0.5,'AI not consulted (neutral for backtest)'
        ai_approved=None
    else:
        ai_score=min(1.0,max(0.0,float(ai_verdict.get('score',0.5))))
        ai_detail=ai_verdict.get('detail','AI verdict')
        ai_approved=ai_verdict.get('approved')
    comps.append(Component('ai_committee',ai_score,WEIGHTS['ai_committee'],ai_detail))
    # --- scores ---
    rule_w=sum(WEIGHTS[k] for k in WEIGHTS if k!='ai_committee')
    rule_score=100*sum(c.score*c.weight for c in comps if c.name!='ai_committee')/rule_w
    total=sum(c.weight for c in comps)
    score=100*sum(c.contribution for c in comps)/total
    risk='LOW' if score>=THRESHOLDS['low_score'] else 'MEDIUM' if score>=THRESHOLDS['medium_score'] else 'HIGH'
    sl,tp,_atr=_atr_levels(base,direction,risk)
    rr=RISK_PARAMS[risk]['rr']
    # v11.2 stop pipeline: structural improvement (symmetric, must add room)
    # then hard caps on the FINAL stop, then TP recomputed so R:R stays exact.
    stop_capped=False
    struct = _structural_stop(base,direction,
                              support if direction=='LONG' else resistance,risk)
    if struct is not None:
        sl=struct
    if sl is not None:
        sl,stop_capped=_apply_stop_caps(sl,price,_atr,direction,risk)
        tp = price+(price-sl)*rr if direction=='LONG' else price-(sl-price)*rr
    # AI must approve when a verdict exists; 'unavailable' (approved=None) never blocks.
    ai_ok=(ai_verdict is None) or (ai_approved is not False)
    # v11.2: SHORTs must clear a higher final score.
    threshold=THRESHOLDS['signal_score']
    if direction=='SHORT':
        threshold=max(threshold,THRESHOLDS['short_signal_score'])
    status='SIGNAL' if score>=threshold and ai_ok and sl and tp else 'NO_SIGNAL'
    stop_pct=abs(price-sl)/price if sl else None
    result={
        'status':status,'symbol':symbol,'direction':direction,'risk':risk,'score':round(score,2),
        'rule_score':round(rule_score,2),
        'confidence':round(score,1),'price':price,'stop_loss':sl,'take_profit':tp,'atr':atr,
        'atr_pct':round(atr_pct,4) if atr else None,
        'stop_pct':round(stop_pct,5) if stop_pct is not None else None,
        'stop_capped':stop_capped,'score_threshold':threshold,
        'support':support,'resistance':resistance,'rr':rr,
        'components':[{'name':c.name,'score':round(c.score,3),'weight':c.weight,'detail':c.detail} for c in comps],
        'signal_source': ' | '.join(f'{c.name}={c.score:.2f}:{c.detail}' for c in comps),
        'version':VERSION,'strategy':STRATEGY_NAME
    }
    if status=='NO_SIGNAL' and score<threshold:
        result['reason']='score below threshold'
        if direction=='SHORT' and THRESHOLDS['short_signal_score']>THRESHOLDS['signal_score']:
            result['reason']='short score gate'
    if status=='NO_SIGNAL' and ai_approved is False:
        result['reason']='AI committee rejected'
    if ai_verdict is not None:
        result['ai_votes'] = ai_verdict.get('votes') or []
        result['ai_report'] = ai_verdict.get('report') or ai_verdict.get('detail') or ''
        result['ai_approved'] = ai_approved
    return result


async def analyze_with_ai(symbol: str, data: Dict[str,list], direction: str,
                          prefer_risk='MEDIUM', session=None) -> dict:
    """Two-phase live analysis: rule pre-pass, then AI committee on strong candidates.

    Weak candidates never reach the AI (quota protection); the committee verdict
    is folded back into the weighted score and can veto the trade.
    """
    from ai_committee import consult_committee, budget_left
    pre=analyze_market(symbol,data,direction,prefer_risk)
    if 'rule_score' not in pre:          # early gate rejection
        return pre
    if pre['rule_score'] < AI_TRIGGER_RULE_SCORE:
        pre['status']='NO_SIGNAL'
        pre['reason']='rule score below AI trigger'
        return pre
    if budget_left() <= 0:
        pre['status']='NO_SIGNAL'
        pre['reason']='AI budget exhausted'
        return pre
    pre['custom_rules']=AI_RULES_TEXT
    verdict=await consult_committee(pre,session=session)
    return analyze_market(symbol,data,direction,prefer_risk,ai_verdict=verdict)


async def send_to_telegram(text: str, reply_to_message_id=None):
    """Send Telegram messages safely: one shared per-chat queue, 1.10s spacing and 429 retry."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning('Telegram credentials are not configured')
        return None
    import asyncio, json, time
    from config import TELEGRAM_MIN_INTERVAL_SECONDS, TELEGRAM_MAX_RETRIES
    if not hasattr(send_to_telegram, '_lock'):
        send_to_telegram._lock = asyncio.Lock()
        send_to_telegram._last_send = 0.0
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': text, 'parse_mode': 'HTML', 'disable_web_page_preview': True}
    if reply_to_message_id:
        try:
            payload['reply_parameters'] = {'message_id': int(reply_to_message_id)}
        except (TypeError, ValueError):
            logger.warning('Invalid Telegram reply id: %r', reply_to_message_id)
    url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage'
    async with send_to_telegram._lock:
        async with aiohttp.ClientSession() as session:
            for attempt in range(TELEGRAM_MAX_RETRIES):
                wait = TELEGRAM_MIN_INTERVAL_SECONDS - (time.monotonic() - send_to_telegram._last_send)
                if wait > 0:
                    await asyncio.sleep(wait)
                try:
                    async with session.post(url, json=payload, timeout=20) as r:
                        body = await r.text()
                        send_to_telegram._last_send = time.monotonic()
                        try:
                            data = json.loads(body)
                        except Exception:
                            data = {}
                        if r.status == 200 and data.get('ok'):
                            return (data.get('result') or {}).get('message_id')
                        if r.status == 429:
                            retry_after = int((data.get('parameters') or {}).get('retry_after', 5))
                            logger.warning('Telegram 429; retry_after=%ss attempt=%d/%d', retry_after, attempt + 1, TELEGRAM_MAX_RETRIES)
                            await asyncio.sleep(max(retry_after, TELEGRAM_MIN_INTERVAL_SECONDS))
                            continue
                        logger.warning('Telegram HTTP %s: %s', r.status, body)
                        return None
                except Exception as exc:
                    logger.warning('Telegram send error attempt=%d/%d: %s', attempt + 1, TELEGRAM_MAX_RETRIES, exc)
                    if attempt + 1 < TELEGRAM_MAX_RETRIES:
                        await asyncio.sleep(min(2 ** attempt, 8))
    return None
