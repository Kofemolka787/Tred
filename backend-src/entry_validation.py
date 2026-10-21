"""Revalidate a reviewed early trigger; never replace it with a later setup."""
from market_analysis import history
from market_feed import gate
from market_strategies import frame, context_allows, price_allows

EARLY = ('ema_pullback','trend_continuation','momentum_breakout','level_engulfing')
FIELDS = ('time','open','high','low','close')


def revalidate_early(prior, asset, histories, prefs, now):
    if prior.get('signal_mode')!='active' or prior.get('strategy') not in EARLY:
        return None, 'setup_changed'
    if now>=prior.get('setup_valid_until',0):
        return None, 'setup_expired'
    if asset is None or asset['symbol']!=prior['symbol'] or asset['market']!=prior['market']:
        return None, 'source_changed'
    if prior.get('source_feed')!=asset.get('catalog_feed'):
        return None, 'source_changed'
    status=gate(asset,expiry=prefs['expiry'],min_payout=prefs['min_payout']/100,now=now)
    if status['code']!='ready':
        return None, 'quote_missing' if status['code']=='quote' else 'market_changed'
    if not prior['entry_low']<=asset['price']<=prior['entry_high'] or not price_allows({**prior,'close':prior['reference_price']},asset['price']):
        return None, 'price_left_zone'
    periods=prior['timeframes']
    features=[frame(history(histories,prior['symbol'],p,now),p,now) for p in periods]
    if len(features)!=2 or any(f is None for f in features):
        return None, 'history_missing'
    primary,context=features
    anchors=prior.get('anchor_bars',[])
    if len(anchors)!=12 or anchors[-1]['time']!=prior['bar_time']:
        return None, 'anchor_changed'
    actual={r['time']:r for r in primary['rows']}
    if any(r['time'] not in actual or any(r[k]!=actual[r['time']][k] for k in FIELDS) for r in anchors):
        return None, 'anchor_changed'
    direction,strategy=prior['direction'],prior['strategy']
    sign=1 if direction=='up' else -1
    if not context_allows(context,direction,strategy,'active') or primary['trend'] not in (None,direction):
        return None, 'context_changed'
    later=[r for r in primary['rows'] if r['time']>prior['bar_time']]
    if any(sign*(r['close']-prior['invalidation'])<=0 for r in later):
        return None, 'setup_invalidated'
    last=primary['rows'][-1]
    if sign*(last['close']-prior['reference_price'])<-.35*prior['atr'] or sign*(last['close']-last['open'])<=-.5*prior['atr']:
        return None, 'context_changed'
    if strategy in ('ema_pullback','trend_continuation'):
        if primary['trend']!=direction or sign*(asset['price']-primary['fast'][-1])<=0:
            return None, 'context_changed'
    if strategy=='momentum_breakout':
        event=next((e for e in prior['events'] if e['kind']=='momentum_breakout'),None)
        if event is None or any(sign*(r['close']-event['level'])<-.1*prior['atr'] for r in later) or sign*(asset['price']-event['level'])<-.1*prior['atr']:
            return None, 'setup_invalidated'
    result={**prior,'price':asset['price'],'quote_time':asset['quote_time'],'entry_ready':True,
            'payout':asset['payout'],'context_bar_time':context['bar_time'],
            'evaluation_bar_time':primary['bar_time'],'revalidated_at':now,'candles':primary['rows'][-60:],
            'evidence':[*prior['evidence'],'После ответа ИИ исходный триггер проверен по новым закрытым свечам и текущей цене; зона и условие отмены сохранены.']}
    return result,None
