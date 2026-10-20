"""Actual HTTP/1.1 chunked SSE with delays and a connection kept open at completion.

Local model/market fixtures only. No owner API key or external paid service.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time

from ai_market import AIError, OpenAIAnalyzer, snapshot
from configure_ai import save
from test_market_analysis import fixture
from test_support import FixtureAI

ROOT=Path(__file__).resolve().parent
with tempfile.TemporaryDirectory(prefix='tred-sse-') as temporary:
    state={'mode':'completed','requests':0}
    release=threading.Event();eof=threading.Event()
    secret='lr_fixture_stream_key_not_a_real_credential'
    class Model(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def log_message(self,*_):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert self.path=='/v1/responses' and body['stream'] and body['store'] is False
            assert self.headers['Authorization']=='Bearer '+secret
            value=json.loads(body['input'][1]['content']);state['requests']+=1
            assert not any(k in json.dumps(value) for k in ('account_id','api_key','balance','user_id','token'))
            d=FixtureAI().submit(1,value).result()['decision']
            d={k:d[k] for k in ('action','symbol','direction','strategy','summary','risk','evidence')}
            mode=state['mode']
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Transfer-Encoding','chunked');self.end_headers()
            def chunk(payload):
                self.wfile.write(f'{len(payload):X}\r\n'.encode()+payload+b'\r\n');self.wfile.flush()
            def event(value):chunk(('data: '+json.dumps(value)+'\n\n').encode())
            try:
                event({'type':'response.created'})
                for _ in range(4):
                    chunk(b': heartbeat\n\n');time.sleep(.03)
                event({'type':'response.output_text.delta','delta':json.dumps(d)})
                if mode=='completed':
                    event({'type':'response.completed','response':{'status':'completed','output':[{'content':[{'type':'output_text','text':json.dumps(d)}]}]}})
                    release.wait(timeout=3)
                    eof.set()
                chunk(b'')
            except (BrokenPipeError,ConnectionResetError):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Model)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    save(temporary,secret,'deepseek-4.1-flash',allowed_users=[101],provider='lunaroute')
    ai=OpenAIAnalyzer(temporary,endpoint=f'http://127.0.0.1:{server.server_port}/v1/responses')
    now=1800000000.0
    asset={'symbol':'PAIR_otc','market':'otc','is_open':True,'payout':.92,'expiries':[5,10,15],'updated_at':now,'price':106.30,'quote_time':now}
    bars={('PAIR_otc',p):fixture(p) for p in (60,300)}
    value,_=snapshot([asset],bars,{'expiry':5,'market':'otc','symbol':None,'allow_otc':True,'min_payout':85},now)
    try:
        decision=ai.review(value)
        assert decision['action']=='enter' and decision['provider']=='LunaRoute'
        assert not eof.is_set(),'Client must return at response.completed without waiting for proxy connection EOF'
        release.set()
        state['mode']='truncated'
        try:ai.review(value)
        except AIError as error:assert error.code=='incomplete_response'
        else:raise AssertionError('A plausible partial JSON delta must never become an entry')
        assert state['requests']==2,'No automatic paid request retries'
        report={'ok':True,'real_http_11_chunked_sse':True,'delayed_heartbeats_and_completed_response':True,
            'does_not_wait_for_connection_eof':True,'truncated_valid_json_delta_rejected':True,
            'no_automatic_paid_retry':True,'market_only_payload':True,'real_provider_verified':False}
        (ROOT/'artifacts').mkdir(exist_ok=True);(ROOT/'artifacts/ai-stream-http-check.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report))
    finally:release.set();server.shutdown();server.server_close();ai.executor.shutdown()
