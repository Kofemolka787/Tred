"""Portable TRED API. Serves only signed users and a read-only paired connector."""
import argparse
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlsplit

from telegram_auth import PUBLIC_KEY, verify
from trading_core import DomainError, Ledger

ROOT = Path(__file__).resolve().parent
FRONTEND_ORIGIN = "https://kofemolka787.github.io"
STATIC = {"/":("index.html","text/html; charset=utf-8"), "/app.js":("app.js","application/javascript"),
          "/data-client.js":("data-client.js","application/javascript"), "/signals.js":("signals.js","application/javascript"), "/styles.css":("styles.css","text/css"),
          "/app-config.json":("app-config.json","application/json")}
LOG = logging.getLogger("tred.backend")


def handler(ledger, *, public_key=PUBLIC_KEY):
    rates = defaultdict(deque)
    rate_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = "TRED"
        sys_version = ""

        def log_message(self, *_):
            pass  # Never log headers, auth data, URLs, balances, or pairing codes.

        def origin_allowed(self):
            origin = self.headers.get("Origin")
            if origin is None:
                return True  # Extension service workers and non-browser clients.
            parsed = urlsplit(origin)
            return origin == FRONTEND_ORIGIN or bool(re.fullmatch(r"chrome-extension://[a-p]{32}", origin)) or (parsed.scheme in ("http","https") and parsed.netloc == self.headers.get("Host") and parsed.path == "")

        def headers_common(self):
            origin = self.headers.get("Origin")
            if origin and self.origin_allowed():
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' https://telegram.org; style-src 'self'; connect-src 'self' https://*.trycloudflare.com; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors https://web.telegram.org https://*.telegram.org https://t.me")

        def reply(self, status, body, mime="application/json; charset=utf-8"):
            payload = body if isinstance(body, bytes) else json.dumps(body,ensure_ascii=False,allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(payload)))
            self.headers_common()
            self.end_headers()
            self.wfile.write(payload)

        def body(self):
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json" or self.headers.get("Transfer-Encoding"):
                raise DomainError("Нужен JSON без потокового тела.", 415)
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 262144:
                    raise ValueError()
                self.connection.settimeout(10)
                return json.loads(self.rfile.read(size), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            except (ValueError, UnicodeError, TimeoutError):
                raise DomainError("Некорректный или слишком большой JSON.") from None

        def limit(self, path):
            # Bounded map; do not trust arbitrary X-Forwarded-For headers.
            key = (self.client_address[0], "claim" if path == "/api/bridge/claim" else "api")
            now = time.monotonic()
            with rate_lock:
                if len(rates) > 2048:
                    for old in list(rates):
                        if not rates[old] or rates[old][-1] < now-60:
                            del rates[old]
                    if len(rates) > 2048:
                        raise DomainError("Слишком много подключений.", 429)
                queue = rates[key]
                while queue and queue[0] < now-60:
                    queue.popleft()
                if len(queue) >= (10 if key[1] == "claim" else 600):
                    raise DomainError("Слишком много запросов. Попробуй через минуту.", 429)
                queue.append(now)

        def dispatch(self, post=False):
            path = urlsplit(self.path).path
            if not self.origin_allowed():
                raise DomainError("Этот источник не разрешён.", 403)
            if not post and path == "/healthz":
                return self.reply(200, {"ok":True,"service":"tred-backend","api_version":1,"real_orders_supported":False})
            if not post and path in STATIC:
                name,mime = STATIC[path]
                return self.reply(200, (ROOT/"web"/name).read_bytes(), mime)
            if not path.startswith("/api/"):
                raise DomainError("Страница не найдена.",404)
            self.limit(path)
            if path == "/api/bridge/claim" and post:
                value = self.body()
                if not isinstance(value,dict) or set(value) != {"code"}:
                    raise DomainError("Укажи одноразовый код.")
                return self.reply(200,ledger.claim(value["code"]))
            if path in ("/api/bridge/events", "/api/bridge/scan") and post:
                auth = self.headers.get("Authorization", "")
                token = auth[7:] if auth.startswith("Bearer ") else ""
                return self.reply(200,ledger.ingest(token,self.body()) if path.endswith("/events") else ledger.bridge_plan(token,self.body()))
            try:
                user = verify(self.headers.get("X-Telegram-Init-Data", ""),public_key=public_key)
            except ValueError as error:
                raise DomainError(str(error),401) from None
            uid = user["id"]
            if path == "/api/state" and not post:
                return self.reply(200,ledger.state(user))
            if not post:
                raise DomainError("Действие не найдено.",404)
            value = self.body()
            if path == "/api/settings":
                ledger.settings(uid,value)
            elif path == "/api/risk":
                ledger.configure_risk(uid,value)
            elif path == "/api/paper":
                if not isinstance(value,dict) or set(value) != {"running"}:
                    raise DomainError("Проверь действие симуляции.")
                ledger.toggle(uid,value["running"])
            elif path == "/api/pair":
                return self.reply(200,ledger.pair(uid))
            elif path == "/api/unlink":
                ledger.unlink(uid)
            elif path == "/api/watch":
                ledger.watch(uid,value)
            elif path == "/api/result":
                ledger.record_result(uid,value)
            elif path == "/api/signal":
                result = ledger.signal(uid)
                return self.reply(200,{**result,"state":ledger.state(user)})
            elif path in ("/api/scan/start", "/api/scan/poll", "/api/scan/cancel"):
                if path.endswith("/start"):
                    if value != {}:
                        raise DomainError("Некорректный запрос анализа.")
                    result = ledger.start_scan(uid)
                else:
                    result = ledger.poll_scan(uid, value, cancel=path.endswith("/cancel"))
                return self.reply(200, {**result, "state": ledger.state(user)})
            else:
                raise DomainError("Действие не найдено.",404)
            self.reply(200,ledger.state(user))

        def do_OPTIONS(self):
            if not self.origin_allowed():
                return self.reply(403,{"message":"Источник не разрешён."})
            self.send_response(204)
            self.headers_common()
            self.send_header("Access-Control-Allow-Methods","GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers","Content-Type, X-Telegram-Init-Data, Authorization")
            self.send_header("Access-Control-Max-Age","300")
            self.send_header("Content-Length","0")
            self.end_headers()

        def handle_api_request(self, post=False):
            try:
                self.dispatch(post)
            except DomainError as error:
                self.reply(error.status,{"error":"request_rejected","message":str(error)})
            except (BrokenPipeError,ConnectionResetError):
                pass
            except Exception as error:
                LOG.error("Request failed: %s",type(error).__name__)
                self.reply(500,{"error":"server_error","message":"Сервер не смог обработать запрос."})

        def do_GET(self):
            self.handle_api_request()

        def do_POST(self):
            self.handle_api_request(True)

    return Handler


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("--port",type=int,default=8790)
    parser.add_argument("--database",type=Path,default=ROOT/"private"/"ledger.sqlite3")
    args = parser.parse_args()
    ledger = Ledger(args.database)
    # A restart always pauses the simulator; day locks and open exposure persist.
    with ledger.db() as db:
        db.execute("UPDATE risk SET running=0,reason='Пауза после перезапуска'")
    server = ThreadingHTTPServer(("127.0.0.1",args.port),handler(ledger))
    server.daemon_threads = True
    stopping = threading.Event()

    def simulation_loop():
        while not stopping.wait(2):
            with ledger.db() as db:
                ids = [r[0] for r in db.execute("SELECT user_id FROM risk WHERE running=1 OR user_id IN (SELECT user_id FROM paper_trades WHERE status='open')")]
            for uid in ids:
                try:
                    ledger.paper_step(uid)
                except Exception as error:
                    LOG.error("Simulation paused: %s",type(error).__name__)
                    with ledger.db() as db:
                        db.execute("UPDATE risk SET running=0,reason='Ошибка данных: симуляция остановлена' WHERE user_id=?",(uid,))

    worker = threading.Thread(target=simulation_loop,daemon=True)
    worker.start()
    LOG.info("TRED API listening on loopback port %d; real execution disabled",args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stopping.set()
        server.server_close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
    main()
