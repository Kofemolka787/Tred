"""Cold catalog, unindexed Fast replies and no live stream: actual collector.

All broker messages and model decisions are fixtures. No external credentials.
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
with tempfile.TemporaryDirectory(prefix='tred-fast-') as temporary:
    current=[1800000000.0]
    ai=FixtureAI()
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
        result=json.loads(raw);assert result.get('ok'),result;assert not result.get('lastError'),result
        return result
    def frame(name,value):return command({'action':'frame','frame':'42'+json.dumps([name,value])})
    try:
        assert json.loads(child.stdout.readline())=={'ready':True}
        frame('successauth',{'uid':456,'isDemo':0,'balance':94,'currency':'USD'})
        catalog=[];histories={}
        for i in range(41):
            symbol=f'PAIR{i}_otc'
            row=[None]*16;row[1]=symbol;row[3]='currency';row[5]=92;row[9]=1;row[14]=True;row[15]=[{'time':300},{'time':600},{'time':900}];catalog.append(row)
            histories[symbol]={p:[{**r,'time':r['time']+7200} for r in fixture(p)] for p in (60,120,300,600,900)}
        frame('updateAssets',catalog);command({'action':'fixtures','histories':histories})
        before=ledger.state({'id':101})['feed']
        assert before['ready']==0 and before['quoted']==0 and not any(p['history_ready'] for p in before['pairs'])
        job=ledger.start_scan(101);command({'action':'auto'})
        samples=[]
        for i in range(250):
            current[0]+=.4;command({'action':'advance','seconds':.4});command({'action':'tick'})
            if i%10==9:command({'action':'auto'})
            if i%25==24:
                feed=ledger.state({'id':101})['feed']
                samples.append({'seconds':round(current[0]-1800000000),'histories':sum(p['history_ready'] for p in feed['pairs']),'quotes':feed['quoted'],'ready':feed['ready']})
        frames=command({'action':'outgoing'})['frames']
        requested={p[1]['asset'] for p in frames if p[0]=='loadHistoryPeriod' and p[1]['period']>=60}
        assert len(requested)==41,('Every eligible instrument must be requested',len(requested),samples)
        with ledger.db() as db:
            history_symbols=db.execute('SELECT count(DISTINCT symbol) FROM candles WHERE user_id=101').fetchone()[0]
            assert history_symbols==41,(history_symbols,samples)
            for p in (60,300):
                assert db.execute('SELECT count(DISTINCT symbol) FROM candles WHERE user_id=101 AND period=?',(p,)).fetchone()[0]==41
            assert db.execute('SELECT count(*) FROM candles WHERE period=1').fetchone()[0]==0
        assert max(s['histories'] for s in samples)>=30,samples
        assert max(s['quotes'] for s in samples)>=8,(samples,frames[-12:])
        assert ledger.state({'id':101})['accounts'][0]['balance']=='94.00'
        assert ledger.state({'id':202})['feed']['catalog']==0
        assert {f[0] for f in frames}=={'subfor','loadHistoryPeriod'}
        report={'ok':True,'cold_catalog_pairs':41,'both_periods_received_for_all_41':True,
                'fast_responses_without_index_or_period':True,'all_stream_subscriptions_silent':True,
                'genuine_timestamped_tick_probes_only':True,'samples':samples,'read_only_market_requests':True,
                'balance_unchanged':True,'other_user_isolated':True,'real_broker_verified':False}
        (ROOT/'artifacts').mkdir(exist_ok=True)
        (ROOT/'artifacts/fast-collection-check.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report))
    finally:
        child.stdin.close()
        try:child.wait(timeout=5)
        except subprocess.TimeoutExpired:child.terminate();child.wait(timeout=5)
        server.shutdown();server.server_close()
