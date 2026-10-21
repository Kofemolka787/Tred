"""Causal, explainable price-action rules. No fitted model or win probability.

OB, FVG and liquidity sweeps describe candle patterns, not actual OTC orders.
Only completed, contiguous bars and already-confirmed pivots are considered.
"""
import math
import json
import hashlib
from market_feed import gate, catalog_observed
from market_strategies import strategy_catalog, evaluate, valid_bar, price_allows, closed_tail


PERIODS = {5: (60, 300), 10: (60, 300), 15: (300, 900)}


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def setup_id(candidate):
    anchor={k:candidate[k] for k in ('strategy','direction','zones','events','invalidation')}
    # A zone-based SMC reaction may remain valid after another bar closes.
    # One-candle strategies and channel-only reactions belong to that exact bar.
    if candidate['strategy']!='smart_money' or not candidate['zones']:
        anchor['bar_time']=candidate['bar_time']
    return hashlib.sha256(json.dumps(anchor,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def same_setup(current, previous):
    if (current['symbol'],current['direction'],current.get('strategy'),current['timeframes'])!=(previous['symbol'],previous['direction'],previous.get('strategy'),previous['timeframes']):
        return False
    if previous.get('source_feed') is not None and current.get('source_feed')!=previous['source_feed']:
        return False
    if previous.get('setup_id'):
        return current.get('setup_id')==previous['setup_id']
    return current['bar_time']==previous['bar_time'] and current['context_bar_time']==previous['context_bar_time']


def aggregate(rows, source, target, now):
    """Never fill gaps or synthesize bars from a partial interval."""
    groups = {}
    for row in rows:
        if row["time"] + source <= now:
            start = int(row["time"] // target) * target
            groups.setdefault(start, {})[row["time"]] = row
    result = []
    for start, group in sorted(groups.items()):
        expected = [start + i * source for i in range(target // source)]
        if start + target > now or set(group) != set(expected):
            continue
        bars = [group[t] for t in expected]
        if not all(valid_bar(bar) for bar in bars):
            continue
        result.append({"time": start, "open": bars[0]["open"], "close": bars[-1]["close"],
                       "high": max(b["high"] for b in bars), "low": min(b["low"] for b in bars), "origin": "ticks" if any(b.get("origin")=="ticks" for b in bars) else "broker"})
    return result


def history(histories, pair, period, now):
    native=closed_tail(histories.get((pair,period),[]),period,now)
    if len(native)>=80 and now-(native[-1]["time"]+period)<=period+10 and all(valid_bar(r) for r in native):
        return native
    merged={}
    for source in (60,120,300,600):
        if source < period and period % source == 0:
            for row in aggregate(histories.get((pair,source),[]),source,period,now):
                merged[row["time"]]=row
    for row in histories.get((pair,period),[]):
        merged[row["time"]]=row
    return sorted(merged.values(),key=lambda r:r["time"])


def frame_data(histories,pair,expiry,now):
    preferred=PERIODS.get(expiry,(60,300))
    plans=list(dict.fromkeys((preferred,(120,600),(60,300),(300,900))))
    fallback=None
    for plan in plans:
        bars=[closed_tail(history(histories,pair,p,now),p,now) for p in plan]
        checks=[analyze(r,p,now) for r,p in zip(bars,plan)]
        result=(plan,bars,checks)
        if all(a.get("history_ready") for a in checks):
            return result
        if fallback is None or min(len(r) for r in bars)>min(len(r) for r in fallback[1]):
            fallback=result
    return fallback


def analyze(candles, period, now):
    rows = closed_tail(candles, period, now)
    if len(rows) < 80:
        return {"status": "skipped", "reason": f"Нужно 80 закрытых свечей M{period//60}; получено {len(rows)}.", "bars": len(rows)}
    if any(not all(finite(r[k]) and 0 < r[k] <= 1e9 for k in ("open", "high", "low", "close")) or r["low"] > min(r["open"], r["close"]) or r["high"] < max(r["open"], r["close"]) for r in rows):
        return {"status": "skipped", "reason": "Некорректная история OHLC."}
    if now - (rows[-1]["time"] + period) > period + 10:
        return {"status": "skipped", "reason": "История свечей устарела."}
    if any(abs(b["time"] - a["time"] - period) > 1 for a, b in zip(rows, rows[1:])):
        return {"status": "skipped", "reason": "В истории есть пропуски свечей."}
    highs, lows = [], []
    for i in range(2, len(rows) - 2):
        window = rows[i-2:i+3]
        if all(rows[i]["high"] > r["high"] for j, r in enumerate(window) if j != 2):
            highs.append((i, rows[i]["high"]))
        if all(rows[i]["low"] < r["low"] for j, r in enumerate(window) if j != 2):
            lows.append((i, rows[i]["low"]))
    close = rows[-1]["close"]
    fast = sum(r["close"] for r in rows[-20:]) / 20
    slow = sum(r["close"] for r in rows[-50:]) / 50
    atr = sum(max(b["high"] - b["low"], abs(b["high"] - a["close"]), abs(b["low"] - a["close"]))
              for a, b in zip(rows[-15:-1], rows[-14:])) / 14
    if atr <= 0 or len(highs) < 2 or len(lows) < 2:
        return {"status": "skipped", "reason": "Нет двух подтверждённых максимумов и минимумов.", "history_ready": True}
    direction = "up" if highs[-1][1] > highs[-2][1] and lows[-1][1] > lows[-2][1] else "down" if highs[-1][1] < highs[-2][1] and lows[-1][1] < lows[-2][1] else None
    aligned = direction == "up" and close > fast > slow or direction == "down" and close < fast < slow
    if not direction or not aligned:
        return {"status": "skipped", "reason": "Экстремумы и направленность цены не согласованы.", "history_ready": True}
    up = direction == "up"
    evidence = ["HH/HL: максимумы и минимумы повышаются." if up else "LH/LL: максимумы и минимумы понижаются.",
                "Цена и средние 20/50 подтверждают направление; средние используются только как фильтр."]
    if any(r.get("origin")=="ticks" for r in rows):
        evidence.append("Часть OHLC построена по полученным тикам без заполнения пропусков. Это наблюдаемая история цен, не подтверждённые брокером свечи.")
    zones, events = [], []
    sweep = False
    for i in range(len(rows) - 5, len(rows)):
        r = rows[i]
        level = min(x["low"] for x in rows[i-10:i]) if up else max(x["high"] for x in rows[i-10:i])
        if r["low"] < level < r["close"] if up else r["high"] > level > r["close"]:
            sweep = True
            events.append({"kind": "sweep", "time": r["time"], "level": level})
    if sweep:
        evidence.append("Вынос локального экстремума и закрытие обратно в диапазон: ценовой признак sweep, без данных о реальных стопах.")
    # A displacement must break a pivot that was confirmed BEFORE that bar.
    ob = False
    for i in range(max(5, len(rows) - 12), len(rows) - 1):
        pivots = [(j, v) for j, v in (highs if up else lows) if j + 2 < i]
        if not pivots:
            continue
        level = pivots[-1][1]
        r, previous = rows[i], rows[i-1]
        broke = previous["close"] <= level < r["close"] if up else previous["close"] >= level > r["close"]
        displaced = abs(r["close"] - r["open"]) >= .8 * atr and (r["close"] > r["open"]) == up
        if not broke or not displaced:
            continue
        events.append({"kind": "bos", "time": r["time"], "level": level})
        opposite = [j for j in range(max(0, i - 6), i) if (rows[j]["close"] < rows[j]["open"]) == up and rows[j]["close"] != rows[j]["open"]]
        if not opposite:
            continue
        block = rows[opposite[-1]]
        lo, hi = block["low"], block["high"]
        after = rows[i+1:]
        intact = all(x["close"] >= lo if up else x["close"] <= hi for x in after)
        reaction = any(x["low"] <= hi and x["high"] >= lo and (x["close"] > hi if up else x["close"] < lo) for x in after[-3:])
        if intact and reaction:
            ob = True
            zones.append({"kind": "order_block", "low": lo, "high": hi, "time": block["time"]})
    if ob:
        evidence.append("BOS с импульсом ≥0,8 ATR и возврат к последней противоположной свече: реакция на предполагаемый order block. Это ценовая модель, не подтверждённая заявка крупного участника.")
    gap = False
    for i in range(max(2, len(rows) - 12), len(rows) - 1):
        a, b, c = rows[i-2:i+1]
        lo, hi = (a["high"], c["low"]) if up else (c["high"], a["low"])
        if hi <= lo or abs(b["close"] - b["open"]) < .5 * atr:
            continue
        after = rows[i+1:]
        intact = all(x["close"] >= lo if up else x["close"] <= hi for x in after)
        reaction = any(x["low"] <= hi and x["high"] >= lo and (x["close"] > hi if up else x["close"] < lo) for x in after[-3:])
        if intact and reaction:
            gap = True
            zones.append({"kind": "fvg", "low": lo, "high": hi, "time": b["time"]})
    if gap:
        evidence.append("Возврат в трёхсвечный FVG с закрытием в сторону структуры; зона не пробита закрытием.")
    # Parallel rising/falling support and resistance from confirmed pivots.
    channel = None
    if len(highs) >= 3 and len(lows) >= 3:
        def line(points):
            pts = points[-3:]
            mx, my = sum(x for x, _ in pts) / 3, sum(y for _, y in pts) / 3
            slope = sum((x-mx)*(y-my) for x, y in pts) / sum((x-mx)**2 for x, _ in pts)
            return slope, my + slope * (len(rows)-1-mx)
        hs, ceiling = line(highs)
        ls, floor = line(lows)
        if (hs > 0 and ls > 0 if up else hs < 0 and ls < 0) and abs(hs-ls)*20 < atr and ceiling-floor > atr:
            channel = {"low": floor, "high": ceiling, "slope_low": ls / period, "slope_high": hs / period, "time": rows[-1]["time"]}
    channel_reaction = channel is not None and (rows[-1]["low"] <= channel["low"] + .5*atr and close > channel["low"] if up else rows[-1]["high"] >= channel["high"] - .5*atr and close < channel["high"])
    if channel_reaction:
        evidence.append("Реакция у нижней границы растущего канала." if up else "Реакция у верхней границы падающего канала.")
    stretched = abs(close-fast) > 3*atr
    against_boundary = channel is not None and (close >= channel["high"]-.25*atr if up else close <= channel["low"]+.25*atr)
    qualified = (ob or gap and sweep or channel_reaction and sweep) and not stretched and not against_boundary
    invalidation = zones[-1]["low" if up else "high"] if zones else lows[-1][1] if up else highs[-1][1]
    return {"status": "candidate" if qualified else "skipped", "context_ready": True, "history_ready": True, "direction": direction,
            "score": len(evidence), "evidence": evidence, "atr": atr, "close": close, "bar_time": rows[-1]["time"],
            "zones": zones, "events": events[-5:], "channel": channel, "invalidation": invalidation,
            "reason": "Структура и реакция на зону согласованы." if qualified else "Структура видна, но нет свежей реакции на зону; ждём подтверждение."}


def rank(assets, histories, *, expiry, market, symbol, allow_otc, now, min_payout=.85, research=False, signal_mode="strict"):
    results, rejected = [], []
    periods = PERIODS.get(expiry, (300, 900) if expiry >= 15 else (60, 300))
    def normalize(s):
        value=s.replace("/", "").upper().replace(" OTC", "_OTC")
        return value+"_OTC" if market=="otc" and not value.endswith("_OTC") else value
    for asset in assets:
        if symbol and normalize(asset["symbol"]) != normalize(symbol) or asset["market"] != market:
            continue
        if asset["market"] == "otc" and not allow_otc:
            rejected.append((0,"Разреши OTC в настройках анализа или выбери вкладку OTC."))
            continue
        status=gate(asset,expiry=expiry,min_payout=min_payout,now=now)
        entry_ready=status['code']=='ready'
        if not entry_ready and not (research and status['metadata_eligible'] and catalog_observed(asset,now)):
            priority={"closed":0,"payout":1,"expiry":2,"catalog":3,"quote":4}[status["code"]]
            rejected.append((priority,status["reason"]))
            continue
        periods,bars,analyses=frame_data(histories,asset["symbol"],expiry,now)
        setups, frames, reason = evaluate(bars, periods, analyses, now, signal_mode)
        if not setups:
            rejected.append((6 if all(a.get("history_ready") for a in analyses) else 5, reason))
        for first in setups:
            price = asset.get("price")
            if entry_ready and not price_allows(first, price):
                rejected.append((7,"Цена уже ушла от зоны или нарушила условие сценария. Вход пропущен."))
                continue
            results.append({"symbol": asset["symbol"], "market": asset["market"], "direction": first["direction"],
                            "setup_id":setup_id(first),
                            "entry_ready":entry_ready,"reference_price":first['close'],"reference_source":"closed_bar",
                            "strategy": first["strategy"], "strategy_name": first["strategy_name"], "strategy_risk": first["strategy_risk"],
                            "signal_mode":signal_mode,"risk_level":"elevated" if signal_mode=="active" else "standard",
                            "source_feed":asset.get('catalog_feed'),
                            "setup_valid_until":first['bar_time']+periods[0]+min(180,2*periods[0]) if signal_mode=='active' and first['strategy'] in ('ema_pullback','trend_continuation','momentum_breakout','level_engulfing') else first['bar_time']+2*periods[0],
                            "anchor_bars":frames[0]['rows'][-12:],
                            "score": first["score"], "evidence": [*first["evidence"], *([f"Старший M{periods[1]//60} подтверждает направление структуры."] if first["strategy"] == "smart_money" else [])],
                            "timeframes": list(periods), "payout": asset["payout"], "price": price,
                            "quote_time": asset["quote_time"], "zones": first["zones"], "events": first["events"],
                            "channel": first["channel"], "invalidation": first["invalidation"], "atr": first["atr"],
                            "entry_low": (price if entry_ready else first['close'])-(.35 if entry_ready else .5)*first["atr"],
                            "entry_high": (price if entry_ready else first['close'])+(.35 if entry_ready else .5)*first["atr"],
                            "catalog_age": status["catalog_age"], "catalog_source": status["catalog_source"],
                            "bar_time": first["bar_time"], "context_bar_time": frames[1]["bar_time"], "candles": frames[0]["rows"][-60:]})
    return sorted(results, key=lambda x: (x["payout"] < .92, -x["score"], -x["payout"], x["symbol"], x["strategy"])), [reason for _,reason in sorted(rejected,key=lambda r:-r[0])]


def coverage(assets, histories, *, market, expiry, min_payout, now, signal_mode="strict"):
    periods = PERIODS.get(expiry, (60, 300))
    rows = []
    for asset in assets:
        if asset["market"] != market:
            continue
        selected,bars,checks=frame_data(histories,asset["symbol"],expiry,now)
        status=gate(asset,expiry=expiry,min_payout=min_payout,now=now)
        quote_live,eligible=status["quote_live"],status["eligible"]
        setups, frames, strategy_reason = evaluate(bars, selected, checks, now, signal_mode) if status["code"] == "ready" and all(a.get("history_ready") for a in checks) else ([], [], "")
        current = [s for s in setups if price_allows(s, asset["price"])]
        if setups and not current:
            strategy_reason = "Сценарий найден, но цена уже ушла от зоны или нарушила условие отмены."
        available=[]
        for period in (60,120,300,600,900):
            stored=closed_tail(histories.get((asset['symbol'],period),[]),period,now)
            if stored:
                available.append({'period':period,'bars':len(stored),'last_time':stored[-1]['time'],
                                  'age_seconds':max(0,round(now-stored[-1]['time']-period))})
        rows.append({"symbol": asset["symbol"], "payout": asset["payout"], "eligible": eligible, "metadata_eligible":status["metadata_eligible"],
                     "analysis_ready":bool(status['metadata_eligible'] and catalog_observed(asset,now) and all(a.get('history_ready') for a in checks)),
                     "quote_live": quote_live, "price": asset.get("price"), "quote_time": asset.get("quote_time"), "timeframes":list(selected), "tick_bars":[sum(r.get("origin")=="ticks" for r in rows) for rows in bars], "bars": [len(r) for r in bars],
                     "history_ready": all(a.get("history_ready") for a in checks),
                     "available_history":available,
                     "strategies": list(dict.fromkeys(s["strategy_name"] for s in current)),
                     "regimes": [f["regime"] for f in frames],
                     "stage": status["code"] if status["code"]!="ready" else "history" if not all(a.get("history_ready") for a in checks) else "structure",
                     "catalog_age":status["catalog_age"],"catalog_source":status["catalog_source"],
                     "history_status":json.loads(asset["history_status"]) if asset.get("history_status") else None,
                     "reason":status["reason"] if status["code"]!="ready" else next((a["reason"] for a in checks if not a.get("history_ready")), "Сценарии: "+", ".join(dict.fromkeys(s["strategy_name"] for s in current)) if current else strategy_reason)})
    stages={name:sum(r["stage"]==name for r in rows) for name in ("closed","payout","expiry","catalog","quote","history","structure")}
    return {"blocked":stages,"catalog": len(rows), "eligible": sum(r["eligible"] for r in rows), "metadata_eligible":sum(r["metadata_eligible"] for r in rows), "quoted": sum(r["quote_live"] for r in rows),
            "ready": sum(r["eligible"] and r["quote_live"] and r["history_ready"] for r in rows),
            "analysis_ready":sum(r['analysis_ready'] for r in rows),
            "strategy_catalog": list(strategy_catalog(signal_mode)), "timeframes": list(periods), "pairs": sorted(rows, key=lambda r: (-r["eligible"], -r["quote_live"], -r["payout"], r["symbol"]))}
