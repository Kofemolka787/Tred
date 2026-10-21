"""Public-protocol case: advanced OHLC empty, native chart history present.

Actual collector, observer, worker and signed HTTP backend, with broker/model
fixtures. The foreground path is separately tested to never send changeSymbol.
"""
from http.server import ThreadingHTTPServer
import argparse
import io
import json
from pathlib import Path
import subprocess
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import patch

from backend import handler
from ai_market import OpenAIAnalyzer
from configure_ai import save
from test_market_analysis import fixture
from test_support import FixtureAI
from trading_core import Ledger

ROOT=Path(__file__).resolve().parent
parser=argparse.ArgumentParser();parser.add_argument('--expiry',type=int,choices=(5,10,15),default=15)
parser.add_argument('--warm',action='store_true',help='Collect before analysis, then repeat manual requests on all durations')
options=parser.parse_args();expiry=options.expiry
count=6 if options.warm else 23
with tempfile.TemporaryDirectory(prefix='tred-native-chart-') as temporary, patch('backend.time',SimpleNamespace(monotonic=lambda:current[0])):
    current=[1800000000.]
    reviews=[]
    def provider(request,timeout):
        payload=json.loads(json.loads(request.data)['input'][1]['content'])
        reviews.append(payload)
        d=FixtureAI().submit(1,payload).result()['decision']
        d={k:d[k] for k in ('action','symbol','direction','strategy','summary','risk','evidence')}
        return io.BytesIO(json.dumps({'status':'completed','output':[{'content':[{'type':'output_text','text':json.dumps(d)}]}]}).encode())
    save(temporary,'lr_fixture_native_history_only',provider='lunaroute',allowed_users=[101])
    ai=OpenAIAnalyzer(temporary,opener=provider);ledger=Ledger(Path(temporary)/'ledger.sqlite3',clock=lambda:current[0],ai=ai)
    token=ledger.claim(ledger.pair(101)['code'])['token']
    ledger.settings(101,{'market':'otc','allow_otc':True,'expiry':expiry})
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(ledger))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    child=subprocess.Popen(['node','check_market_pipeline.js',f'http://127.0.0.1:{server.server_port}',token,str(current[0]),
        '--auto-fixture','--native-chart-fixture'],cwd=ROOT,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
    def command(value):
        child.stdin.write(json.dumps(value)+'\n');child.stdin.flush()
        raw=child.stdout.readline();assert raw,child.stderr.read()
        value=json.loads(raw);assert value.get('ok') and not value.get('lastError'),value
        return value
    def frame(name,data):return command({'action':'frame','frame':'42'+json.dumps([name,data])})
    try:
        assert json.loads(child.stdout.readline())=={'ready':True}
        frame('successauth',{'uid':456,'isDemo':1,'currency':'USD','balance':94})
        catalog=[];histories={}
        for i in range(count):
            name=f'PAIR{i:02}_otc';row=[None]*16
            row[1]=name;row[3]='currency';row[5]=92;row[9]=1;row[14]=True;row[15]=[{'time':300},{'time':600},{'time':900}]
            catalog.append(row);histories[name]={p:[{**r,'time':r['time']+7200} for r in fixture(p)] for p in (60,300,900)}
        frame('updateAssets',catalog);command({'action':'fixtures','histories':histories})
        assert ledger.state({'id':101})['feed']['analysis_ready']==0
        if options.warm:
            # No user scan or model call exists during this collection window.
            command({'action':'auto'})
            for i in range(140):
                current[0]+=.4;command({'action':'advance','seconds':.4})
                frame('updateStream',[['PAIR00_otc',current[0]+7200,106.30]])
                if i%10==9:command({'action':'auto'})
                with ledger.db() as db:
                    groups=db.execute('SELECT symbol,period,count(*) FROM candles GROUP BY symbol,period HAVING count(*)>=80').fetchall()
                if len(groups)==count*3:break
            assert len(groups)==count*3,[(tuple(r)) for r in groups]
            assert not reviews,'Warm collection must never spend model credits'
            with ledger.db() as db:feed_before=db.execute('SELECT catalog_feed FROM assets ORDER BY symbol').fetchall()
            chosen=[]
            for duration in (5,10,15):
                selected=f'PAIR{len(chosen):02}_otc'
                ledger.settings(101,{'expiry':duration,'selection':'manual','symbol':selected})
                job=ledger.start_scan(101)
                if job['status']=='loading':
                    ai.get(job['scan_id']).result(timeout=5)
                    job=ledger.poll_scan(101,{'scan_id':job['scan_id']})
                assert job['status']=='candidate' and job['symbol']==selected,job
                assert job['expiry']==duration and job['ai']['analyzed_charts']==1
                chosen.append(selected);command({'action':'auto'})
            with ledger.db() as db:
                assert [tuple(r) for r in feed_before]==[tuple(r) for r in db.execute('SELECT catalog_feed FROM assets ORDER BY symbol')]
            report={'ok':True,'warm_pairs':count,'three_periods_ready_before_first_click':True,'background_model_calls':0,
                    'manual_durations':[5,10,15],'manual_pairs':chosen,'same_source_and_history_reused':True,
                    'model_calls_after_three_clicks':len(reviews),'balance_unchanged':ledger.state({'id':101})['accounts'][0]['balance']=='94.00',
                    'owner_native_browser_or_broker_verified':False}
            (ROOT/'artifacts').mkdir(exist_ok=True)
            (ROOT/'artifacts/warm-native-scan-check.json').write_text(json.dumps(report,indent=2)+'\n')
            print(json.dumps(report))
        else:
            job=ledger.start_scan(101);command({'action':'auto'});result=job
            for i in range(210):
                current[0]+=.4;command({'action':'advance','seconds':.4})
                # Simulate the terminal's own current-chart stream with no page timers.
                frame('updateStream',[['PAIR00_otc',current[0]+7200,106.30]])
                if i%10==9:command({'action':'auto'})
                if i%5==4:result=ledger.poll_scan(101,{'scan_id':job['scan_id']})
                if result['status']!='loading':break
            assert result['status']=='candidate',result
            assert result['expiry']==expiry and result['ai']['analyzed_charts']>=1
            assert result['ai']['reviewed_pairs']==23
            frames=command({'action':'outgoing'})['frames']
            assert any(name=='changeSymbol' for name,_ in frames)
            assert {name for name,_ in frames}<={'changeSymbol','subfor','loadHistoryPeriod'}
            assert not any('session' in json.dumps(v) for _,v in frames)
            with ledger.db() as db:
                source=db.execute('SELECT source_role FROM assets WHERE symbol=?',(result['symbol'],)).fetchone()[0]
            assert source=='scanner'
            assert ledger.state({'id':101})['accounts'][0]['balance']=='94.00'
            assert ledger.state({'id':202})['feed']['catalog']==0
            report={'ok':True,'expiry':expiry,'catalog_pairs':23,'all_advanced_history_and_tick_requests_empty':True,'native_change_symbol_history':True,
                    'nested_binary_update_history_decoded':True,'no_page_timers':True,'source_role':source,
                    'analyzed_graphs':result['ai']['analyzed_charts'],'signal_simulated_seconds':round(current[0]-1800000000,1),
                    'no_auth_or_order_commands':True,'balance_unchanged':True,'other_user_isolated':True,
                    'actual_responses_client_with_fixture_provider':True,'owner_native_browser_or_broker_verified':False}
            (ROOT/'artifacts').mkdir(exist_ok=True)
            (ROOT/f'artifacts/native-chart-{expiry}-check.json').write_text(json.dumps(report,indent=2)+'\n')
            print(json.dumps(report))
    finally:
        child.stdin.close()
        try:child.wait(timeout=5)
        except subprocess.TimeoutExpired:child.terminate();child.wait(timeout=5)
        server.shutdown();server.server_close()
        ai.executor.shutdown()
