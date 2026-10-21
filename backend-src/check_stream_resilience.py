"""Native-shaped mixed history -> real worker/API -> one-click AI fixture.

No broker login or paid model: verifies real transport and strict bar filtering.
"""
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import tempfile
import threading

from backend import handler
from test_market_analysis import fixture
from test_support import FixtureAI
from trading_core import Ledger

ROOT=Path(__file__).resolve().parent
with tempfile.TemporaryDirectory(prefix='tred-resilience-') as temporary:
    now=1800000000.
    ai=FixtureAI()
    ledger=Ledger(Path(temporary)/'ledger.sqlite3',clock=lambda:now,ai=ai)
    token=ledger.claim(ledger.pair(101)['code'])['token']
    ledger.settings(101,{'market':'otc','allow_otc':True})
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(ledger))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    child=subprocess.Popen(['node','check_market_pipeline.js',f'http://127.0.0.1:{server.server_port}',token,str(now)],
        cwd=ROOT,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
    def send(name,data):
        child.stdin.write(json.dumps({'action':'frame','frame':'42'+json.dumps([name,data])})+'\n');child.stdin.flush()
        raw=child.stdout.readline();assert raw,child.stderr.read()
        value=json.loads(raw);assert value.get('ok') and not value.get('lastError'),value
    try:
        assert json.loads(child.stdout.readline())=={'ready':True}
        row=[None]*16
        row[1]='PAIR_otc';row[3]='currency';row[5]=92;row[9]=1;row[14]=True;row[15]=[{'time':300},{'time':600},{'time':900}]
        send('updateAssets',[row])
        job=ledger.start_scan(101)
        assert job['status']=='loading' and ai.calls==0
        for period in (60,300):
            rows=[{**r,'time':r['time']+7200} for r in fixture(period)]
            if period==60:
                rows[10]['low']=999
                rows[20]['time']+=2
            send('loadHistoryPeriodFast',{'asset':'PAIR_otc','period':period,'data':rows})
        send('updateStream',[['PAIR_otc',now+7200,106.30]])
        with ledger.db() as db:
            assert db.execute('SELECT count(*) FROM candles WHERE period=60').fetchone()[0]==158
            assert db.execute('SELECT count(*) FROM candles WHERE period=300').fetchone()[0]==160
        state=ledger.state({'id':101})
        assert state['feed']['ready']==1 and state['feed']['pairs'][0]['bars'][0]==139,state['feed']
        result=ledger.poll_scan(101,{'scan_id':job['scan_id']})
        assert result['status']=='candidate' and result['ai']['ready_pairs']==1,result
        assert ai.calls==1 and ledger.poll_scan(101,{'scan_id':job['scan_id']})==result
        assert ledger.state({'id':202})['feed']['catalog']==0
        report={'ok':True,'actual_observer_worker_http_backend':True,'malformed_and_off_grid_bars_held':2,
            'valid_m1_delivered':158,'recent_contiguous_m1_used':139,'one_click_fixture_ai_candidate':True,
            'no_filled_gaps_or_changed_source_time':True,'other_user_isolated':True,
            'real_broker_or_real_provider_verified':False}
        (ROOT/'artifacts').mkdir(exist_ok=True)
        (ROOT/'artifacts/stream-resilience-check.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report))
    finally:
        child.stdin.close()
        try:child.wait(timeout=5)
        except subprocess.TimeoutExpired:child.terminate();child.wait(timeout=5)
        server.shutdown();server.server_close()
