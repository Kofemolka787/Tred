"""Actual popup HTML/JS in Chromium with isolated Chrome API fixtures.

Native unpacked extension loading is blocked by this machine's browser policy;
check_connector_mv3.py retains that separate attempted check, without a bypass.
"""
from functools import partial
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import threading

from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parent
class Quiet(SimpleHTTPRequestHandler):
    def log_message(self,*_):pass

server=ThreadingHTTPServer(("127.0.0.1",0),partial(Quiet,directory=str(ROOT/"connector")))
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
try:
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(executable_path="/usr/bin/chromium",headless=True,args=["--no-sandbox","--disable-dev-shm-usage"])
        try:
            page=browser.new_page(viewport={"width":340,"height":820});errors=[]
            page.on("pageerror",lambda error:errors.append(str(error)))
            page.add_init_script("""
              window.fixture={worker_version:'0.3.2',base:'https://fixture-route.trycloudflare.com',paired:true,needsPairing:false,lastError:'Некорректное время данных.',diagnostic:'Котировка · time: опережает часы браузера примерно на 30 с. Событие не передано.',receivedKinds:['account']};
              window.reloaded=0;
              window.chrome={runtime:{sendMessage:async message=>{
                if(message.action==='status')return structuredClone(window.fixture);
                if(message.action==='disconnect'){window.fixture={worker_version:'0.3.2',paired:false,lastError:'Старая ошибка'};return {ok:true};}
                if(message.action==='pair')return {error:'Код связи неверен или истёк.'};
                throw Error('Unexpected action');
              },reload:()=>window.reloaded++}};
            """)
            page.goto(f"http://127.0.0.1:{server.server_port}/popup.html")
            page.locator("#connection").filter(has_text="Повторный код не нужен").wait_for()
            assert not page.locator("#form").is_visible()
            assert page.locator("#diagnostic").is_visible()
            assert '30 с' in page.locator("#diagnostic").inner_text()
            assert 'баланс' in page.locator("#received").inner_text()
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
            page.screenshot(path=str(ROOT/"artifacts/connector-popup-fixed.png"))
            page.locator("#change").click();assert page.locator("#form").is_visible()
            page.locator("#base").fill("https://new-fixture.trycloudflare.com")
            page.wait_for_timeout(2200)
            assert page.locator("#base").input_value()=="https://new-fixture.trycloudflare.com"
            page.locator("#code").fill('a'*24)
            page.locator("#form button").click()
            page.locator("#pair-error").filter(has_text="Скопируй текущий код").wait_for()
            assert 'сохранена' in page.locator("#connection").inner_text()
            page.locator("#disconnect").click()
            page.locator("#connection").filter(has_text="ещё не связан").wait_for()
            page.locator("#base").fill("https://typed-fixture.trycloudflare.com")
            page.wait_for_timeout(2200)
            assert page.locator("#base").input_value()=="https://typed-fixture.trycloudflare.com"
            assert 'Старая ошибка' not in page.locator('#status').inner_text()
            page.evaluate("window.fixture={base:'https://old-fixture.trycloudflare.com',lastError:'Некорректное время данных.'}")
            page.locator('#reload-module').wait_for()
            assert not page.locator('#form').is_visible()
            assert 'Модуль ещё не связан' not in page.locator('#connection').inner_text()
            page.locator('#reload-module').click();assert page.evaluate('window.reloaded')==1
            assert not errors,errors
            result={"actual_html_js_fixture_ui":True,"paired_form_hidden":True,"time_diagnostic_visible":True,"status_poll_preserves_typed_address":True,"failed_claim_keeps_connection_status":True,"legacy_worker_detected":True,"stale_data_error_hidden_when_unpaired":True,"page_errors":len(errors),"native_mv3_update_verified":False,"native_mv3_blocker":"Managed Chromium administrator policy disables unpacked extension loading"}
            (ROOT/"artifacts/connector-popup-check.json").write_text(json.dumps(result,indent=2)+'\n')
            print(json.dumps(result))
        finally:browser.close()
finally:server.shutdown();server.server_close();thread.join(timeout=5)
