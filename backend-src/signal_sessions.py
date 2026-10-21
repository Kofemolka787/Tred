"""User-confirmed observations, never broker orders or automatic settlements."""
import json
import math
from market_feed import catalog_current


SCHEMA = """
CREATE TABLE IF NOT EXISTS quote_samples(user_id INTEGER NOT NULL,symbol TEXT NOT NULL,time INTEGER NOT NULL,price REAL NOT NULL,PRIMARY KEY(user_id,symbol,time));
CREATE TABLE IF NOT EXISTS signal_sessions(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,analysis_id INTEGER NOT NULL,symbol TEXT NOT NULL,direction TEXT NOT NULL,mode TEXT NOT NULL,account_id TEXT NOT NULL,expiry INTEGER NOT NULL,payout REAL NOT NULL,opened_at REAL NOT NULL,expires_at REAL NOT NULL,entry_price REAL NOT NULL,entry_source TEXT NOT NULL,result TEXT,result_at REAL,evidence TEXT NOT NULL,UNIQUE(user_id,analysis_id));
"""


class SignalSessions:
    def watch(self, uid, value):
        from trading_core import DomainError
        if not isinstance(value, dict) or not set(value) <= {"analysis_id", "mode", "entry_price"} or type(value.get("analysis_id")) is not int or value.get("mode") not in ("real", "demo"):
            raise DomainError("Проверь выбранный сигнал и режим счёта.")
        with self.db() as db:
            self.setup(db, uid)
            old = db.execute("SELECT * FROM signal_sessions WHERE user_id=? AND analysis_id=?", (uid, value["analysis_id"])).fetchone()
            if old:
                if value["mode"] != old["mode"] or "entry_price" in value and value["entry_price"] != old["entry_price"]:
                    raise DomainError("Этот сигнал уже отмечен с другими параметрами входа.", 409)
                return old["id"]  # Retry is idempotent; cannot duplicate or reopen.
            now = self.clock()
            row = db.execute("SELECT result FROM analysis WHERE user_id=? AND id=?", (uid, value["analysis_id"])).fetchone()
            if not row:
                raise DomainError("Сигнал не найден.", 404)
            signal = json.loads(row[0])
            if signal.get("status") != "candidate" or now > signal.get("valid_until", 0):
                raise DomainError("Окно входа закончилось. Запроси новый анализ.", 409)
            account = db.execute("SELECT account_id FROM accounts WHERE user_id=? AND mode=?", (uid, value["mode"])).fetchone()
            if not account:
                raise DomainError("Открой этот режим счёта в Pocket Option, чтобы получить его ID.", 409)
            if db.execute("SELECT count(*) FROM signal_sessions WHERE user_id=? AND result IS NULL", (uid,)).fetchone()[0] >= 3:
                raise DomainError("Сначала заверши одну из трёх отслеживаемых сделок.", 409)
            asset = db.execute("SELECT * FROM assets WHERE user_id=? AND symbol=?", (uid, signal["symbol"])).fetchone()
            if not asset or asset["market"] != signal["market"] or asset["quote_time"] is None or now-asset["quote_time"] > 10 or not catalog_current(dict(asset),now) or not asset["is_open"] or asset["payout"] < signal.get("min_payout", .85) or signal["expiry"] not in json.loads(asset["expiries"]):
                raise DomainError("Цена, выплата или доступность пары изменились. Запроси новый анализ.", 409)
            price = asset["price"]
            if not signal["entry_low"] <= price <= signal["entry_high"] or (price <= signal["invalidation"] if signal["direction"] == "up" else price >= signal["invalidation"]):
                raise DomainError("Цена вышла из зоны входа. Вход пропущен.", 409)
            # New bars must not silently replace the reviewed setup. A causal
            # SMC zone can survive a bar close only after fresh full revalidation.
            latest = db.execute("SELECT max(time) FROM candles WHERE user_id=? AND symbol=? AND period=?", (uid, signal["symbol"], signal["timeframes"][0])).fetchone()[0]
            if latest is not None and latest > signal["bar_time"]:
                current=[]
                if signal.get('ai') and signal.get('setup_id'):
                    from market_analysis import rank, same_setup
                    prefs,_,histories=self._market(db,uid)
                    current,_=rank([{**dict(asset),'expiries':json.loads(asset['expiries'])}],histories,expiry=signal['expiry'],market=signal['market'],symbol=signal['symbol'],allow_otc=signal['market']=='otc',now=now,min_payout=signal.get('min_payout',.85))
                    current=[c for c in current if same_setup(c,signal)]
                if not current:
                    raise DomainError("Закрылась новая свеча и прежний сценарий не подтверждён. Обнови анализ перед входом.", 409)
            entry = value.get("entry_price", price)
            if type(entry) not in (int, float) or not math.isfinite(entry) or entry <= 0 or not signal["entry_low"] <= entry <= signal["entry_high"]:
                raise DomainError("Цена входа должна находиться в указанной зоне.")
            source = "user" if "entry_price" in value else "observed_quote"
            result = db.execute("INSERT INTO signal_sessions(user_id,analysis_id,symbol,direction,mode,account_id,expiry,payout,opened_at,expires_at,entry_price,entry_source,evidence) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (uid, value["analysis_id"], signal["symbol"], signal["direction"], value["mode"], account[0], signal["expiry"], asset["payout"], now, now+signal["expiry"]*60, entry, source, json.dumps(signal)))
            return result.lastrowid

    def record_result(self, uid, value):
        from trading_core import DomainError
        if not isinstance(value, dict) or set(value) != {"session_id", "result"} or type(value.get("session_id")) is not int or value.get("result") not in ("win", "loss", "draw"):
            raise DomainError("Выбери плюс, минус или возврат.")
        with self.db() as db:
            row = db.execute("SELECT * FROM signal_sessions WHERE user_id=? AND id=?", (uid, value["session_id"])).fetchone()
            if not row:
                raise DomainError("Сделка не найдена.", 404)
            if row["result"]:
                if row["result"] != value["result"]:
                    raise DomainError("Результат уже записан.", 409)
                return
            if self.clock() < row["expires_at"]:
                raise DomainError("Дождись окончания выбранного времени сделки.", 409)
            db.execute("UPDATE signal_sessions SET result=?,result_at=? WHERE user_id=? AND id=? AND result IS NULL", (value["result"], self.clock(), uid, value["session_id"]))

    def _sessions(self, db, uid, now):
        sessions = []
        for row in db.execute("SELECT * FROM signal_sessions WHERE user_id=? ORDER BY id DESC LIMIT 50", (uid,)):
            item = dict(row)
            item.pop("user_id")
            item["setup"] = json.loads(item.pop("evidence"))
            # Polls need the explanation and live path, not repeated pre-entry OHLC.
            item["setup"].pop("candles", None)
            quote = db.execute("SELECT price,quote_time FROM assets WHERE user_id=? AND symbol=?", (uid, row["symbol"])).fetchone()
            item["current_price"] = quote[0] if quote else None
            item["quote_time"] = quote[1] if quote else None
            item["live"] = bool(quote and quote[1] is not None and now-quote[1] <= 10)
            item["status"] = row["result"] or ("awaiting_result" if now >= row["expires_at"] else "open")
            if row["result"]:
                item["series"] = []
            else:
                # One point per 5s bucket; missing time stays a visible gap.
                points = db.execute("SELECT time,price FROM quote_samples WHERE user_id=? AND symbol=? AND time>=? AND time<=? ORDER BY time", (uid, row["symbol"], row["opened_at"]-1, min(now, row["expires_at"]))).fetchall()
                buckets = {int(p[0]//5): {"time": p[0], "price": p[1]} for p in points}
                item["series"] = list(buckets.values())[-1000:]
            sessions.append(item)
        return sessions
