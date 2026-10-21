"""Signed Mini App -> real Responses client -> fixture SSE -> saved strategy.

Synthetic market/provider only; no external calls, owner keys or broker orders.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from playwright.sync_api import expect, sync_playwright

from ai_market import OpenAIAnalyzer, SCHEMA
from backend import handler
from configure_ai import save
from market_strategies import BY_ID, ACTIVE_STRATEGIES
from test_active_signals import active_market
from test_market_strategies import NOW, market
from test_support import FixtureAI, signed_data
from trading_core import Ledger

ROOT = Path(__file__).resolve().parent
(ROOT/"artifacts").mkdir(exist_ok=True)
reports = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(executable_path="/usr/bin/chromium", args=["--no-sandbox", "--disable-dev-shm-usage"])
    try:
        for family in ("ema_pullback", "range_rebound", "breakout_retest", "false_breakout", *(s["id"] for s in ACTIVE_STRATEGIES)):
            active=family in {s["id"] for s in ACTIVE_STRATEGIES}
            with tempfile.TemporaryDirectory(prefix="tred-strategy-ui-") as temporary:
                current, requests, errors = [float(NOW)], [], []
                secret = "lr_fixture_strategy_ui_not_real"
                class Model(BaseHTTPRequestHandler):
                    def log_message(self, *_): pass
                    def do_POST(self):
                        body=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                        assert self.path == "/v1/responses" and body["stream"] and body["store"] is False
                        payload=json.loads(body["input"][1]["content"]);requests.append(payload)
                        assert not any(k in json.dumps(payload) for k in ("account_id","api_key","balance","user_id","token"))
                        chosen={**payload,"qualified":[q for q in payload["qualified"] if q["strategy"] == family]}
                        assert chosen["qualified"]
                        d=FixtureAI().submit(1,chosen).result()["decision"]
                        text=json.dumps({k:d[k] for k in SCHEMA["required"]})
                        result={"status":"completed","output":[{"content":[{"type":"output_text","text":text}]}]}
                        reply=("data: "+json.dumps({"type":"response.completed","response":result})+"\n\n").encode()
                        self.send_response(200);self.send_header("Content-Type","text/event-stream");self.send_header("Content-Length",str(len(reply)));self.end_headers();self.wfile.write(reply)
                model=ThreadingHTTPServer(("127.0.0.1",0),Model)
                threading.Thread(target=model.serve_forever,daemon=True).start()
                save(temporary,secret,"deepseek-4.1-flash",allowed_users=[101],provider="lunaroute")
                engine=OpenAIAnalyzer(temporary,endpoint=f"http://127.0.0.1:{model.server_port}/v1/responses")
                ledger=Ledger(Path(temporary)/"ledger.sqlite3",clock=lambda:current[0],ai=engine)
                token=ledger.claim(ledger.pair(101)["code"])["token"]
                ledger.settings(101,{"market":"otc","allow_otc":True})
                asset,histories,_=(active_market if active else market)(family)
                events=[{"type":"asset","symbol":"PAIR_otc","market":"otc","is_open":True,"payout":.92,"expiries":[5,10,15],"time":NOW},
                        {"type":"quote","symbol":"PAIR_otc","price":asset["price"],"time":NOW},
                        {"type":"account","account_id":"456","mode":"demo","currency":"USD","balance":"105.20","time":NOW}]
                events += [{"type":"candle","symbol":symbol,"period":period,**row} for (symbol,period),rows in histories.items() for row in rows]
                for i in range(0,len(events),200):ledger.ingest(token,{"events":events[i:i+200]})
                key=Ed25519PrivateKey.generate()
                Parent=handler(ledger,public_key=key.public_key().public_bytes_raw())
                class App(Parent):
                    def do_GET(self):
                        if self.path=="/app-config.json":return self.reply(200,{"mode":"api","version":3})
                        return super().do_GET()
                server=ThreadingHTTPServer(("127.0.0.1",0),App)
                threading.Thread(target=server.serve_forever,daemon=True).start()
                context=browser.new_context(viewport={"width":390,"height":844})
                try:
                    script="window.Telegram={WebApp:{initData:"+json.dumps(signed_data(key))+",initDataUnsafe:{user:{id:101}},ready(){},expand(){}}};"
                    context.route("https://telegram.org/js/telegram-web-app.js",lambda route:route.fulfill(content_type="application/javascript",body=script))
                    page=context.new_page();page.on("pageerror",lambda error:errors.append(str(error)));page.goto(f"http://127.0.0.1:{server.server_port}")
                    expect(page.locator("#feed-coverage")).to_contain_text("готовы к анализу: 1")
                    if active:
                        page.locator('#signal-mode').select_option('active')
                        expect(page.locator('#signal-mode-help')).to_contain_text('Риск выше')
                        assert ledger.state({'id':101})['preferences']['signal_mode']=='active'
                    expect(page.locator("#strategy-help")).to_be_visible()
                    page.locator("#strategy-help summary").click()
                    assert page.locator("#strategy-list > p").count()==(8 if active else 5)
                    page.locator("#strategy-help summary").click()
                    page.locator("#request-signal").click()
                    expect(page.locator("#signal-result h2")).to_contain_text("PAIR_otc",timeout=15000)
                    expect(page.locator("#signal-result")).to_contain_text(BY_ID[family]["name"])
                    if active:expect(page.locator('#signal-result')).to_contain_text('Чаще · повышенный риск')
                    page.locator("#signal-result summary").click()
                    expect(page.locator("#signal-result details")).to_contain_text("Слабость стратегии:")
                    expect(page.locator("#signal-result details")).to_contain_text("M1 + M5")
                    if family=="ema_pullback":page.screenshot(path=str(ROOT/"artifacts/strategy-mobile.png"),full_page=True)
                    page.locator("#watch-entry").click()
                    expect(page.locator("#live-sessions")).to_contain_text("До завершения")
                    assert ledger.state({"id":101})["signal_sessions"][0]["setup"]["strategy"]==family
                    current[0]+=301;page.reload()
                    page.locator('#live-sessions [data-result="win"]').click()
                    expect(page.locator("#live-sessions > article")).to_have_count(0)
                    state=ledger.state({"id":101})
                    assert state["signal_sessions"][0]["result"]=="win"
                    assert state["signal_sessions"][0]["setup"]["strategy"]==family
                    assert state['signal_sessions'][0]['setup']['signal_mode']==('active' if active else 'strict')
                    assert state["accounts"][0]["balance"]=="105.20" and not state["trades"]
                    assert ledger.state({"id":202})["signal_sessions"]==[]
                    assert len(requests)==1 and not errors, errors
                    assert secret not in page.content()
                    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                    reports.append({"strategy":family,"signal_mode":"active" if active else "strict","ai_choice_visible":True,"explanation_and_weakness_visible":True,"strategy_persisted_with_user_result":True,"risk_label_visible":active,"balance_unchanged":True})
                finally:
                    context.close();server.shutdown();server.server_close();model.shutdown();model.server_close();engine.executor.shutdown()
    finally:browser.close()
report={"ok":True,"families":reports,"real_responses_client_with_fixture_sse":True,"page_errors":[],"native_provider_and_profitability_verified":False}
(ROOT/"artifacts/strategy-ui-check.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report))
