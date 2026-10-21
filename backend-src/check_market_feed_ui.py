"""Native-shaped Fast frames -> actual observer/worker -> signed API -> UI.

Synthetic protocol and price data; no broker session, trades or native MV3 load.
"""
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from playwright.sync_api import expect,sync_playwright
from backend import handler
from trading_core import Ledger
from test_support import signed_data,FixtureAI
from test_market_analysis import fixture

ROOT=Path(__file__).resolve().parent
with tempfile.TemporaryDirectory(prefix='tred-feed-ui-') as temporary:
    current=[1800000000.0]
    ledger=Ledger(Path(temporary)/'db.sqlite3',clock=lambda:current[0],ai=FixtureAI())
    token=ledger.claim(ledger.pair(101)['code'])['token']
    ledger.settings(101,{'market':'otc','allow_otc':True,'selection':'manual','symbol':'PAIR0_otc'})
    key=Ed25519PrivateKey.generate()
    Parent=handler(ledger,public_key=key.public_key().public_bytes_raw())
    class FixtureHandler(Parent):
        def do_GET(self):
            if self.path=='/app-config.json':return self.reply(200,{'mode':'api','version':3})
            return super().do_GET()
    server=ThreadingHTTPServer(('127.0.0.1',0),FixtureHandler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    base=f'http://127.0.0.1:{server.server_port}'
    child=subprocess.Popen(['node','check_market_pipeline.js',base,token,str(current[0])],cwd=ROOT,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
    def command(value):
        child.stdin.write(json.dumps(value)+'\n');child.stdin.flush()
        reply=json.loads(child.stdout.readline())
        assert reply.get('ok'),reply
        assert not reply['lastError'],reply
        return reply
    def frame(name,value):return command({'action':'frame','frame':'42'+json.dumps([name,value])})
    def quotes():return frame('updateStream',[[f'PAIR{i}_otc',current[0]+7200,106.30] for i in range(16)])
    def asset_rows():
        result=[]
        for i in range(16):
            row=[None]*16;row[0]=i;row[1]=f'PAIR{i}_otc';row[3]='currency';row[5]=92;row[9]=1;row[14]=True;row[15]=[{'time':300},{'time':600},{'time':900}];result.append(row)
        return result
    try:
        assert json.loads(child.stdout.readline())=={'ready':True}
        frame('successauth',{'uid':456,'isDemo':0,'balance':94,'currency':'USD'})
        frame('updateAssets',asset_rows());quotes()
        current[0]+=120;command({'action':'advance','seconds':120});quotes()
        state=ledger.state({'id':101})
        assert state['feed']['eligible']==16 and state['feed']['quoted']==16
        assert state['feed']['ready']==0
        assert state['feed']['blocked']['history']==16
        assert all(p['bars']==[0,0] for p in state['feed']['pairs'])
        with ledger.db() as db:assert db.execute('SELECT min(updated_at),max(updated_at) FROM assets').fetchone()[0]==1800000000
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path='/usr/bin/chromium',args=['--no-sandbox','--disable-dev-shm-usage'])
            try:
                ctx=browser.new_context(viewport={'width':390,'height':844})
                script='window.Telegram={WebApp:{initData:'+json.dumps(signed_data(key))+',initDataUnsafe:{user:{id:101}},ready(){},expand(){}}};'
                ctx.route('https://telegram.org/js/telegram-web-app.js',lambda route:route.fulfill(content_type='application/javascript',body=script))
                page=ctx.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)));page.goto(base)
                expect(page.locator('#feed-coverage')).to_contain_text('готовы к анализу: 0')
                page.locator('#request-signal').click()
                expect(page.locator('#signal-result')).to_contain_text('80')
                assert page.locator('#watch-entry').count()==0
                page.locator('#cancel-scan').click()
                expect(page.locator('#signal-result')).to_contain_text('Проверка остановлена')
                # Actual Fast binary name, actual attachment, no canonical candles
                # injected directly into the worker or database.
                for period in (60,300):
                    rows=fixture(period,int(current[0]//period)*period)
                    for row in rows:row['time']+=7200
                    command({'action':'frame','frame':'451-["loadHistoryPeriodFast",{"_placeholder":true,"num":0}]'})
                    command({'action':'frame','frame':json.dumps({'asset':'PAIR0_otc','period':period,'data':rows})})
                expect(page.locator('#feed-coverage')).to_contain_text('готовы к анализу: 1',timeout=7000)
                expect(page.locator('#coverage-list')).to_contain_text('loadHistoryPeriodFast')
                expect(page.locator('#coverage-list')).to_contain_text('распознаны OHLC')
                expect(page.locator('#coverage-list')).to_contain_text('На сервере, непрерывные закрытые свечи: M1: 160')
                # Previous click was at the same fixture clock; respect 2s throttle.
                current[0]+=3;command({'action':'advance','seconds':3});quotes()
                page.locator('#request-signal').click()
                expect(page.locator('#signal-result h2')).to_contain_text('PAIR0_otc')
                expect(page.locator('#signal-result h2')).to_contain_text('ВВЕРХ')
                result=ledger.state({'id':101})['recent_requests'][0]
                assert result['catalog_age']==123
                assert result['catalog_source']=='live_socket'
                assert result['status']=='candidate' and result['probability'] is None
                page.locator('#watch-entry').click()
                expect(page.locator('#live-sessions')).to_contain_text('До завершения')
                command({'action':'close'})
                # Payout/expiry metadata remains visible after disconnect;
                # source continuity and entry eligibility must be invalidated.
                expect(page.locator('#feed-coverage')).to_contain_text('готовы к анализу: 0',timeout=7000)
                expect(page.locator('#coverage-list')).to_contain_text('Канал, передавший каталог, закрылся')
                closed=ledger.state({'id':101})['feed']
                assert closed['metadata_eligible']==16
                assert closed['eligible']==closed['ready']==closed['analysis_ready']==0
                assert ledger.state({'id':202})['signal_sessions']==[]
                assert ledger.state({'id':101})['accounts'][0]['balance']=='94.00'
                assert not errors,errors
                (ROOT/'artifacts').mkdir(exist_ok=True)
                page.locator('#live-sessions').scroll_into_view_if_needed();page.screenshot(path=str(ROOT/'artifacts/market-feed-mobile.png'))
                report={'ok':True,'raw_fast_binary_to_real_observer_worker_api_ui':True,'same_socket_catalog_after_123_seconds_without_restamping':True,'16_live_pairs_no_history_is_data_wait_not_fake_direction':True,'fast_history_makes_real_smc_candidate':True,'entry_accepts_valid_same_socket_catalog':True,'close_invalidates_entry_and_analysis':True,'balance_unchanged':True,'other_user_isolated':True,'no_extension_initiated_broker_requests':True,'elapsed_ms':result.get('elapsed_ms'),'page_errors':errors,'owner_native_feed_verified':False}
                (ROOT/'artifacts/market-feed-check.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
            finally:browser.close()
    finally:
        if child.stdin:child.stdin.close()
        try:child.wait(timeout=5)
        except subprocess.TimeoutExpired:child.terminate();child.wait(timeout=5)
        server.shutdown();server.server_close()
