"""Exercise actual app handlers with delayed API fixtures and changing URLs."""
from functools import partial
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading

from playwright.sync_api import expect,sync_playwright
from trading_core import Ledger

ROOT=Path(__file__).resolve().parent
class Quiet(SimpleHTTPRequestHandler):
    def log_message(self,*_):pass

with tempfile.TemporaryDirectory(prefix="tred-pairing-ui-") as temporary:
    state=Ledger(Path(temporary)/'db.sqlite3').state({'id':101,'first_name':'Тест'})
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(Quiet,directory=str(ROOT/'web')))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path='/usr/bin/chromium',args=['--no-sandbox'])
            try:
                page=browser.new_page(viewport={'width':390,'height':844});errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.route('https://telegram.org/js/telegram-web-app.js',lambda route:route.fulfill(content_type='application/javascript',body='window.Telegram={WebApp:{initData:"fixture-only",initDataUnsafe:{user:{id:101}},ready(){},expand(){}}};'))
                page.goto(f'http://127.0.0.1:{server.server_port}/')
                expect(page.locator('#request-signal')).to_be_enabled()
                page.evaluate('s=>window.fixtureState=s',state)
                page.evaluate("""()=>{
                    window.oldCalls=0;window.newCalls=0;window.commits=[];
                    window.oldClient={mode:'api',base:'https://old-fixture.trycloudflare.com',request:async path=>{
                        if(path==='/api/pair'){window.oldCalls++;return new Promise(resolve=>window.oldReply=resolve);}
                        return structuredClone(window.fixtureState);
                    }};
                    window.newClient={mode:'api',base:'https://new-fixture.trycloudflare.com',request:async path=>{
                        if(path==='/api/pair'){window.newCalls++;return {code:String(window.newCalls).repeat(24),expires_in:300};}
                        return structuredClone(window.fixtureState);
                    }};
                    window.TredData.connect=async(tg,url)=>{
                        if(url.includes('failed'))throw Error('fixture connection failed');
                        return {client:window.newClient,state:structuredClone(window.fixtureState),commit:()=>window.commits.push(url)};
                    };
                    current=structuredClone(window.fixtureState);dataClient=window.oldClient;authorized=true;render();setBusy(false);
                }""")
                page.locator('[data-tab="profile"]').click()
                page.evaluate("current.connection.authenticated=true;current.connection.live=true;render()")
                expect(page.locator('#account-status')).to_have_text('Котировки поступают')
                expect(page.locator('#account-list')).to_contain_text('Баланс и ID счёта ещё не получены')
                expect(page.locator('#account-list strong')).to_have_text('—')
                page.evaluate("current.accounts=[{id:'456',mode:'real',balance:'120.25',updated_at:Date.now()/1000,live:true}];render()")
                expect(page.locator('#account-status')).to_have_text('Баланс обновлён')
                expect(page.locator('#account-list')).to_contain_text('Реальный счёт · USD')
                expect(page.locator('#account-list')).to_contain_text('ID 456')
                expect(page.locator('#account-list strong')).to_have_text('$120.25')
                page.evaluate('current.stale=true;render()')
                expect(page.locator('#account-status')).to_have_text('Сервер недоступен')
                expect(page.locator('#account-list strong')).to_have_text('$120.25')
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                # Exercise the app's existing two-second poll, without rendering
                # a new balance manually or reloading the page.
                page.evaluate("window.fixtureState.connection.authenticated=true;window.fixtureState.connection.live=true;window.fixtureState.accounts=[{id:'456',mode:'demo',balance:'5.00',updated_at:Date.now()/1000,live:true}]")
                expect(page.locator('#account-list strong')).to_have_text('$5.00',timeout=6000)
                page.evaluate("window.fixtureState.accounts[0].balance='100.00';window.fixtureState.accounts[0].updated_at=Date.now()/1000")
                expect(page.locator('#account-list strong')).to_have_text('$100.00',timeout=6000)
                page.evaluate("window.fixtureState.accounts[0].balance='108.00';window.fixtureState.accounts[0].updated_at=Date.now()/1000")
                expect(page.locator('#account-list strong')).to_have_text('$108.00',timeout=6000)
                page.evaluate("window.fixtureState.accounts=[]")
                page.evaluate('current=structuredClone(window.fixtureState);render()')
                page.locator('#connect-backend').click()
                page.wait_for_function('window.oldCalls===1')
                page.locator('#backend-url').fill('https://new-fixture.trycloudflare.com')
                page.locator('#backend-form button').click()
                assert not page.locator('#pairing-box').is_visible()
                page.evaluate("window.oldReply({code:'a'.repeat(24),expires_in:300})")
                expect(page.locator('#pair-code')).to_have_text('1'*24)
                expect(page.locator('#pair-server')).to_contain_text('new-fixture.trycloudflare.com')
                assert page.evaluate('window.commits.length')==1
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                page.locator('[data-close="connect-dialog"]').click();page.locator('#connect-backend').click()
                expect(page.locator('#pair-code')).to_have_text('1'*24)
                assert page.evaluate('window.newCalls')==1,'Reopening must not silently rotate a valid code'
                page.locator('#renew-code').click()
                expect(page.locator('#pair-code')).to_have_text('2'*24)
                page.locator('#backend-url').fill('https://failed-fixture.trycloudflare.com')
                assert not page.locator('#pairing-box').is_visible()
                page.locator('#backend-form button').click()
                expect(page.locator('#connect-status')).to_have_text('fixture connection failed')
                assert not page.locator('#pairing-box').is_visible()
                assert page.locator('#pair-code').inner_text()==''
                assert page.locator('#copy-code').is_disabled()
                assert not errors,errors
                result={'balance_5_to_100_to_108_via_poll_without_reload':True,'live_quotes_without_balance_not_disconnected':True,'account_balance_and_pocket_id_rendered':True,'offline_last_balance_not_claimed_fresh':True,'old_pair_reply_cannot_overwrite_new_code':True,'pending_pair_requests_serialized':True,'code_displays_exact_server':True,'reopen_reuses_valid_code':True,'explicit_renew_rotates_code':True,'failed_new_connection_clears_old_code':True,'no_mobile_overflow':True,'page_errors':len(errors)}
                (ROOT/'artifacts/pairing-ui-check.json').write_text(json.dumps(result,indent=2)+'\n')
                print(json.dumps(result))
            finally:browser.close()
    finally:server.shutdown();server.server_close();thread.join(timeout=5)
