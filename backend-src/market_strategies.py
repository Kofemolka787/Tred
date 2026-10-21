"""Causal setups for different regimes; scores are confluence, never win odds.

Every detector uses closed bars and levels confirmed before the reaction.
Indicators confirm price action; none is a standalone entry trigger.
"""
import math


STRATEGIES = (
    {"id": "smart_money", "name": "Smart Money и структура", "regime": "trend",
     "rule": "Структура на двух периодах и реакция на OB, FVG или канал.",
     "weakness": "Ценовые модели не доказывают наличие реальных заявок или стопов."},
    {"id": "ema_pullback", "name": "Откат по тренду EMA 20/50", "regime": "trend",
     "rule": "Наклон EMA 20/50, откат и закрытая свеча возобновления тренда.",
     "weakness": "В боковике средние дают ложные продолжения."},
    {"id": "breakout_retest", "name": "Пробой и ретест уровня", "regime": "breakout",
     "rule": "Закрытие за подтверждённым уровнем, возврат и удержание уровня.",
     "weakness": "После ретеста цена может вернуться за уровень."},
    {"id": "range_rebound", "name": "Отскок от границы диапазона", "regime": "range",
     "rule": "Повторные касания границ боковика и закрытая свеча отскока.",
     "weakness": "Начало сильного тренда отменяет торговлю границ диапазона."},
    {"id": "false_breakout", "name": "Ложный пробой уровня", "regime": "reversal",
     "rule": "Выход за заранее известный уровень и закрытие обратно с подтверждением.",
     "weakness": "Возврат за уровень может оказаться временным."},
)
BY_ID = {s["id"]: s for s in STRATEGIES}


def valid_bar(row):
    return (all(type(row.get(k)) in (float, int) and math.isfinite(row[k]) and 0 < row[k] <= 1e9 for k in ("open", "high", "low", "close"))
            and row["low"] <= min(row["open"], row["close"]) and row["high"] >= max(row["open"], row["close"]))


def closed_tail(rows, period, now, limit=160):
    """Use the latest continuous closed run; never bridge a missing interval.

    A gap before 80 good recent bars must not invalidate those recent bars.
    A gap inside the required run still leaves fewer than 80 usable bars.
    """
    rows = sorted((r for r in rows if r["time"]+period <= now), key=lambda r:r["time"])[-limit:]
    for i in range(len(rows)-1, 0, -1):
        if abs(rows[i]["time"]-rows[i-1]["time"]-period) > 1:
            return rows[i:]
    return rows


def ema(values, length):
    result = [None] * len(values)
    if len(values) < length:
        return result
    result[length-1] = sum(values[:length]) / length
    alpha = 2 / (length+1)
    for i in range(length, len(values)):
        result[i] = alpha*values[i] + (1-alpha)*result[i-1]
    return result


def rsi(values, length=14):
    gains = [max(b-a, 0) for a, b in zip(values, values[1:])]
    losses = [max(a-b, 0) for a, b in zip(values, values[1:])]
    up, down = sum(gains[:length])/length, sum(losses[:length])/length
    for gain, loss in zip(gains[length:], losses[length:]):
        up, down = (up*(length-1)+gain)/length, (down*(length-1)+loss)/length
    return 50 if up == down == 0 else 100 if down == 0 else 100-100/(1+up/down)


def levels(rows, atr):
    """Two distinct confirmed pivots per level; no unconfirmed right edge."""
    result = {"support": [], "resistance": []}
    for kind, field, sign in (("support", "low", -1), ("resistance", "high", 1)):
        pivots = []
        for i in range(max(2, len(rows)-65), len(rows)-2):
            value = rows[i][field]
            if all(sign*value > sign*rows[j][field] for j in range(i-2, i+3) if j != i):
                pivots.append((i, value))
        groups = []
        for i, value in pivots:
            group = next((g for g in groups if abs(value-sum(v for _, v in g)/len(g)) <= .3*atr), None)
            if group is None:
                groups.append([(i, value)])
            elif i-group[-1][0] >= 3:
                group.append((i, value))
        for group in groups:
            if len(group) >= 2:
                result[kind].append({"price": sum(v for _, v in group)/len(group),
                                     "touches": len(group), "time": rows[group[-1][0]]["time"]})
    return result


def frame(rows, period, now):
    rows = closed_tail(rows, period, now)
    if len(rows) < 80 or now-(rows[-1]["time"]+period) > period+10:
        return None
    if any(abs(b["time"]-a["time"]-period) > 1 for a, b in zip(rows, rows[1:])):
        return None
    if not all(valid_bar(r) for r in rows):
        return None
    closes = [r["close"] for r in rows]
    fast, slow = ema(closes, 20), ema(closes, 50)
    tr = [max(b["high"]-b["low"], abs(b["high"]-a["close"]), abs(b["low"]-a["close"])) for a, b in zip(rows, rows[1:])]
    atr = sum(tr[:14])/14
    for value in tr[14:]:
        atr = (atr*13+value)/14
    if atr <= 0:
        return None
    path = sum(abs(b-a) for a, b in zip(closes[-21:-1], closes[-20:]))
    efficiency = abs(closes[-1]-closes[-21])/path if path else 0
    spread = (fast[-1]-slow[-1])/atr
    fs, ss = (fast[-1]-fast[-4])/atr, (slow[-1]-slow[-4])/atr
    trend = "up" if spread > .15 and fs > .03 and ss > .015 and efficiency >= .25 else "down" if spread < -.15 and fs < -.03 and ss < -.015 and efficiency >= .25 else None
    regime = "trend" if trend else "range" if efficiency <= .35 and abs(spread) <= .6 and abs(fs) <= .35 else "transition"
    mid = sum(closes[-20:])/20
    deviation = math.sqrt(sum((c-mid)**2 for c in closes[-20:])/20)
    e12, e26 = ema(closes, 12), ema(closes, 26)
    macd = [a-b for a, b in zip(e12, e26) if a is not None and b is not None]
    signal = ema(macd, 9)
    stoch = []
    for i in range(len(rows)-3, len(rows)):
        hi, lo = max(r["high"] for r in rows[i-13:i+1]), min(r["low"] for r in rows[i-13:i+1])
        stoch.append(100*(rows[i]["close"]-lo)/(hi-lo) if hi > lo else 50)
    return {"rows": rows, "period": period, "bar_time": rows[-1]["time"], "close": closes[-1], "atr": atr,
            "fast": fast, "slow": slow, "trend": trend, "regime": regime,
            "levels": levels(rows[:-2], atr),
            "indicators": {"ema20": fast[-1], "ema50": slow[-1], "ema20_slope_atr": fs,
                           "ema50_slope_atr": ss, "efficiency20": efficiency, "rsi14": rsi(closes),
                           "bollinger20": {"middle": mid, "lower": mid-2*deviation, "upper": mid+2*deviation},
                           "macd": macd[-1], "macd_signal": signal[-1], "macd_histogram": macd[-1]-signal[-1],
                           "stochastic_k": stoch[-1], "stochastic_d": sum(stoch)/3}}


def engulfing(rows, up):
    previous, last = rows[-2:]
    return (previous["close"] < previous["open"] and last["close"] > last["open"] and last["open"] <= previous["close"] and last["close"] >= previous["open"]) if up else (
            previous["close"] > previous["open"] and last["close"] < last["open"] and last["open"] >= previous["close"] and last["close"] <= previous["open"])


def context_allows(context, direction, family):
    if family in ("smart_money", "ema_pullback"):
        return context["trend"] == direction
    if family == "range_rebound":
        return context["regime"] == "range"
    return context["trend"] in (None, direction)


def setup(f, strategy, direction, evidence, invalidation, *, level=None, level_time=None, events=None, weakness=None):
    spec = BY_ID[strategy]
    rows, atr = f["rows"], f["atr"]
    up = direction == "up"
    evidence = list(evidence)
    if engulfing(rows, up):
        evidence.append("Закрытое бычье поглощение подтверждает реакцию." if up else "Закрытое медвежье поглощение подтверждает реакцию.")
    histogram = f["indicators"]["macd_histogram"]
    if (histogram > 0 if up else histogram < 0):
        evidence.append("MACD подтверждает направление; это дополнительный фильтр, не отдельный сигнал.")
    return {"strategy": strategy, "strategy_name": spec["name"], "strategy_risk": weakness or spec["weakness"],
            "direction": direction, "score": len(evidence), "evidence": evidence,
            "atr": atr, "close": f["close"], "bar_time": f["bar_time"], "invalidation": invalidation,
            "zones": [{"kind": "level", "low": level-.2*atr, "high": level+.2*atr, "time": level_time}] if level is not None else [],
            "events": events or [{"kind": strategy, "time": f["bar_time"]}], "channel": None}


def detect(f):
    """Independent families: a range entry never requires a trending SMC frame."""
    rows, atr = f["rows"], f["atr"]
    last, previous = rows[-1], rows[-2]
    found = []
    for direction in ("up", "down"):
        up, sign = direction == "up", 1 if direction == "up" else -1
        body = sign*(last["close"]-last["open"])
        rebound = body >= .15*atr and sign*(last["close"]-previous["close"]) > 0
        if f["trend"] == direction and rebound:
            touched = any(r["low"] <= f["fast"][i]+.2*atr and r["high"] >= f["fast"][i]-.2*atr
                          or r["low"] <= f["slow"][i]+.2*atr and r["high"] >= f["slow"][i]-.2*atr
                          for i, r in enumerate(rows[-3:], len(rows)-3))
            resumed = sign*(last["close"]-f["fast"][-1]) > 0 and abs(last["close"]-f["fast"][-1]) <= atr
            held = min(r["low"] for r in rows[-3:]) >= f["slow"][-1]-atr if up else max(r["high"] for r in rows[-3:]) <= f["slow"][-1]+atr
            if touched and resumed and held:
                invalidation = min(r["low"] for r in rows[-3:])-.1*atr if up else max(r["high"] for r in rows[-3:])+.1*atr
                found.append(setup(f, "ema_pullback", direction,
                    ["EMA 20/50 и их наклон согласованы с направлением тренда.",
                     "Откат коснулся EMA; последняя закрытая свеча возобновляет движение."], invalidation))
        boundary = f["levels"]["support" if up else "resistance"]
        for item in boundary:
            level = item["price"]
            edge = last["low"] if up else last["high"]
            near = abs(edge-level) <= .4*atr and 0 < sign*(last["close"]-level) <= atr
            wick = min(last["open"], last["close"])-last["low"] if up else last["high"]-max(last["open"], last["close"])
            if f["regime"] == "range" and rebound and near and (wick >= max(body*.7, .15*atr) or engulfing(rows, up)):
                opposite = f["levels"]["resistance" if up else "support"]
                room = [sign*(other["price"]-level) for other in opposite]
                if any(2*atr <= width <= 12*atr and width-sign*(last["close"]-level) >= atr for width in room):
                    found.append(setup(f, "range_rebound", direction,
                        [f"Боковик: эффективность движения {f['indicators']['efficiency20']:.2f}; направленного тренда нет.",
                         f"Уровень {level:.8g} подтверждён {item['touches']} отдельными касаниями.",
                         "Закрытая свеча отскока у границы; до противоположной границы остаётся пространство."],
                        min(last["low"], level)-.2*atr if up else max(last["high"], level)+.2*atr, level=level, level_time=item["time"]))
            swept = (last["low"] < level-.1*atr if up else last["high"] > level+.1*atr)
            prior_swept = (previous["low"] < level-.1*atr and previous["close"] <= level if up else previous["high"] > level+.1*atr and previous["close"] >= level)
            reclaim = .1*atr < sign*(last["close"]-level) <= atr
            if rebound and reclaim and (swept or prior_swept):
                invalidation = min(last["low"], previous["low"])-.1*atr if up else max(last["high"], previous["high"])+.1*atr
                found.append(setup(f, "false_breakout", direction,
                    [f"Уровень {level:.8g} существовал до сигнальной свечи: касаний {item['touches']}.",
                     "Цена вышла за уровень и закрылась обратно; закрытая свеча подтверждает возврат."],
                    invalidation, level=level, level_time=item["time"], events=[{"kind": "sweep", "time": last["time"] if swept else previous["time"], "level": level}]))
        if not rebound:
            continue
        # Recompute levels from the prefix: the breakout cannot create its own level.
        for i in range(len(rows)-7, len(rows)-1):
            known = levels(rows[:i], atr)["resistance" if up else "support"]
            breakout = rows[i]
            if sign*(breakout["close"]-breakout["open"]) < .5*atr:
                continue
            for item in known:
                level = item["price"]
                crossed = sign*(rows[i-1]["close"]-level) <= .1*atr and sign*(breakout["close"]-level) > .15*atr
                edge = last["low"] if up else last["high"]
                retest = -.4*atr <= sign*(edge-level) <= .25*atr and .1*atr < sign*(last["close"]-level) <= atr
                held = all(sign*(r["close"]-level) >= -.1*atr for r in rows[i+1:])
                if crossed and retest and held:
                    found.append(setup(f, "breakout_retest", direction,
                        [f"Закрытый импульс пробил уровень {level:.8g}, известный до пробоя.",
                         "Цена вернулась к уровню с новой стороны; последняя закрытая свеча удержала его."],
                        min(edge, level)-.2*atr if up else max(edge, level)+.2*atr,
                        level=level, level_time=item["time"], events=[{"kind": "breakout", "time": breakout["time"], "level": level},
                                             {"kind": "retest", "time": last["time"], "level": level}]))
    # Several nearby levels can describe the same setup. Keep one per family/direction.
    unique = {}
    for item in found:
        key = item["strategy"], item["direction"]
        if key not in unique or abs(f["close"]-item["invalidation"]) < abs(f["close"]-unique[key]["invalidation"]):
            unique[key] = item
    return list(unique.values())


def evaluate(bars, periods, checks, now):
    primary, context = [frame(rows, period, now) for rows, period in zip(bars, periods)]
    if primary is None or context is None:
        return [], [], "Для стратегий нужны два полных свежих периода без пропусков."
    setups, rejected = [], []
    first, higher = checks
    if first["status"] == "candidate" and higher.get("context_ready") and first["direction"] == higher["direction"]:
        spec = BY_ID["smart_money"]
        setups.append({**first, "strategy": spec["id"], "strategy_name": spec["name"], "strategy_risk": spec["weakness"]})
    for candidate in detect(primary):
        if context_allows(context, candidate["direction"], candidate["strategy"]):
            candidate["evidence"].append(f"Старший M{periods[1]//60}: " + ("подтверждён боковик." if candidate["strategy"] == "range_rebound" else "режим не противоречит сценарию."))
            setups.append(candidate)
        else:
            rejected.append(candidate["strategy_name"]+": старший период не подтверждает режим.")
    reason = "; ".join(rejected) if rejected else "Нет закрытой свечи, подтверждающей откат, ретест, отскок или возврат за уровень."
    return setups, [primary, context], reason


def price_allows(candidate, price):
    if candidate["atr"] <= 0 or candidate["invalidation"] <= 0:
        return False
    inside = price > candidate["invalidation"] if candidate["direction"] == "up" else price < candidate["invalidation"]
    return inside and abs(price-candidate["close"]) <= .5*candidate["atr"]
