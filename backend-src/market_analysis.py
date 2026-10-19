"""Causal price-action research, never a calibrated probability or order sender."""
import math


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def analyze(candles, period, now):
    """Only completed, contiguous candles. Pivots require two confirming bars."""
    rows = sorted(candles, key=lambda x: x["time"])
    rows = [x for x in rows if x["time"] + period <= now]
    if len(rows) < 80:
        return {"status": "skipped", "reason": "Нужно минимум 80 закрытых свечей на каждом таймфрейме."}
    rows = rows[-160:]
    if now - (rows[-1]["time"] + period) > period + 10:
        return {"status": "skipped", "reason": "История свечей устарела."}
    if any(abs(b["time"] - a["time"] - period) > 1 for a, b in zip(rows, rows[1:])):
        return {"status": "skipped", "reason": "В истории есть пропуски свечей."}
    highs, lows = [], []
    # The last two bars cannot be pivots yet. The current last bar is never
    # used to identify a level it is simultaneously breaking.
    for i in range(2, len(rows) - 3):
        window = rows[i-2:i+3]
        if all(rows[i]["high"] > r["high"] for j, r in enumerate(window) if j != 2):
            highs.append(rows[i]["high"])
        if all(rows[i]["low"] < r["low"] for j, r in enumerate(window) if j != 2):
            lows.append(rows[i]["low"])
    close = rows[-1]["close"]
    fast = sum(x["close"] for x in rows[-20:]) / 20
    slow = sum(x["close"] for x in rows[-50:]) / 50
    atr = sum(max(x["high"]-x["low"], abs(x["high"]-p["close"]), abs(x["low"]-p["close"]))
              for p, x in zip(rows[-15:-1], rows[-14:])) / 14
    if atr <= 0:
        return {"status": "skipped", "reason": "Нет измеримого движения цены."}
    direction = "up" if fast > slow and close > fast else "down" if fast < slow and close < fast else None
    if not direction or len(highs) < 2 or len(lows) < 2:
        return {"status": "skipped", "reason": "Нет подтверждённой направленной структуры."}
    up = direction == "up"
    structure = highs[-1] > highs[-2] and lows[-1] > lows[-2] if up else highs[-1] < highs[-2] and lows[-1] < lows[-2]
    broken = close > highs[-1] if up else close < lows[-1]
    sweep = any(x["low"] < min(p["low"] for p in rows[i-10:i]) and x["close"] > min(p["low"] for p in rows[i-10:i])
                if up else x["high"] > max(p["high"] for p in rows[i-10:i]) and x["close"] < max(p["high"] for p in rows[i-10:i])
                for i, x in enumerate(rows) if len(rows)-5 <= i < len(rows))
    gap = False
    for i in range(max(2, len(rows)-12), len(rows)-1):
        a, b, c = rows[i-2:i+1]
        if up and c["low"] > a["high"]:
            gap |= all(x["close"] >= a["high"] for x in rows[i+1:]) and any(x["low"] <= c["low"] and x["close"] > c["low"] for x in rows[i+1:])
        elif not up and c["high"] < a["low"]:
            gap |= all(x["close"] <= a["low"] for x in rows[i+1:]) and any(x["high"] >= c["high"] and x["close"] < c["high"] for x in rows[i+1:])
    stretched = abs(close - fast) > 3 * atr
    evidence = ["Средние 20/50 согласованы с ценой"]
    for condition, label in [(structure, "Повышающиеся экстремумы" if up else "Понижающиеся экстремумы"),
                             (broken, "Пробой подтверждённого swing-уровня"), (sweep, "Снятие локального экстремума с возвратом"),
                             (gap, "Возврат к трёхсвечному ценовому разрыву")]:
        if condition:
            evidence.append(label)
    qualified = structure and (broken or sweep) and gap and not stretched
    return {"status": "candidate" if qualified else "skipped", "direction": direction,
            "score": len(evidence), "evidence": evidence, "atr": atr,
            "reason": "Есть согласование условий; метод ещё не проверен на реальной выборке." if qualified else "Недостаточно согласованных условий для входа."}


def rank(assets, histories, *, expiry, market, symbol, allow_otc, now):
    """Availability must be observed, not guessed from a weekday calendar."""
    results, rejected = [], []
    periods = (300, 900) if expiry >= 15 else (60, 300)
    for asset in assets:
        if symbol and asset["symbol"].replace("/", "").upper() != symbol.replace("/", "").replace(" OTC", "_OTC").upper():
            continue
        if asset["market"] != market:
            continue
        if expiry not in asset.get("expiries",[]):
            rejected.append("Эта экспирация не подтверждена каталогом брокера.")
            continue
        if asset["market"] == "otc" and not allow_otc:
            rejected.append("OTC отключён в настройках.")
            continue
        if not asset["is_open"] or now - asset["updated_at"] > 60 or now - asset["quote_time"] > 10:
            rejected.append("Нет свежей цены или подтверждения доступности пары.")
            continue
        analyses = [analyze(histories.get((asset["symbol"], p), []), p, now) for p in periods]
        if all(a["status"] == "candidate" for a in analyses) and analyses[0]["direction"] == analyses[1]["direction"]:
            results.append({"symbol": asset["symbol"], "market": asset["market"], "direction": analyses[0]["direction"],
                            "score": sum(a["score"] for a in analyses), "evidence": analyses[0]["evidence"],
                            "timeframes": list(periods), "payout": asset["payout"], "price": asset["price"]})
        else:
            rejected.append(next((a["reason"] for a in analyses if a["status"] != "candidate"), "Таймфреймы расходятся."))
    return sorted(results, key=lambda x: (-x["score"], -x["payout"], x["symbol"])), rejected
