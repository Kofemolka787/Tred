"""Actual signed API, actual SMC engine, and two browser UIs on synthetic candles.

No Pocket login, native MV3 load, real orders or production Telegram secrets.
"""
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from playwright.sync_api import expect,sync_playwright

from backend import handler
from test_market_analysis import fixture
from test_support import signed_data,FixtureAI
from trading_core import Ledger

ROOT=Path(__file__).resolve().parent
with tempfile.TemporaryDirectory(prefix="tred-signal-ui-") as temporary:
    current=[float(int(time.time()//900)*900)]
    ledger=Ledger(Path(temporary)/"ledger.sqlite3",clock=lambda:current[0],ai=FixtureAI())
    token=ledger.claim(ledger.pair(101)["code"])["token"]
    asset={"type":"asset","symbol":"EURUSD_otc","market":"otc","is_open":True,"payout":.92,"expiries":[5,10,15],"time":current[0]}
    ledger.ingest(token,{"events":[asset,{"type":"quote","symbol":"EURUSD_otc","price":106.30,"time":current[0]},{"type":"account","account_id":"456","mode":"real","currency":"USD","balance":"94.00","time":current[0]}]})
    key=Ed25519PrivateKey.generate();requests=[]
    Parent=handler(ledger,public_key=key.public_key().public_bytes_raw())
    class FixtureHandler(Parent):
        def do_GET(self):
            if self.path=="/app-config.json":return self.reply(200,{"mode":"api","version":3})
            return super().do_GET()
        def do_POST(self):
            requests.append(self.path)
            return super().do_POST()
    server=ThreadingHTTPServer(("127.0.0.1",0),FixtureHandler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    base=f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path="/usr/bin/chromium",args=["--no-sandbox","--disable-dev-shm-usage"])
            errors=[]
            def page_for(uid,width):
                context=browser.new_context(viewport={"width":width,"height":844})
                signature=signed_data(key,user_id=uid)
                script="window.Telegram={WebApp:{initData:"+json.dumps(signature)+",initDataUnsafe:{user:{id:"+str(uid)+"}},ready(){},expand(){}}};"
                context.route("https://telegram.org/js/telegram-web-app.js",lambda route:route.fulfill(content_type="application/javascript",body=script))
                page=context.new_page();page.on("pageerror",lambda e:errors.append(str(e)));page.goto(base)
                return page
            desktop=page_for(101,1000)
            desktop.locator('[data-market="otc"]').click()
            expect(desktop.locator("#feed-coverage")).to_contain_text("готовы к анализу: 0")
            desktop.locator("#request-signal").click()
            expect(desktop.locator("#signal-result")).to_contain_text("нужны 80 закрытых свечей")
            assert desktop.locator("#watch-entry").count()==0
            desktop.locator("#cancel-scan").click()
            expect(desktop.locator("#signal-result")).to_contain_text("Проверка остановлена")
            bars=[{"type":"candle","symbol":"EURUSD_otc","period":period,**row} for period in (60,300,900) for row in fixture(period,int(current[0]))]
            for i in range(0,len(bars),200):ledger.ingest(token,{"events":bars[i:i+200]})
            current[0]+=3
            ledger.ingest(token,{"events":[{**asset,"time":current[0]},{"type":"quote","symbol":"EURUSD_otc","price":106.30,"time":current[0]}]})
            expect(desktop.locator("#feed-coverage")).to_contain_text("готовы к анализу: 1",timeout=7000)
            desktop.locator("#request-signal").click()
            expect(desktop.locator("#signal-result h2")).to_contain_text("ВВЕРХ")
            expect(desktop.locator("#signal-result")).to_contain_text("выплата 92%")
            expect(desktop.locator("#signal-result svg")).to_be_visible()
            desktop.locator("#signal-result summary").click()
            expect(desktop.locator("#signal-result details")).to_contain_text("order block")
            desktop.screenshot(path=str(ROOT/"artifacts/signal-analysis-desktop.png"))
            desktop.locator("#watch-price").fill("106.30")
            desktop.locator("#watch-entry").click()
            expect(desktop.locator("#live-sessions")).to_contain_text("До завершения")
            phone=page_for(101,390)
            expect(phone.locator("#live-sessions")).to_contain_text("EURUSD_otc")
            for price in (106.34,106.22):
                current[0]+=5
                ledger.ingest(token,{"events":[{"type":"quote","symbol":"EURUSD_otc","price":price,"time":current[0]}]})
                for page in (desktop,phone):expect(page.locator("#live-sessions")).to_contain_text(f"Цена {price}",timeout=7000)
            phone.locator("#live-sessions").scroll_into_view_if_needed()
            phone.screenshot(path=str(ROOT/"artifacts/signal-live-mobile.png"))
            assert ledger.state({"id":101})["accounts"][0]["balance"]=="94.00"
            current[0]+=20
            expect(phone.locator("#live-sessions")).to_contain_text("Котировки не поступают",timeout=7000)
            assert phone.locator("[data-result]").count()==0
            session=ledger.state({"id":101})["signal_sessions"][0]
            current[0]=session["expires_at"]+1
            expect(phone.locator("#live-sessions")).to_contain_text("Время закончилось",timeout=7000)
            phone.locator('[data-result="win"]').click()
            phone.locator('[data-tab="trades"]').click()
            expect(phone.locator("#signal-trade-list")).to_contain_text("Плюс")
            expect(phone.locator("#signal-trade-list")).to_contain_text("результат указан тобой")
            phone.reload();phone.locator('[data-tab="trades"]').click()
            expect(phone.locator("#signal-trade-list")).to_contain_text("Плюс")
            other=page_for(202,320)
            assert other.locator(".session-card").count()==0
            assert other.locator("#signal-trade-list .trade-entry").count()==0
            assert ledger.state({"id":101})["trades"]==[]
            assert not any(path in requests for path in ("/api/order","/api/trade"))
            for page in (desktop,phone,other):assert not page.evaluate("document.documentElement.scrollWidth>innerWidth")
            assert not errors,errors
            phone.screenshot(path=str(ROOT/"artifacts/signal-journal-mobile.png"))
            result={"ok":True,"synthetic_candles_actual_smc_engine":True,"actual_signed_backend":True,"two_devices_same_user_share_tracking":True,"no_history_no_signal":True,"payout_92_and_long_short_explanations":True,"price_changes_without_reload":True,"stale_feed_is_visible":True,"no_result_before_expiry":True,"manual_result_persists_and_is_labelled":True,"other_user_isolated":True,"balance_and_broker_statistics_unchanged":True,"no_order_requests":True,"page_errors":errors,"owner_live_signal_validation":False}
            (ROOT/"artifacts/signal-ui-check.json").write_text(json.dumps(result,indent=2)+"\n")
            print(json.dumps(result))
            browser.close()
    finally:
        server.shutdown();server.server_close()
