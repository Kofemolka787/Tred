"""33 histories but zero streams: recover real tick samples via the actual bridge.

Broker and model replies are synthetic fixtures; no owner key or broker login.
"""
from http.server import ThreadingHTTPServer
import argparse
import io
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import sys

parser=argparse.ArgumentParser()
parser.add_argument('--legacy-server',type=Path)
options=parser.parse_args()
if options.legacy_server:
    sys.path.insert(0,str(options.legacy_server))

from ai_market import OpenAIAnalyzer
from backend import handler
from configure_ai import save
from test_market_analysis import fixture
from test_support import FixtureAI
from trading_core import Ledger

ROOT=Path(__file__).resolve().parent
with tempfile.TemporaryDirectory(prefix='tred-quotes-') as temporary:
    current=[1800000000.0]
    reviewed=[]
    def provider(request,timeout):
        value=json.loads(json.loads(request.data)['input'][1]['content']);reviewed.append(value)
        assert request.full_url=='https://gw.lunaroute.com/v1/responses'
        d=FixtureAI().submit(1,value).result()['decision']
        fields=('action','symbol','direction','summary','risk','evidence')
        if 'strategy' in d:
            fields+=('strategy',)
        d={k:d[k] for k in fields}
        return io.BytesIO(json.dumps({'status':'completed','output':[{'content':[{'type':'output_text','text':json.dumps(d)}]}]}).encode())
    save(temporary,'lr_fixture_key_only_for_quote_recovery',allowed_users=[101],provider='lunaroute')
    ai=OpenAIAnalyzer(temporary,opener=provider)
    ledger=Ledger(Path(temporary)/'ledger.sqlite3',clock=lambda:current[0],ai=ai)
    token=ledger.claim(ledger.pair(101)['code'])['token']
    ledger.settings(101,{'market':'otc','allow_otc':True})
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(ledger))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    base=f'http://127.0.0.1:{server.server_port}'
    child=subprocess.Popen(['node','check_market_pipeline.js',base,token,str(current[0]),'--auto-fixture','--quote-probe-fixture'],cwd=ROOT,
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
    def command(value):
        child.stdin.write(json.dumps(value)+'\n');child.stdin.flush()
        raw=child.stdout.readline();assert raw,child.stderr.read()
        result=json.loads(raw);assert result.get('ok'),result;assert not result.get('lastError'),result
        return result
    def frame(name,value):return command({'action':'frame','frame':'42'+json.dumps([name,value])})
    def histories():
        for i in range(33):
            for period in (60,300):
                rows=[{**r,'time':r['time']+7200} for r in fixture(period,int(current[0]//period)*period)]
                frame('loadHistoryPeriod',{'asset':f'PAIR{i}_otc','period':period,'candles':rows})
    try:
        assert json.loads(child.stdout.readline())=={'ready':True}
        frame('successauth',{'uid':456,'isDemo':0,'balance':94,'currency':'USD'})
        rows=[]
        for i in range(33):
            row=[None]*16;row[1]=f'PAIR{i}_otc';row[3]='currency';row[5]=92;row[9]=1;row[14]=True;row[15]=[{'time':300},{'time':600},{'time':900}];rows.append(row)
        frame('updateAssets',rows);histories()
        before=ledger.state({'id':101})['feed']
        metadata=before.get('metadata_eligible',sum(p['stage'] not in ('closed','payout','expiry') for p in before['pairs']))
        assert metadata==33 and before['quoted']==0 and before['ready']==0
        assert all(p['history_ready'] for p in before['pairs'])
        job=ledger.start_scan(101)
        assert job['progress']['history_ready']==33
        # Research uses the real closed graphs, while no live price is invented.
        ai.get(job['scan_id']).result(timeout=5)
        result=ledger.poll_scan(101,{'scan_id':job['scan_id']})
        assert result['status']=='loading' and result['phase']=='entry_quote',result
        assert not result.get('direction') and ledger.state({'id':101})['feed']['quoted']==0
        command({'action':'auto'})
        for i in range(90):
            current[0]+=.4;command({'action':'advance','seconds':.4});command({'action':'tick'})
            if i%10==9:command({'action':'auto'})
            if i%5==4:
                result=ledger.poll_scan(101,{'scan_id':job['scan_id']})
                if result['status']!='loading':break
        after=ledger.state({'id':101})['feed']
        assert after['quoted']>0 and after['ready']>0
        result=ledger.poll_scan(101,{'scan_id':job['scan_id']})
        assert result['status']=='candidate',result
        assert result['ai']['provider']=='LunaRoute' and result['total_pairs']==33
        assert result['ai']['analyzed_charts']==33
        assert {p['symbol'] for batch in reviewed for p in batch['pairs']}=={f'PAIR{i}_otc' for i in range(33)}
        frames=command({'action':'outgoing'})['frames']
        probes=[f[1] for f in frames if f[0]=='loadHistoryPeriod' and f[1]['period']==1]
        assert probes and all(p['offset']==20 for p in probes)
        assert all(f[0] in ('subfor','loadHistoryPeriod') for f in frames)
        state=ledger.state({'id':101})
        assert state['accounts'][0]['balance']=='94.00'
        assert ledger.state({'id':202})['feed']['catalog']==0
        with ledger.db() as db:
            chosen=dict(db.execute('SELECT * FROM assets WHERE user_id=101 AND symbol=?',(result['symbol'],)).fetchone())
            assert 0<=current[0]-chosen['quote_time']<=10
            assert db.execute('SELECT count(*) FROM candles WHERE period=1').fetchone()[0]==0
        report={'ok':True,'legacy_server':bool(options.legacy_server),'history_ready_before':33,'quotes_before':0,'quotes_after':after['quoted'],
            'all_33_pairs_reach_model':True,'all_33_closed_graphs_analyzed':True,'no_entry_before_genuine_quote':True,'silent_stream_recovered_by_timestamped_ticks':True,
            'native_source_clock_preserved':True,'no_quote_from_ohlc_close':True,'no_period_1_research_candles':True,
            'freshness_10_seconds_unchanged':True,'balance_unchanged':True,'other_user_isolated':True,
            'no_order_or_chart_switch_commands':True,'real_broker_or_real_provider_verified':False}
        (ROOT/'artifacts').mkdir(exist_ok=True)
        target='quote-recovery-legacy-check.json' if options.legacy_server else 'quote-recovery-check.json'
        (ROOT/'artifacts'/target).write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report))
    finally:
        child.stdin.close()
        try:child.wait(timeout=5)
        except subprocess.TimeoutExpired:child.terminate();child.wait(timeout=5)
        server.shutdown();server.server_close();ai.executor.shutdown()
