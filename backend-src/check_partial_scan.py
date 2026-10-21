"""Cold 41-pair market, stopped page timers and a deliberately delayed model.

Actual observer/worker, HTTP API and Responses SSE client. Broker and model
messages are fixtures; no external account, model credits or orders are used.
"""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

from ai_market import OpenAIAnalyzer
from backend import handler
from configure_ai import save
from test_market_analysis import fixture
from test_support import FixtureAI
from trading_core import Ledger

ROOT=Path(__file__).resolve().parent
with tempfile.TemporaryDirectory(prefix='tred-partial-') as temporary, patch('backend.time',SimpleNamespace(monotonic=lambda:current[0])):
    current=[1800000000.]
    release=threading.Event();requests=[]
    class Model(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert body['stream'] and body['store'] is False
            value=json.loads(body['input'][1]['content']);requests.append(value)
            assert 0<len(value['pairs'])<=64
            assert not any(k in json.dumps(value) for k in ('account_id','api_key','balance','token','user_id'))
            # Pick a complete chart away from the one visible native stream.
            decision=FixtureAI().submit(1,{**value,'qualified':list(reversed(value['qualified']))}).result()['decision']
            decision={k:decision[k] for k in ('action','symbol','direction','strategy','summary','risk','evidence')}
            assert release.wait(10),'Fixture driver did not release its model response'
            response={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(decision)}]}]}
            payload=('data: '+json.dumps({'type':'response.completed','response':response})+'\n\n').encode()
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
    model=ThreadingHTTPServer(('127.0.0.1',0),Model)
    threading.Thread(target=model.serve_forever,daemon=True).start()
    save(temporary,'lr_fixture_key_only_for_partial_scan',model='deepseek-4.1-flash',allowed_users=[101],provider='lunaroute')
    ai=OpenAIAnalyzer(temporary,endpoint=f'http://127.0.0.1:{model.server_port}/v1/responses')
    ledger=Ledger(Path(temporary)/'ledger.sqlite3',clock=lambda:current[0],ai=ai)
    token=ledger.claim(ledger.pair(101)['code'])['token']
    ledger.settings(101,{'market':'otc','allow_otc':True})
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(ledger))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    child=subprocess.Popen(['node','check_market_pipeline.js',f'http://127.0.0.1:{server.server_port}',token,str(current[0]),
        '--auto-fixture','--quote-probe-fixture','--unindexed-fast-fixture'],cwd=ROOT,
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
    def command(value):
        child.stdin.write(json.dumps(value)+'\n');child.stdin.flush()
        raw=child.stdout.readline();assert raw,child.stderr.read()
        result=json.loads(raw);assert result.get('ok') and not result.get('lastError'),result
        return result
    def frame(name,value):return command({'action':'frame','frame':'42'+json.dumps([name,value])})
    try:
        assert json.loads(child.stdout.readline())=={'ready':True}
        frame('successauth',{'uid':456,'isDemo':0,'balance':94,'currency':'USD'})
        rows=[];histories={}
        for i in range(41):
            symbol=f'PAIR{i}_otc';row=[None]*16
            row[1]=symbol;row[3]='currency';row[5]=92;row[9]=1;row[14]=True;row[15]=[{'time':300},{'time':600},{'time':900}];rows.append(row)
            histories[symbol]={p:[{**r,'time':r['time']+7200} for r in fixture(p)] for p in (60,300)}
        frame('updateAssets',rows);command({'action':'fixtures','histories':histories})
        job=ledger.start_scan(101);command({'action':'auto'})
        result=job;pinned=False
        for i in range(112):
            current[0]+=.4;command({'action':'advance','seconds':.4})
            frame('updateStream',[['PAIR0_otc',current[0]+7200,106.30]])
            if i%5==4:
                result=ledger.poll_scan(101,{'scan_id':job['scan_id']})
                if result.get('phase')=='entry_quote':
                    plan=ledger.bridge_plan(token,{'code':'ready'})['plan']
                    pinned=any(p['priority'] and p['symbol']!='PAIR0_otc' for p in plan['pairs'])
            if requests and current[0]-requests[0]['as_of']>=20:
                release.set()
            if result['status']!='loading':break
        if result['status']=='loading' and release.is_set():
            ai.get(job['scan_id']).result(timeout=3)
            result=ledger.poll_scan(101,{'scan_id':job['scan_id']})
        assert result['status']=='candidate',result
        assert 1<=len(requests)<=5 and result['symbol']!='PAIR0_otc'
        assert {p['symbol'] for v in requests for p in v['pairs']}=={f'PAIR{i}_otc' for i in range(41)}
        assert current[0]<job['deadline'] and current[0]-1800000000<=40
        assert result['ai']['reviewed_pairs']==41 and result['ai']['ready_pairs']<41
        assert 0<=current[0]-result['quote_time']<=10
        assert ledger.state({'id':101})['accounts'][0]['balance']=='94.00'
        assert ledger.state({'id':202})['feed']['catalog']==0
        report={'ok':True,'catalog_pairs':41,'model_calls':len(requests),'model_delay_simulated_seconds':20,
            'model_started_seconds':round(requests[0]['as_of']-1800000000,1),
            'signal_seconds':round(current[0]-1800000000,1),'chosen_pair':result['symbol'],
            'complete_charts_sent_to_model':result['ai']['ready_pairs'],'focused_quote_age':round(current[0]-result['quote_time'],1),
            'tab_timers_disabled':True,'actual_responses_sse_client':True,'inventory_not_truncated':True,
            'selected_quote_focus_used':pinned,
            'balance_unchanged':True,'other_user_isolated':True,'real_broker_or_real_provider_verified':False}
        (ROOT/'artifacts').mkdir(exist_ok=True)
        (ROOT/'artifacts/partial-scan-check.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report))
    finally:
        release.set();child.stdin.close()
        try:child.wait(timeout=5)
        except subprocess.TimeoutExpired:child.terminate();child.wait(timeout=5)
        server.shutdown();server.server_close();model.shutdown();model.server_close();ai.executor.shutdown()
