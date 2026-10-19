"""Connection-scoped catalog validity and complete bars from observed ticks.

No timers refresh market timestamps. A catalog remains scoped to the SAME live
socket; a new socket must deliver its own catalog before it can qualify pairs.
"""
import re

FEED_ID = re.compile(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}")
SCHEMA = """
CREATE TABLE IF NOT EXISTS live_bars(user_id INTEGER NOT NULL,symbol TEXT NOT NULL,time INTEGER NOT NULL,open REAL NOT NULL,high REAL NOT NULL,low REAL NOT NULL,close REAL NOT NULL,first REAL NOT NULL,last REAL NOT NULL,gap REAL NOT NULL,count INTEGER NOT NULL,PRIMARY KEY(user_id,symbol));
"""


def migrate(db):
    columns = {r[1] for r in db.execute("PRAGMA table_info(assets)")}
    for name, kind in (("catalog_feed", "TEXT"), ("quote_feed", "TEXT"), ("feed_connected", "INTEGER NOT NULL DEFAULT 0"), ("history_status", "TEXT")):
        if name not in columns:
            db.execute(f"ALTER TABLE assets ADD COLUMN {name} {kind}")
    if "origin" not in {r[1] for r in db.execute("PRAGMA table_info(candles)")}:
        db.execute("ALTER TABLE candles ADD COLUMN origin TEXT NOT NULL DEFAULT 'broker'")
    db.executescript(SCHEMA)


def catalog_current(asset, now):
    if asset.get("catalog_feed"):
        return bool(asset.get("feed_connected") and asset.get("quote_feed") == asset["catalog_feed"] and asset.get("quote_time") is not None and now-asset["quote_time"] <= 10)
    return now-asset["updated_at"] <= 60  # Old modules get no invented continuity.


def gate(asset, *, expiry, min_payout, now):
    live = asset.get("quote_time") is not None and now-asset["quote_time"] <= 10
    if not asset["is_open"]:
        code, reason = "closed", "Брокер отметил пару закрытой."
    elif asset["payout"] < min_payout:
        code, reason = "payout", f"Выплата {round(asset['payout']*100)}% ниже минимума {round(min_payout*100)}%."
    elif expiry not in asset["expiries"]:
        code, reason = "expiry", f"Брокер не подтвердил сделку на {expiry} мин."
    elif not catalog_current(asset, now):
        code, reason = "catalog", "Нет действующего каталога этого канала. Обнови вкладку Pocket Option; старый модуль не передаёт состояние канала."
    elif not live:
        code, reason = "quote", "Нет свежей цены: последняя котировка старше 10 секунд."
    else:
        code, reason = "ready", "Каталог и цена получены."
    # Eligible reflects metadata only; current-socket validity needs its ticks.
    eligible = bool(asset["is_open"] and asset["payout"] >= min_payout and expiry in asset["expiries"] and catalog_current(asset, now))
    return {"code": code, "reason": reason, "eligible": eligible, "quote_live": live,
            "catalog_age": max(0, round(now-asset["updated_at"])),
            "catalog_source": "live_socket" if asset.get("catalog_feed") else "legacy_snapshot"}


def live_tick(db, uid, pair, at, price):
    """Build M1 ONLY with actual ticks near BOTH ends and no gap over 10s."""
    start = int(at//60)*60
    old = db.execute("SELECT * FROM live_bars WHERE user_id=? AND symbol=?", (uid, pair)).fetchone()
    if old and at <= old["last"]:
        return
    if old and start == old["time"]:
        db.execute("UPDATE live_bars SET high=max(high,?),low=min(low,?),close=?,last=?,gap=max(gap,?),count=count+1 WHERE user_id=? AND symbol=?", (price,price,price,at,at-old["last"],uid,pair))
        return
    if old and start == old["time"]+60 and old["count"] >= 2 and old["first"] <= old["time"]+5 and old["last"] >= old["time"]+50 and old["gap"] <= 10 and at-old["last"] <= 10:
        db.execute("INSERT OR IGNORE INTO candles(user_id,symbol,period,time,open,high,low,close,origin) VALUES (?,?,60,?,?,?,?,?,'ticks')", (uid,pair,old["time"],old["open"],old["high"],old["low"],old["close"]))
    db.execute("INSERT INTO live_bars VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id,symbol) DO UPDATE SET time=excluded.time,open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,first=excluded.first,last=excluded.last,gap=excluded.gap,count=excluded.count", (uid,pair,start,price,price,price,price,at,at,0,1))
    db.execute("DELETE FROM candles WHERE user_id=? AND symbol=? AND period=60 AND time<?", (uid,pair,at-1600*60))
