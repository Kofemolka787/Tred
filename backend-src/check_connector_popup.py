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
              window.fixture={worker_version:'0.3.15',base:'https://fixture-route.trycloudflare.com',paired:true,needsPairing:false,lastError:'',diagnostic:'Котировка · time: опережает часы браузера примерно на 7200 с. Событие не передано.',receivedKinds:['asset'],marketClock:'utc'};
              window.reloaded=0;
              window.fixtureTabId=1;window.idConfirmations=0;
              window.chrome={tabs:{query:async()=>[{id:window.fixtureTabId}]},runtime:{sendMessage:async message=>{
                if(message.action==='status')return structuredClone(window.fixture);
                if(message.action==='disconnect'){window.fixture={worker_version:'0.3.15',paired:false,lastError:'Старая ошибка'};return {ok:true};}
                if(message.action==='market-clock'){window.fixture.marketClock=message.profile;window.fixture.diagnostic='';return {ok:true};}
                if(message.action==='confirm-usd'){if(message.account_id!==window.fixture.accountCandidate.account_id||message.mode!==window.fixture.accountCandidate.mode)throw Error('Wrong account');window.fixture.usdConfirmed=true;return {ok:true};}
                if(message.action==='forget-account-id'){window.fixture.accountCandidate=null;return {ok:true};}
                if(message.action==='forget-usd'){window.fixture.usdConfirmed=false;return {ok:true};}
                if(message.action==='confirm-account-id'){
                  if(message.account_id!=='456'||message.mode!=='demo'||message.active_tab_id!==1||!message.confirm_usd)throw Error('Wrong manual identity');
                  window.idConfirmations++;
                  window.fixture.accountCandidate={account_id:message.account_id,mode:message.mode,reported_currency:null,identity_source:'manual',source_stream:message.source_stream};
                  window.fixture.usdConfirmed=true;window.fixture.identityCandidate=null;window.fixture.lastSeen=Date.now();window.fixture.receivedKinds=['asset','quote','account'];return {ok:true};
                }
                if(message.action==='pair')return {error:'Код связи неверен или истёк.'};
                throw Error('Unexpected action');
              },reload:()=>window.reloaded++}};
            """)
            page.goto(f"http://127.0.0.1:{server.server_port}/popup.html")
            page.locator("#connection").filter(has_text="Повторный код не нужен").wait_for()
            assert not page.locator("#form").is_visible()
            assert page.locator("#diagnostic").is_visible()
            assert '7200 с' in page.locator("#diagnostic").inner_text()
            assert 'каталог пар' in page.locator("#received").inner_text()
            page.evaluate("window.fixture.scannerStatus={code:'ready',pairs:21,updated_at:Date.now()}")
            page.locator('#market-delivery').filter(has_text='заданий для пар 21').wait_for()
            assert 'не число готовых графиков' in page.locator('#market-delivery').inner_text()
            page.evaluate("window.fixture.scannerStatus.updated_at=Date.now()-21000")
            page.locator('#market-delivery').filter(has_text='давно не отвечал').wait_for()
            page.evaluate("window.fixture.scannerStatus={code:'no_socket',pairs:0,updated_at:Date.now()}")
            page.locator('#market-delivery').filter(has_text='канал пока не найден').wait_for()
            assert 'без пересчёта' in page.locator('#clock-status').inner_text()
            page.locator('#clock-toggle').click()
            page.locator('#clock-status').filter(has_text='UTC+2 → UTC').wait_for()
            assert not page.locator('#diagnostic').is_visible()
            assert not page.locator('#form').is_visible()
            page.wait_for_timeout(2200)
            assert page.evaluate('window.fixture.marketClock')=='po-utc2'
            assert page.locator('#clock-toggle').inner_text()=='Вернуть формат UTC'
            page.locator('#clock-toggle').click()
            page.locator('#clock-status').filter(has_text='без пересчёта').wait_for()
            assert not page.locator('#account-currency').is_visible()
            page.evaluate("window.fixture.accountCandidate={account_id:'456',mode:'demo',reported_currency:null,seen_at:Date.now()/1000}")
            page.locator('#account-currency').wait_for()
            assert 'Pocket ID 456' in page.locator('#account-identity').inner_text()
            assert 'демосчёт' in page.locator('#account-identity').inner_text()
            page.locator('#confirm-usd').click()
            page.locator('#currency-status').filter(has_text='USD подтверждён тобой').wait_for()
            assert not page.locator('#form').is_visible()
            assert not page.locator('#confirm-usd').is_visible()
            assert page.locator('#forget-usd').is_visible()
            assert 'автоматически' in page.locator('#currency-error').inner_text()
            page.locator('#forget-usd').click()
            page.locator('#confirm-usd').wait_for()
            page.evaluate("window.fixture.accountCandidate=null;window.fixture.identityCandidate={source_stream:'11111111-1111-4111-8111-111111111111',mode:'demo',reported_currency:null,tab_id:1,id_status:'unsupported'};window.fixture.diagnostic='Баланс получен. Подтверди Pocket ID из своего профиля ниже. Поля ID: uid — текст; id — логическое значение.';window.fixture.lastManualAccountId=''")
            page.locator('#manual-identity').wait_for()
            assert 'Поля ID: uid — текст' in page.locator('#diagnostic').inner_text()
            page.locator('#pocket-id').fill('456')
            page.wait_for_timeout(2200)
            assert page.locator('#pocket-id').input_value()=='456','Polling must preserve typed ID'
            page.locator('#identity-form button').click()
            assert page.evaluate('window.idConfirmations')==0,'Currency checkbox requires explicit confirmation'
            page.locator('#identity-usd').check()
            page.screenshot(path=str(ROOT/'artifacts/connector-manual-id.png'),full_page=True)
            page.locator('#identity-form button').click()
            page.locator('#manual-id-status').wait_for()
            assert 'Pocket ID 456' in page.locator('#manual-id-status').inner_text()
            assert not page.locator('#manual-identity').is_visible()
            assert not page.locator('#form').is_visible()
            assert 'Повторное подтверждение' in page.locator('#manual-id-status').inner_text()
            assert '5 секунд' in page.locator('#auto-refresh-status').inner_text()
            assert page.locator('#forget-account-id').is_visible()
            assert 'баланс' in page.locator('#received').inner_text()
            page.evaluate("window.fixture.lastBalance={mode:'demo',received_at:Date.now(),origin:'terminal-dom'};window.fixture.terminalCode='ready'")
            page.locator('#balance-delivery').filter(has_text='Последний баланс принят').wait_for()
            assert 'распознан' in page.locator('#terminal-reader-status').inner_text()
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
            assert not page.locator('#market-time').is_visible()
            assert not page.locator('#account-currency').is_visible()
            page.evaluate("window.fixture={base:'https://old-fixture.trycloudflare.com',lastError:'Некорректное время данных.'}")
            page.locator('#reload-module').wait_for()
            assert not page.locator('#form').is_visible()
            assert not page.locator('#market-time').is_visible()
            assert not page.locator('#market-delivery').is_visible()
            assert 'Модуль ещё не связан' not in page.locator('#connection').inner_text()
            page.locator('#reload-module').click();assert page.evaluate('window.reloaded')==1
            assert not errors,errors
            result={"actual_html_js_fixture_ui":True,"paired_form_hidden":True,"time_diagnostic_visible":True,"explicit_clock_toggle_and_revert":True,"account_usd_confirmation_and_revoke":True,"pocket_account_identity_visible":True,"manual_id_form_with_explicit_usd":True,"unknown_id_shape_shown_without_raw_values":True,"poll_preserves_typed_id":True,"manual_id_provenance_visible":True,"saved_profile_and_auto_refresh_visible":True,"balance_acceptance_time_separate_from_quotes":True,"clock_selection_preserves_pairing_ui":True,"status_poll_preserves_typed_address":True,"failed_claim_keeps_connection_status":True,"legacy_worker_detected":True,"stale_data_error_hidden_when_unpaired":True,"page_errors":len(errors),"native_mv3_update_verified":False,"native_mv3_blocker":"Managed Chromium administrator policy disables unpacked extension loading"}
            (ROOT/"artifacts/connector-popup-check.json").write_text(json.dumps(result,indent=2)+'\n')
            print(json.dumps(result))
        finally:browser.close()
finally:server.shutdown();server.server_close();thread.join(timeout=5)
