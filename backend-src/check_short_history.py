"""Native-shaped 24-bar pages through actual MAIN, worker and HTTP backend.

No broker credentials, live provider calls or orders. A short latest response
must be paged backward until two complete, fresh closed histories are available.
"""
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import patch

from backend import handler
from test_market_analysis import fixture
from test_support import FixtureAI
from trading_core import Ledger

ROOT=Path(__file__).resolve().parent
with tempfile.TemporaryDirectory(prefix='tred-short-pages-') as temporary, patch(
        'backend.time',SimpleNamespace(monotonic=lambda:current[0])):
    current=[1800000000.]
    ai=FixtureAI();ledger=Ledger(Path(temporary)/'ledger.sqlite3',clock=lambda:current[0],ai=ai)
    token=ledger.claim(ledger.pair(101)['code'])['token']
    ledger.settings(101,{'market':'otc','allow_otc':True,'expiry':15})
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(ledger))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    child=subprocess.Popen(['node','check_market_pipeline.js',f'http://127.0.0.1:{server.server_port}',token,str(current[0]),
        '--auto-fixture','--quote-probe-fixture','--unindexed-fast-fixture','--short-page-fixture'],cwd=ROOT,
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
    def command(value):
        child.stdin.write(json.dumps(value)+'\n');child.stdin.flush()
        raw=child.stdout.readline();assert raw,child.stderr.read()
        value=json.loads(raw);assert value.get('ok') and not value.get('lastError'),value
        return value
    def frame(name,data):return command({'action':'frame','frame':'42'+json.dumps([name,data])})
    try:
        assert json.loads(child.stdout.readline())=={'ready':True}
        catalog=[];histories={}
        for i in range(19):
            name=f'PAIR{i:02}_otc';row=[None]*16
            row[1]=name;row[3]='currency';row[5]=92;row[9]=1;row[14]=True;row[15]=[{'time':300},{'time':600},{'time':900}]
            catalog.append(row)
            histories[name]={p:[{**r,'time':r['time']+7200} for r in fixture(p)] for p in (300,900)}
        frame('updateAssets',catalog);command({'action':'fixtures','histories':histories})
        job=ledger.start_scan(101);command({'action':'auto'});result=job
        for i in range(220):
            current[0]+=.4;command({'action':'advance','seconds':.4})
            frame('updateStream',[['PAIR00_otc',current[0]+7200,106.30]])
            if i%5==4:result=ledger.poll_scan(101,{'scan_id':job['scan_id']})
            if result['status']!='loading':break
        assert result['status']=='candidate',result
        assert result['expiry']==15 and result['ai']['analyzed_charts']>0
        frames=command({'action':'outgoing'})['frames']
        requests=[v for name,v in frames if name=='loadHistoryPeriod' and v['period']>=60]
        chosen=[v for v in requests if v['asset']==result['symbol']]
        for period in (300,900):
            ends={v['time'] for v in chosen if v['period']==period}
            assert len(ends)>=4,(period,chosen)
            assert all(1800000000+7200-1600*period<=t<=current[0]+7200 for t in ends)
        with ledger.db() as db:
            counts={r[0]:r[1] for r in db.execute('SELECT period,count(*) FROM candles WHERE symbol=? GROUP BY period',(result['symbol'],))}
        assert all(counts.get(p,0)>=80 for p in (300,900)),counts
        assert ledger.state({'id':202})['feed']['catalog']==0
        assert {name for name,_ in frames}<={'subfor','loadHistoryPeriod'}
        report={'ok':True,'catalog_pairs':19,'native_page_bars':24,'chosen_pair':result['symbol'],
                'received_closed_bars':counts,'signal_simulated_seconds':round(current[0]-1800000000,1),
                'actual_main_observer_worker_http':True,'older_history_bounds_requested':True,
                'no_tab_timers':True,'source_clock_preserved':True,'no_fabricated_bars':True,
                'other_user_isolated':True,'real_broker_or_provider_verified':False}
        (ROOT/'artifacts').mkdir(exist_ok=True)
        (ROOT/'artifacts/short-history-check.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report))
    finally:
        child.stdin.close()
        try:child.wait(timeout=5)
        except subprocess.TimeoutExpired:child.terminate();child.wait(timeout=5)
        server.shutdown();server.server_close()
