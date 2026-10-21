"""User-scoped market-data plans and asynchronous analysis, never broker orders."""
import json
import math

from market_analysis import frame_data, same_setup, history
from market_feed import catalog_current, catalog_observed
from market_strategies import closed_tail

SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_bridge(user_id INTEGER PRIMARY KEY,seen_at REAL NOT NULL,code TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scan_jobs(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,preferences TEXT NOT NULL,started_at REAL NOT NULL,deadline REAL NOT NULL,status TEXT NOT NULL,result_id INTEGER);
CREATE INDEX IF NOT EXISTS scan_jobs_owner ON scan_jobs(user_id,id);
"""
CODES = ("ready", "no_tab", "no_socket", "waiting_catalog", "request_failed", "unsupported_module")


def migrate(db):
    columns={r[1] for r in db.execute('PRAGMA table_info(scan_jobs)')}
    if 'watch_symbols' not in columns:
        db.execute("ALTER TABLE scan_jobs ADD COLUMN watch_symbols TEXT NOT NULL DEFAULT '[]'")
    if 'quote_until' not in columns:
        db.execute('ALTER TABLE scan_jobs ADD COLUMN quote_until REAL')


def selection(asset, prefs):
    def key(value):
        value = (value or "").replace("/", "").upper().replace(" OTC", "_OTC")
        return value + "_OTC" if prefs["market"] == "otc" and not value.endswith("_OTC") else value
    return asset["market"] == prefs["market"] and (not prefs["symbol"] or key(asset["symbol"]) == key(prefs["symbol"])) and (prefs["market"] != "otc" or prefs["allow_otc"]) and asset["is_open"] and asset["payout"] >= prefs["min_payout"] / 100 and prefs["expiry"] in asset["expiries"]


def pool(assets, prefs):
    return sorted((a for a in assets if selection(a, prefs)), key=lambda a: (a["payout"] < .92, -a["payout"], a["symbol"]))


class MarketScanner:
    def bridge_plan(self, token, value):
        from trading_core import DomainError, digest
        if not isinstance(token, str) or not 32 <= len(token) <= 100:
            raise DomainError("Модуль связи не авторизован.", 401)
        if not isinstance(value, dict) or set(value) != {"code"} or value["code"] not in CODES:
            raise DomainError("Неподдерживаемый статус сканера.")
        now = self.clock()
        with self.db() as db:
            paired = db.execute("SELECT user_id FROM pairing WHERE token_hash=? AND token_expires>?", (digest(token), now)).fetchone()
            if not paired:
                raise DomainError("Связь истекла или отключена.", 401)
            uid = paired[0]
            db.execute("INSERT INTO scan_bridge VALUES (?,?,?) ON CONFLICT(user_id) DO UPDATE SET seen_at=excluded.seen_at,code=excluded.code", (uid, now, value["code"]))
            prefs, assets, histories = self._market(db, uid)
            candidates = pool(assets, prefs)
            # Active observations remain streamed when the selected market changes.
            watched = {r[0] for r in db.execute("SELECT symbol FROM signal_sessions WHERE user_id=? AND result IS NULL AND expires_at>?", (uid, now))}
            for row in db.execute("SELECT watch_symbols FROM scan_jobs WHERE user_id=? AND status='ai' AND started_at>?",(uid,now-240)):
                watched.update(json.loads(row[0]))
            for row in db.execute("SELECT result FROM analysis WHERE user_id=? ORDER BY id DESC LIMIT 5", (uid,)):
                result = json.loads(row[0])
                if result.get("status") == "candidate" and result.get("valid_until", 0) > now:
                    watched.add(result["symbol"])
            by_symbol = {a["symbol"]: a for a in assets}
            prioritized = [by_symbol[s] for s in sorted(watched) if s in by_symbol]
            prioritized.extend(a for a in candidates if a["symbol"] not in watched)
            rows = []
            for a in prioritized:
                if not a.get("catalog_feed") or not a.get("feed_connected"):
                    continue
                frames, bars, checks = frame_data(histories, a["symbol"], prefs["expiry"], now)
                usable = all(c.get("history_ready") for c in checks)
                effective = dict(zip(frames, bars)) if usable else {}
                needed = []
                for period in frames:
                    # frame_data can already supply complete M2/M10 etc. from
                    # contiguous lower-period bars. Reuse that verified history
                    # instead of repeatedly fetching a new 160-bar native copy.
                    data = effective[period] if period in effective else history(histories,a["symbol"],period,now)
                    closed = math.floor(now / period) * period - period
                    tail = closed_tail(data,period,now)
                    complete = len(tail) >= 80 and all(b["time"] - x["time"] == period for x, b in zip(tail, tail[1:]))
                    if not complete or not data or data[-1]["time"] < closed:
                        task={"period": period, "bars": 160 if not complete else 5}
                        # Short native pages need an older end bound. Repeating
                        # the same newest 24 bars can never produce 80 closed
                        # bars. Stale data is refreshed at the current time first.
                        if not complete and tail and now-(tail[-1]['time']+period)<=period+10:
                            task['before']=math.floor(tail[0]['time']/period)*period
                        needed.append(task)
                quoted = a.get("quote_time") is not None and now-a["quote_time"] <= 8 and a.get("quote_feed") == a["catalog_feed"]
                priority=a['symbol'] in watched
                # Focused instruments keep a task present even between polls;
                # MAIN skips it while its own genuine tick is <=5 seconds old.
                rows.append({"symbol": a["symbol"], "feed_id": a["catalog_feed"], "history": needed, "quote": priority or not quoted,"priority":priority})
            return {"ok": True, "plan": {"version": 1, "until": now + 15, "pairs": rows, "total": len(candidates)}}

    def _scan_progress(self, db, uid, prefs, assets, histories, now):
        eligible = pool(assets, prefs)
        ready = quoted = history_ready = analysis_ready = 0
        for a in eligible:
            _, _, checks = frame_data(histories, a["symbol"], prefs["expiry"], now)
            enough = all(c.get("history_ready") for c in checks)
            current = a.get("quote_time") is not None and now - a["quote_time"] <= 10
            history_ready += enough
            analysis_ready += bool(enough and catalog_observed(a,now))
            quoted += current
            ready += bool(enough and current and catalog_current(a, now))
        bridge = db.execute("SELECT * FROM scan_bridge WHERE user_id=?", (uid,)).fetchone()
        return {"total": len(eligible), "checked": ready, "history_ready": history_ready,'analysis_ready':analysis_ready, "quoted": quoted,
                "bridge_live": bool(bridge and now - bridge["seen_at"] <= 20),
                "bridge_code": bridge["code"] if bridge and now - bridge["seen_at"] <= 20 else "offline"}

    def start_scan(self, uid):
        if not self.ai.status(uid)["configured"]:
            return {"status": "setup_required", "reason": "Для ИИ-анализа запусти SETUP-LUNAROUTE.cmd на компьютере и введи ключ LunaRoute локально. Для прямого OpenAI есть SETUP-AI.cmd. Ключ не нужно отправлять в Telegram. После сохранения нажми «Получить сигнал» снова."}
        with self.db() as db:
            self.setup(db, uid)
            prefs, assets, _ = self._market(db, uid)
            signature = json.dumps(prefs, sort_keys=True)
            existing = db.execute("SELECT * FROM scan_jobs WHERE user_id=? AND status IN ('loading','ai') ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
            if existing and existing["preferences"] == signature and existing["deadline"] > self.clock():
                job_id = existing["id"]
            else:
                db.execute("UPDATE scan_jobs SET status='cancelled' WHERE user_id=? AND status IN ('loading','ai')", (uid,))
                deadline = self.clock() + min(180, max(45, 10 + len(pool(assets, prefs)) * 1.8))
                job_id = db.execute("INSERT INTO scan_jobs(user_id,preferences,started_at,deadline,status) VALUES (?,?,?,?,'loading')", (uid, signature, self.clock(), deadline)).lastrowid
                db.execute("DELETE FROM scan_jobs WHERE user_id=? AND id NOT IN (SELECT id FROM scan_jobs WHERE user_id=? ORDER BY id DESC LIMIT 20)", (uid, uid))
        return self.poll_scan(uid, {"scan_id": job_id})

    def poll_scan(self, uid, value, *, cancel=False):
        from trading_core import DomainError
        if not isinstance(value, dict) or set(value) != {"scan_id"} or type(value["scan_id"]) is not int:
            raise DomainError("Некорректный запрос анализа.")
        now = self.clock()
        with self.db() as db:
            job = db.execute("SELECT * FROM scan_jobs WHERE user_id=? AND id=?", (uid, value["scan_id"])).fetchone()
            if not job:
                raise DomainError("Проверка не найдена.", 404)
            if job["result_id"]:
                row = db.execute("SELECT result FROM analysis WHERE user_id=? AND id=?", (uid, job["result_id"])).fetchone()
                if row:
                    return json.loads(row[0])
            prefs, assets, histories = self._market(db, uid)
            if cancel or job["status"] == "cancelled" or json.dumps(prefs, sort_keys=True) != job["preferences"]:
                db.execute("UPDATE scan_jobs SET status='cancelled' WHERE id=?", (job["id"],))
                return {"status": "cancelled", "scan_id": job["id"], "reason": "Проверка остановлена."}
            progress = self._scan_progress(db, uid, prefs, assets, histories, now)
            risk = self._risk(db, uid)
            # A silent pair must not hold every other complete chart until the
            # cold-catalog deadline. Include the whole inventory in the model
            # input, but start after a short collection window if any charts
            # are usable. Missing graphs remain explicitly data_ready=false.
            partial_ready=progress['analysis_ready']>0 and now-job['started_at']>=12
            if not risk["locked"] and not self.ai.get(job["id"]) and now < job["deadline"] and not partial_ready and (not progress["total"] or progress["analysis_ready"] < progress["total"]):
                missing_quotes = progress["history_ready"] > 0 and not progress["quoted"]
                return {"status": "loading", "phase": "quotes" if missing_quotes else "history", "scan_id": job["id"], "deadline": job["deadline"], "progress": progress,
                        "reason": "История уже поступает, но свежих цен пока нет. Расширение повторяет подписки и запрашивает последние тики. Оставь Pocket Option и сервер открытыми." if missing_quotes else "Расширение автоматически запрашивает историю и котировки доступных пар. Для анализа нужны 80 закрытых свечей на двух таймфреймах. Оставь Pocket Option и сервер открытыми."}
            if risk["locked"]:
                result = {"status": "skipped", "reason": risk["reason"]}
            elif progress["analysis_ready"] or self.ai.get(job["id"]):
                from ai_market import AIError, snapshot
                try:
                    future = self.ai.get(job["id"])
                    if future is None:
                        if job["status"] == "ai":
                            raise AIError("Сервер перезапущен во время ответа ИИ. Запроси новый анализ.")
                        if not self.ai.status(uid)["configured"]:
                            raise AIError("API-доступ изменился. Проверь SETUP-AI.cmd на компьютере.")
                        value, _ = snapshot(assets, histories, prefs, now)
                        future = self.ai.submit(job["id"], value)
                        # Closed-graph research does not need fresh ticks for
                        # every candidate simultaneously. Reserve focused tick
                        # recovery for the actual choice, keeping history moving.
                        db.execute("UPDATE scan_jobs SET status='ai',watch_symbols='[]' WHERE id=?", (job["id"],))
                    if not future.done():
                        return {"status": "loading", "phase": "ai", "scan_id": job["id"], "deadline": job["deadline"], "progress": progress,
                                "reason": f"{self.ai.status(uid)['provider']} сравнивает Smart Money, откаты, ретесты, отскоки и ложные пробои с учётом режима рынка. Решение появится здесь автоматически; повторно нажимать кнопку не нужно."}
                    reviewed = future.result()
                    decision, original = reviewed["decision"], reviewed["snapshot"]
                    info = {k: decision[k] for k in ("provider", "model", "elapsed_ms", "reviewed_pairs", "batches")}
                    info.update(summary=decision["summary"], risk=decision["risk"], evidence=decision["evidence"], as_of=original["as_of"],
                                ready_pairs=sum(p.get("data_ready", False) for p in original["pairs"]),
                                analyzed_charts=sum(p.get('analysis_ready',p.get('data_ready',False)) for p in original['pairs']),
                                qualified_setups=len(original["qualified"]))
                    result = {"status": "skipped", "reason": decision["summary"], "ai": info}
                    if decision["action"] == "enter":
                        _, fresh = snapshot(assets, histories, prefs, now)
                        selected = next((c for c in fresh if c['entry_ready'] and (c["symbol"], c["direction"], c["strategy"]) == (decision["symbol"], decision["direction"], decision["strategy"])), None)
                        prior = next((q for q in original["qualified"] if (q["symbol"], q["direction"], q["strategy"]) == (decision["symbol"], decision["direction"], decision["strategy"])), None)
                        live_window=selected and (selected['strategy']=='smart_money' and selected['zones'] or now<selected['bar_time']+2*selected['timeframes'][0])
                        if selected and prior and live_window and same_setup(selected,prior) and prior["entry_low"] <= selected["price"] <= prior["entry_high"] and now - original["as_of"] <= 180:
                            result = {"status": "candidate", **selected, "expiry": prefs["expiry"], "entry_at": now, "valid_until": now + 30, "expires_at": now + prefs["expiry"] * 60,
                                      "validated": False, "min_payout": prefs["min_payout"] / 100, "probability": None, "ai": info,
                                      "reason": decision["summary"], "evidence": [*["ИИ: " + e["explanation"] for e in decision["evidence"]], *selected["evidence"]]}
                            result['entry_low']=max(selected['entry_low'],prior['entry_low'])
                            result['entry_high']=min(selected['entry_high'],prior['entry_high'])
                            if selected['strategy']!='smart_money' or not selected['zones']:
                                result['valid_until']=min(result['valid_until'],selected['bar_time']+2*selected['timeframes'][0])
                        else:
                            asset=next((a for a in assets if a['symbol']==decision['symbol']),None)
                            needs_quote=prior and asset and selection(asset,prefs) and catalog_observed(asset,now) and not catalog_current(asset,now) and now-original['as_of']<=180
                            if needs_quote:
                                until=job['quote_until'] if job['quote_until'] is not None else now+12
                                db.execute("UPDATE scan_jobs SET quote_until=?,watch_symbols=? WHERE id=?",(until,json.dumps([decision['symbol']]),job['id']))
                                if now<until:
                                    return {'status':'loading','phase':'entry_quote','scan_id':job['id'],'deadline':until,'progress':progress,
                                            'reason':f"ИИ выбрал сценарий {decision['symbol']}. Запрашиваем текущую цену этой пары и проверяем зону. Направление входа появится только после проверки; повторный запрос ИИ не отправляется."}
                                result.update(waiting_data=True,reason='Не получена свежая цена выбранной пары. Вход не показан; история свечей не используется вместо живой котировки.')
                            else:
                                result["reason"] = "Пока ИИ анализировал данные, цена или условия сценария изменились. Старый вход пропущен; запроси новый анализ."
                except AIError as error:
                    result = {"status": "ai_unavailable", "reason": str(error), 'ai_error_code':error.code}
                except Exception:
                    result = {"status": "ai_unavailable", "reason": "Ответ ИИ не прошёл проверку. Вход не предложен; повтори запрос позже."}
            else:
                result = {"status": "skipped", "waiting_data": True, "reason": "История или свежие котировки ещё не получены."}
            result.update({"scan_id": job["id"], "progress": progress, "checked_pairs": progress["checked"], "total_pairs": progress["total"]})
            if result["status"] == "skipped" and not risk["locked"] and not progress["analysis_ready"]:
                result["waiting_data"] = True
                result["reason"] = "Не удалось получить полный набор свежих данных. Проверь, что обновлённое расширение включено, Pocket Option открыт и вход выполнен. В списке пар показано, каких данных не хватает."
            cursor = db.execute("INSERT INTO analysis(user_id,created_at,result) VALUES (?,?,?)", (uid, now, json.dumps(result)))
            result["analysis_id"] = cursor.lastrowid
            db.execute("UPDATE analysis SET result=? WHERE id=?", (json.dumps(result), cursor.lastrowid))
            db.execute("UPDATE scan_jobs SET status='done',result_id=? WHERE id=?", (cursor.lastrowid, job["id"]))
            db.execute("DELETE FROM analysis WHERE user_id=? AND id NOT IN (SELECT id FROM analysis WHERE user_id=? ORDER BY id DESC LIMIT 100)", (uid, uid))
            return result
