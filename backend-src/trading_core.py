"""Per-user read-only account journal and simulated trading risk controls.

No Pocket Option credential storage, order placement, or withdrawal code exists.
Money is represented by integer cents; risk reservations include all open stakes.
"""
from contextlib import contextmanager
from decimal import Decimal, DecimalException, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import re
import secrets
import sqlite3
import time

from market_analysis import rank, coverage
from signal_sessions import SCHEMA, SignalSessions


class DomainError(ValueError):
    def __init__(self, message, status=400):
        self.status = status
        super().__init__(message)


def money(value, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise DomainError("Сумма должна быть числом с точностью до цента.")
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number.copy_abs() > 10000000:
            raise InvalidOperation()
        rounded = number.quantize(Decimal("0.01"))
        if rounded != number:
            raise InvalidOperation()
        result = int(rounded * 100)
        if positive and result <= 0:
            raise InvalidOperation()
        return result
    except (DecimalException, ValueError, OverflowError):
        raise DomainError("Проверь сумму: максимум два знака после запятой.") from None


def amount(cents):
    return f"{Decimal(cents) / 100:.2f}"


def stamp(value, now, *, history=False):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 1000000000 or value > now + 5:
        raise DomainError("Некорректное время данных.")
    if not history and now - value > 60:
        raise DomainError("Получены устаревшие данные.")
    return float(value)


def symbol(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./ #-]{1,31}", value):
        raise DomainError("Некорректная пара.")
    return value


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Ledger(SignalSessions):
    def __init__(self, path, *, clock=time.time):
        self.path, self.clock = Path(path), clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS profiles(user_id INTEGER PRIMARY KEY, settings TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS pairing(user_id INTEGER PRIMARY KEY, code_hash TEXT UNIQUE, expires REAL, token_hash TEXT UNIQUE, token_expires REAL);
            CREATE TABLE IF NOT EXISTS accounts(user_id INTEGER NOT NULL, mode TEXT NOT NULL, account_id TEXT NOT NULL, currency TEXT NOT NULL, balance INTEGER NOT NULL, stamp REAL NOT NULL, PRIMARY KEY(user_id,mode));
            CREATE TABLE IF NOT EXISTS manual_trades(user_id INTEGER NOT NULL, mode TEXT NOT NULL, account_id TEXT NOT NULL, trade_id TEXT NOT NULL, symbol TEXT NOT NULL, direction TEXT NOT NULL, stake INTEGER NOT NULL, opened_at REAL NOT NULL, expires_at REAL NOT NULL, closed_at REAL, pnl INTEGER, status TEXT NOT NULL, stamp REAL NOT NULL, PRIMARY KEY(user_id,mode,account_id,trade_id));
            CREATE TABLE IF NOT EXISTS assets(user_id INTEGER NOT NULL, symbol TEXT NOT NULL, market TEXT NOT NULL, is_open INTEGER NOT NULL, payout REAL NOT NULL, updated_at REAL NOT NULL, price REAL, quote_time REAL, expiries TEXT NOT NULL, PRIMARY KEY(user_id,symbol));
            CREATE TABLE IF NOT EXISTS candles(user_id INTEGER NOT NULL, symbol TEXT NOT NULL, period INTEGER NOT NULL, time REAL NOT NULL, open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL, PRIMARY KEY(user_id,symbol,period,time));
            CREATE TABLE IF NOT EXISTS risk(user_id INTEGER PRIMARY KEY, config TEXT NOT NULL, running INTEGER NOT NULL DEFAULT 0, locked_day INTEGER, reason TEXT, balance INTEGER NOT NULL DEFAULT 10000, run_day INTEGER);
            CREATE TABLE IF NOT EXISTS paper_trades(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, symbol TEXT NOT NULL, direction TEXT NOT NULL, stake INTEGER NOT NULL, payout REAL NOT NULL, opened_at REAL NOT NULL, expires_at REAL NOT NULL, entry_price REAL NOT NULL, exit_price REAL, pnl INTEGER, status TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS analysis(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, created_at REAL NOT NULL, result TEXT NOT NULL);
            """)
            db.executescript(SCHEMA)
            if "run_day" not in {r[1] for r in db.execute("PRAGMA table_info(risk)")}:
                db.execute("ALTER TABLE risk ADD COLUMN run_day INTEGER")

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                # Serialize read-modify-write risk checks across HTTP workers.
                db.execute("BEGIN IMMEDIATE")
                yield db
        finally:
            db.close()

    @staticmethod
    def setup(db, uid):
        settings = {"market": "normal", "selection": "auto", "symbol": None, "expiry": 5, "allow_otc": False, "min_payout": 85}
        risk = {"daily_loss": "10.00", "daily_target": "20.00", "balance_floor": "90.00", "stake": "1.00", "max_open": 1, "max_overlays": 0, "goal_start": "100.00", "goal_target": "1000.00"}
        db.execute("INSERT OR IGNORE INTO profiles VALUES (?,?)", (uid, json.dumps(settings)))
        saved = json.loads(db.execute("SELECT settings FROM profiles WHERE user_id=?", (uid,)).fetchone()[0])
        migrated = {**saved, "min_payout": saved.get("min_payout", 85)}
        if migrated.get("expiry") not in (5,10,15):
            migrated["expiry"] = 15 if type(saved.get("expiry")) is int and saved["expiry"] >= 15 else 5
        if migrated != saved:
            db.execute("UPDATE profiles SET settings=? WHERE user_id=?", (json.dumps(migrated), uid))
        db.execute("INSERT OR IGNORE INTO risk(user_id,config) VALUES (?,?)", (uid, json.dumps(risk)))

    def pair(self, uid):
        code = secrets.token_hex(12)
        with self.db() as db:
            self.setup(db, uid)
            db.execute("INSERT INTO pairing(user_id,code_hash,expires) VALUES (?,?,?) ON CONFLICT(user_id) DO UPDATE SET code_hash=excluded.code_hash,expires=excluded.expires", (uid, digest(code), self.clock()+300))
        return {"code": code, "expires_in": 300}

    def claim(self, code):
        if not isinstance(code, str) or not re.fullmatch(r"[a-f0-9]{24}", code):
            raise DomainError("Код связи неверен или истёк.", 401)
        token = secrets.token_urlsafe(32)
        with self.db() as db:
            row = db.execute("SELECT user_id FROM pairing WHERE code_hash=? AND expires>?", (digest(code), self.clock())).fetchone()
            if not row:
                raise DomainError("Код связи неверен или истёк.", 401)
            db.execute("UPDATE pairing SET code_hash=NULL,expires=NULL,token_hash=?,token_expires=? WHERE user_id=?", (digest(token), self.clock()+30*86400, row[0]))
        return {"token": token, "expires_in": 30*86400}

    def unlink(self, uid):
        with self.db() as db:
            db.execute("DELETE FROM pairing WHERE user_id=?", (uid,))
            db.execute("DELETE FROM accounts WHERE user_id=?", (uid,))
            db.execute("DELETE FROM assets WHERE user_id=?", (uid,))
            db.execute("DELETE FROM candles WHERE user_id=?", (uid,))
            db.execute("DELETE FROM quote_samples WHERE user_id=?", (uid,))
            db.execute("UPDATE risk SET running=0,reason='Связь отключена' WHERE user_id=?", (uid,))

    def settings(self, uid, changes):
        with self.db() as db:
            self.setup(db, uid)
            prefs = json.loads(db.execute("SELECT settings FROM profiles WHERE user_id=?", (uid,)).fetchone()[0])
            prefs.setdefault("min_payout", 85)
            if not isinstance(changes, dict) or not changes or not changes.keys() <= prefs.keys():
                raise DomainError("Некорректные настройки.")
            prefs.update(changes)
            if prefs["market"] not in ("normal", "otc") or prefs["selection"] not in ("auto", "manual") or type(prefs["expiry"]) is not int or prefs["expiry"] not in (5,10,15) or type(prefs["allow_otc"]) is not bool:
                raise DomainError("Проверь рынок и экспирацию.")
            if type(prefs["min_payout"]) is not int or not 85 <= prefs["min_payout"] <= 92:
                raise DomainError("Минимальная выплата — от 85 до 92%. Это выплата брокера, не вероятность выигрыша.")
            if prefs["selection"] == "auto":
                prefs["symbol"] = None
            else:
                prefs["symbol"] = symbol(prefs["symbol"])
            db.execute("UPDATE profiles SET settings=? WHERE user_id=?", (json.dumps(prefs), uid))

    def configure_risk(self, uid, changes):
        with self.db() as db:
            self.setup(db, uid)
            row = db.execute("SELECT * FROM risk WHERE user_id=?", (uid,)).fetchone()
            config = json.loads(row["config"])
            if row["running"] or row["locked_day"] == int(self.clock()//86400):
                raise DomainError("Во время работы или дневной блокировки лимиты менять нельзя.", 409)
            if not isinstance(changes, dict) or not changes or not changes.keys() <= config.keys():
                raise DomainError("Неизвестные параметры лимитов.")
            config.update(changes)
            for key in ("daily_loss", "daily_target", "balance_floor", "stake", "goal_start", "goal_target"):
                config[key] = amount(money(config[key], positive=True))
            if type(config["max_open"]) is not int or not 1 <= config["max_open"] <= 3:
                raise DomainError("Одновременно можно держать от 1 до 3 позиций симуляции.")
            # Deliberately unsupported: multiplying losing stakes is not a safety mechanism.
            if type(config["max_overlays"]) is not int or config["max_overlays"] != 0:
                raise DomainError("Перекрытия отключены. Размер позиции после убытка не увеличивается.")
            if money(config["stake"]) > money(config["daily_loss"]) or money(config["goal_target"]) <= money(config["goal_start"]):
                raise DomainError("Ставка не должна превышать стоп; цель должна быть выше начала.")
            day_start = int(self.clock()//86400)*86400
            traded = db.execute("SELECT 1 FROM paper_trades WHERE user_id=? AND opened_at>=? LIMIT 1",(uid,day_start)).fetchone()
            previous = json.loads(row["config"])
            if traded and (any(money(config[k]) > money(previous[k]) for k in ("daily_loss","daily_target","stake")) or money(config["balance_floor"]) < money(previous["balance_floor"]) or config["max_open"] > previous["max_open"]):
                raise DomainError("После первой позиции дня лимиты можно только ужесточить. Ослабление — со следующего дня UTC.",409)
            db.execute("UPDATE risk SET config=? WHERE user_id=?", (json.dumps(config), uid))

    def _risk(self, db, uid, *, additional=0):
        self.setup(db, uid)
        row = db.execute("SELECT * FROM risk WHERE user_id=?", (uid,)).fetchone()
        config = json.loads(row["config"])
        day = int(self.clock()//86400)
        running = bool(row["running"])
        if running and row["run_day"] != day:
            running = False
            db.execute("UPDATE risk SET running=0,reason='Новый день UTC: требуется ручной старт' WHERE user_id=?",(uid,))
        pnl = db.execute("SELECT COALESCE(sum(pnl),0) FROM paper_trades WHERE user_id=? AND status IN ('win','loss','draw') AND expires_at>=? AND expires_at<?", (uid, day*86400, (day+1)*86400)).fetchone()[0]
        exposure, count = db.execute("SELECT COALESCE(sum(stake),0),count(*) FROM paper_trades WHERE user_id=? AND status IN ('open','unresolved')", (uid,)).fetchone()
        reason = (row["reason"] if not row["running"] else "Новый день UTC: требуется ручной старт") if not running else None
        hard = row["locked_day"] == day
        if hard:
            reason = row["reason"] or "Дневной стоп уже сработал."
        elif pnl <= -money(config["daily_loss"]):
            reason, hard = "Достигнут дневной лимит убытка", True
        elif pnl >= money(config["daily_target"]):
            reason, hard = "Дневная цель достигнута", True
        elif row["balance"] - exposure <= money(config["balance_floor"]):
            reason, hard = "Достигнут минимальный баланс", True
        elif db.execute("SELECT 1 FROM paper_trades WHERE user_id=? AND status='unresolved'", (uid,)).fetchone():
            reason = "Нет цены завершения позиции. Симуляция остановлена, ставка остаётся в резерве"
            db.execute("UPDATE risk SET running=0,reason=? WHERE user_id=?", (reason,uid))
            return {"config":config,"running":False,"locked":True,"reason":reason,"balance":amount(row["balance"]),"daily_pnl":amount(pnl),"reserved":amount(exposure),"open_count":count,"resets_at":(day+1)*86400,"execution":"simulation","real_orders_supported":False}
        elif additional and (pnl - exposure - additional < -money(config["daily_loss"]) or row["balance"] - exposure - additional < money(config["balance_floor"])):
            reason = "Новая позиция превысит лимит с учётом открытых ставок"
        elif additional and count >= config["max_open"]:
            reason = "Достигнут лимит открытых позиций"
        if hard:
            db.execute("UPDATE risk SET running=0,locked_day=?,reason=? WHERE user_id=?", (day, reason, uid))
        return {"config": config, "running": running and not hard, "locked": hard,
                "reason": reason, "balance": amount(row["balance"]), "daily_pnl": amount(pnl),
                "reserved": amount(exposure), "open_count": count, "resets_at": (day+1)*86400,
                "execution": "simulation", "real_orders_supported": False}

    def toggle(self, uid, running):
        if type(running) is not bool:
            raise DomainError("Некорректное действие.")
        with self.db() as db:
            state = self._risk(db, uid)
            if running and state["locked"]:
                raise DomainError(state["reason"], 409)
            if running and self._candidate(db, uid).get("status") != "candidate":
                raise DomainError("Для старта нужны свежие данные и подтверждённый сценарий анализа.", 409)
            db.execute("UPDATE risk SET running=?,reason=?,run_day=? WHERE user_id=?", (int(running), None if running else "Остановлено пользователем", int(self.clock()//86400),uid))

    def _market(self, db, uid):
        prefs = json.loads(db.execute("SELECT settings FROM profiles WHERE user_id=?", (uid,)).fetchone()[0])
        assets = [{**dict(x),"expiries":json.loads(x["expiries"])} for x in db.execute("SELECT * FROM assets WHERE user_id=?", (uid,))]
        histories = {}
        for x in db.execute("SELECT symbol,period,time,open,high,low,close FROM candles WHERE user_id=? ORDER BY time", (uid,)):
            histories.setdefault((x["symbol"], x["period"]), []).append(dict(x))
        prefs.setdefault("min_payout", 85)
        return prefs, assets, histories

    def _candidate(self, db, uid):
        prefs, assets, histories = self._market(db, uid)
        candidates, rejected = rank(assets, histories, expiry=prefs["expiry"], market=prefs["market"], symbol=prefs["symbol"], allow_otc=prefs["allow_otc"], min_payout=prefs["min_payout"]/100, now=self.clock())
        if not candidates:
            return {"status": "skipped", "expiry": prefs["expiry"], "reason": rejected[0] if rejected else "Нет доступных пар со свежей ценой и историей. Открой нужные графики в Pocket Option."}
        return {"status": "candidate", **candidates[0], "expiry": prefs["expiry"], "entry_at": self.clock(), "valid_until": self.clock()+30,
                "expires_at": self.clock()+prefs["expiry"]*60, "validated": False, "min_payout": prefs["min_payout"]/100,
                "reason": "Сценарий по закрытым свечам. Вход допустим в указанной зоне в ближайшие 30 секунд; при выходе цены из зоны пропусти вход. Проходимость не измерена.", "probability": None}

    def signal(self, uid):
        with self.db() as db:
            self.setup(db, uid)
            last = db.execute("SELECT max(created_at) FROM analysis WHERE user_id=?", (uid,)).fetchone()[0]
            if last is not None and self.clock()-last < 2:
                raise DomainError("Подожди две секунды.", 429)
            risk = self._risk(db,uid)
            result = {"status":"skipped","reason":risk["reason"]} if risk["locked"] else self._candidate(db, uid)
            cursor = db.execute("INSERT INTO analysis(user_id,created_at,result) VALUES (?,?,?)", (uid, self.clock(), json.dumps(result)))
            result["analysis_id"] = cursor.lastrowid
            db.execute("UPDATE analysis SET result=? WHERE id=?", (json.dumps(result), cursor.lastrowid))
            db.execute("DELETE FROM analysis WHERE user_id=? AND id NOT IN (SELECT id FROM analysis WHERE user_id=? ORDER BY id DESC LIMIT 100)", (uid, uid))
        return result

    def ingest(self, token, batch):
        if not isinstance(token, str) or not 32 <= len(token) <= 100:
            raise DomainError("Модуль связи не авторизован.", 401)
        if not isinstance(batch, dict) or set(batch) != {"events"} or not isinstance(batch["events"], list) or not 1 <= len(batch["events"]) <= 250:
            raise DomainError("Некорректный пакет данных.")
        now = self.clock()
        with self.db() as db:
            pair = db.execute("SELECT user_id FROM pairing WHERE token_hash=? AND token_expires>?", (digest(token), now)).fetchone()
            if not pair:
                raise DomainError("Связь истекла или отключена.", 401)
            uid = pair[0]
            for event in batch["events"]:
                self._event(db, uid, event, now)
            self._settle(db, uid, now)
            self._risk(db, uid)
        return {"ok": True, "received_at": now}

    def _event(self, db, uid, e, now):
        if not isinstance(e, dict) or not isinstance(e.get("type"), str):
            raise DomainError("Неизвестное событие.")
        kind = e["type"]
        fields = {"account": {"type","mode","account_id","currency","balance","time"},
                  "trade": {"type","mode","account_id","id","symbol","direction","stake","opened_at","expires_at","closed_at","pnl","status","time"},
                  "asset": {"type","symbol","market","is_open","payout","time"},
                  "quote": {"type","symbol","price","time"},
                  "candle": {"type","symbol","period","time","open","high","low","close"}}
        fields["asset"].add("expiries")
        required = {"account":{"mode","account_id","currency","balance"},"trade":{"mode","account_id","id","symbol","direction","stake","opened_at","expires_at","status"},"asset":{"symbol","market","is_open","payout","expiries"},"quote":{"symbol","price"},"candle":{"symbol","period","open","high","low","close"}}
        if kind not in fields or not e.keys() <= fields[kind] or not required[kind] <= e.keys():
            raise DomainError("Пакет содержит неподдерживаемые поля.")
        at = stamp(e.get("time"), now, history=kind in ("trade","candle"))
        if kind in ("account", "trade"):
            mode, account = e.get("mode"), str(e.get("account_id", ""))
            if mode not in ("demo", "real") or not re.fullmatch(r"[0-9]{1,20}", account):
                raise DomainError("Счёт или режим не определён.")
            existing = db.execute("SELECT account_id FROM accounts WHERE user_id=? AND mode=?", (uid,mode)).fetchone()
            if existing and existing[0] != account:
                raise DomainError("Обнаружен другой Pocket-счёт. Сначала отключи старую связь.", 409)
        if kind == "account":
            if e.get("currency") != "USD":
                raise DomainError("Пока поддерживается только счёт USD: конвертация не выдумывается.")
            balance = money(e.get("balance"))
            if balance < 0:
                raise DomainError("Баланс не может быть отрицательным.")
            db.execute("INSERT INTO accounts VALUES (?,?,?,?,?,?) ON CONFLICT(user_id,mode) DO UPDATE SET balance=excluded.balance,stamp=excluded.stamp WHERE excluded.stamp>accounts.stamp", (uid,mode,account,"USD",balance,at))
        elif kind == "trade":
            trade_id = str(e.get("id", ""))
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", trade_id) or e.get("direction") not in ("up","down") or e.get("status") not in ("open","win","loss","draw","cancelled"):
                raise DomainError("Некорректная сделка.")
            opened = stamp(e.get("opened_at"), now, history=True)
            expires = e.get("expires_at")
            if type(expires) not in (int,float) or not math.isfinite(expires) or not opened < expires <= opened+86400:
                raise DomainError("Некорректная экспирация.")
            stake = money(e.get("stake"), positive=True)
            status = e["status"]
            pnl = None if status == "open" else money(e.get("pnl"))
            closed = None if status == "open" else stamp(e.get("closed_at"), now, history=True)
            if closed is not None and closed < opened or status == "win" and pnl <= 0 or status == "loss" and not -stake <= pnl < 0 or status in ("draw","cancelled") and pnl != 0:
                raise DomainError("Результат сделки противоречит выплате.")
            old = db.execute("SELECT * FROM manual_trades WHERE user_id=? AND mode=? AND account_id=? AND trade_id=?", (uid,mode,account,trade_id)).fetchone()
            if old and (old["status"] != "open" or at <= old["stamp"]):
                return  # Duplicate or delayed open never reopens a settled trade.
            if old and (old["stake"] != stake or old["symbol"] != e["symbol"] or old["direction"] != e["direction"] or old["opened_at"] != opened or old["expires_at"] != expires):
                raise DomainError("Изменились исходные поля сделки.", 409)
            db.execute("INSERT INTO manual_trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id,mode,account_id,trade_id) DO UPDATE SET closed_at=excluded.closed_at,pnl=excluded.pnl,status=excluded.status,stamp=excluded.stamp", (uid,mode,account,trade_id,symbol(e.get("symbol")),e["direction"],stake,opened,expires,closed,pnl,status,at))
        elif kind == "asset":
            pair = symbol(e.get("symbol"))
            payout = e.get("payout")
            expiries = e.get("expiries")
            if e.get("market") not in ("normal","otc") or type(e.get("is_open")) is not bool or type(payout) not in (int,float) or not math.isfinite(payout) or not 0 < payout <= 1 or not isinstance(expiries,list) or len(expiries)>100 or any(type(x) is not int or not 1<=x<=1440 for x in expiries):
                raise DomainError("Некорректная доступность или выплата пары.")
            db.execute("INSERT INTO assets(user_id,symbol,market,is_open,payout,updated_at,expiries) VALUES (?,?,?,?,?,?,?) ON CONFLICT(user_id,symbol) DO UPDATE SET market=excluded.market,is_open=excluded.is_open,payout=excluded.payout,updated_at=excluded.updated_at,expiries=excluded.expiries WHERE excluded.updated_at>assets.updated_at", (uid,pair,e["market"],int(e["is_open"]),payout,at,json.dumps(expiries)))
        elif kind == "quote":
            pair, price = symbol(e.get("symbol")), e.get("price")
            if type(price) not in (int,float) or not math.isfinite(price) or not 0 < price <= 1e9:
                raise DomainError("Некорректная цена.")
            db.execute("UPDATE assets SET price=?,quote_time=? WHERE user_id=? AND symbol=? AND (quote_time IS NULL OR quote_time<?)", (price,at,uid,pair,at))
            if db.execute("SELECT 1 FROM assets WHERE user_id=? AND symbol=?", (uid,pair)).fetchone():
                db.execute("INSERT OR IGNORE INTO quote_samples VALUES (?,?,?,?)", (uid,pair,int(at),price))
                db.execute("DELETE FROM quote_samples WHERE user_id=? AND symbol=? AND time<?", (uid,pair,now-7200))
        elif kind == "candle":
            pair, period = symbol(e.get("symbol")), e.get("period")
            prices = [e.get(k) for k in ("open","high","low","close")]
            if type(period) is not int or period not in (60,300,900) or any(type(v) not in (int,float) or not math.isfinite(v) or not 0 < v <= 1e9 for v in prices):
                raise DomainError("Некорректная свеча.")
            o,h,l,c = prices
            if l > min(o,c) or h < max(o,c) or at+period > now or at % period > 1:
                raise DomainError("Свеча ещё открыта или нарушен OHLC.")
            db.execute("INSERT OR IGNORE INTO candles VALUES (?,?,?,?,?,?,?,?)", (uid,pair,period,at,o,h,l,c))
            db.execute("DELETE FROM candles WHERE user_id=? AND symbol=? AND period=? AND time<?", (uid,pair,period,now-1600*period))

    def _settle(self, db, uid, now):
        # The first observed quote within five seconds of expiry is used. Missing
        # or delayed settlement data never becomes a fabricated win or loss.
        for trade in db.execute("SELECT * FROM paper_trades WHERE user_id=? AND status='open' AND expires_at<=?", (uid,now)).fetchall():
            q = db.execute("SELECT price,quote_time FROM assets WHERE user_id=? AND symbol=?", (uid,trade["symbol"])).fetchone()
            if not q or q[1] is None or not trade["expires_at"] <= q[1] <= trade["expires_at"]+5:
                if now > trade["expires_at"]+10:
                    db.execute("UPDATE paper_trades SET status='unresolved' WHERE id=?",(trade["id"],))
                continue
            move = q[0]-trade["entry_price"]
            pnl = 0 if move == 0 else int(Decimal(trade["stake"])*Decimal(str(trade["payout"]))) if (move>0) == (trade["direction"]=="up") else -trade["stake"]
            status = "draw" if pnl==0 else "win" if pnl>0 else "loss"
            db.execute("UPDATE paper_trades SET exit_price=?,pnl=?,status=? WHERE id=? AND status='open'", (q[0],pnl,status,trade["id"]))
            db.execute("UPDATE risk SET balance=balance+? WHERE user_id=?", (pnl,uid))

    def paper_step(self, uid):
        """One serial simulation step. Never sends an order to Pocket Option."""
        with self.db() as db:
            now = self.clock()
            self._settle(db, uid, now)
            risk = self._risk(db, uid)
            if not risk["running"]:
                return
            stake = money(risk["config"]["stake"])
            guard = self._risk(db, uid, additional=stake)
            if guard["reason"]:
                return
            fresh = db.execute("SELECT 1 FROM assets WHERE user_id=? AND is_open=1 AND quote_time>? AND updated_at>?",(uid,now-10,now-60)).fetchone()
            if not fresh:
                db.execute("UPDATE risk SET running=0,reason='Связь с котировками потеряна. Требуется ручной старт' WHERE user_id=?",(uid,))
                return
            candidate = self._candidate(db, uid)
            if candidate["status"] != "candidate":
                return
            # One simulated entry per completed analysis bar, even with fast polling.
            last = db.execute("SELECT max(opened_at) FROM paper_trades WHERE user_id=?", (uid,)).fetchone()[0]
            if last is not None and now-last < candidate["timeframes"][0]:
                return
            db.execute("INSERT INTO paper_trades(user_id,symbol,direction,stake,payout,opened_at,expires_at,entry_price,status) VALUES (?,?,?,?,?,?,?,?,'open')", (uid,candidate["symbol"],candidate["direction"],stake,candidate["payout"],now,now+candidate["expiry"]*60,candidate["price"]))

    def state(self, user):
        uid, now = user["id"], self.clock()
        with self.db() as db:
            self.setup(db, uid)
            prefs = json.loads(db.execute("SELECT settings FROM profiles WHERE user_id=?", (uid,)).fetchone()[0])
            risk = self._risk(db, uid)
            accounts = [{"mode":r["mode"], "id":r["account_id"], "currency":r["currency"], "balance":amount(r["balance"]), "updated_at":r["stamp"], "live":now-r["stamp"]<=15} for r in db.execute("SELECT * FROM accounts WHERE user_id=?", (uid,))]
            trades = [dict(r) for r in db.execute("SELECT * FROM manual_trades WHERE user_id=? ORDER BY opened_at DESC LIMIT 100", (uid,))]
            for t in trades:
                t["stake"], t["pnl"] = amount(t["stake"]), None if t["pnl"] is None else amount(t["pnl"])
            paper = [dict(r) for r in db.execute("SELECT * FROM paper_trades WHERE user_id=? ORDER BY id DESC LIMIT 30", (uid,))]
            for t in paper:
                t["stake"], t["pnl"] = amount(t["stake"]), None if t["pnl"] is None else amount(t["pnl"])
            outcomes = {r[0]:r[1] for r in db.execute("SELECT status,count(*) FROM manual_trades WHERE user_id=? GROUP BY status", (uid,))}
            rows = db.execute("SELECT created_at,result FROM analysis WHERE user_id=? ORDER BY id DESC LIMIT 10", (uid,)).fetchall()
            recent = [{"created_at":r[0], **json.loads(r[1]), "expiry":json.loads(r[1]).get("expiry",prefs["expiry"])} for r in rows]
            for request in recent:
                request.pop("candles", None)  # Candle chart is sent once by /api/signal.
            wins, losses = outcomes.get("win",0), outcomes.get("loss",0)
            paired = db.execute("SELECT 1 FROM pairing WHERE user_id=? AND token_hash IS NOT NULL AND token_expires>?", (uid,now)).fetchone() is not None
            live = bool(db.execute("SELECT 1 FROM assets WHERE user_id=? AND quote_time>?", (uid,now-10)).fetchone())
            count = db.execute("SELECT count(*) FROM assets WHERE user_id=? AND is_open=1 AND updated_at>?", (uid,now-60)).fetchone()[0]
            sessions = self._sessions(db, uid, now)
            market_prefs, market_assets, market_histories = self._market(db, uid)
            feed = coverage(market_assets, market_histories, market=prefs["market"], expiry=prefs["expiry"], min_payout=market_prefs["min_payout"]/100, now=now)
            # Goal follows settled trades observed by this connector, never deposits.
            real_account = next((a for a in accounts if a["mode"]=="real"),None)
            goal_pnl = db.execute("SELECT COALESCE(sum(pnl),0) FROM manual_trades WHERE user_id=? AND mode='real' AND account_id=? AND status!='open'", (uid,real_account["id"] if real_account else "")).fetchone()[0]
            start, target = money(risk["config"]["goal_start"]), money(risk["config"]["goal_target"])
        return {"mode":"api", "user":user, "preferences":prefs, "risk":risk, "accounts":accounts, "trades":trades, "paper_trades":paper,
                "goal":{"start":amount(start), "target":amount(target), "earned":amount(goal_pnl), "current":amount(start+goal_pnl), "progress":max(0,min(1,goal_pnl/(target-start))), "source":"observed_real_settlements", "available":real_account is not None},
                "statistics":{"signals":sum(outcomes.values()),"wins":wins,"losses":losses,"draws":outcomes.get("draw",0),"win_rate":wins/(wins+losses) if wins+losses else None},
                "connection":{"authenticated":paired,"live":live,"model_ready":False,"reason":"Котировки поступают. Анализ по проверяемым правилам структуры." if live else "Подключи модуль браузера для котировок, баланса и сделок."},
                "signal_sessions":sessions, "signal_version":1, "feed":feed, "recent_requests":recent, "catalog":{"verified_count":count}, "minimum_expiry":5, "bot_username":"Tr1edBot_bot", "server_time":now}
