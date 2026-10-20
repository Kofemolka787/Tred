"""One click -> full catalog -> generated read-only requests -> real AI transport.

The broker socket and OpenAI server are fixtures. Actual connector JavaScript,
Responses client, signed backend and Mini App run; no external key or orders.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import threading
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from playwright.sync_api import expect, sync_playwright

from ai_market import OpenAIAnalyzer
from backend import handler
from configure_ai import save
from test_market_analysis import fixture
from test_support import signed_data, FixtureAI
from trading_core import Ledger

ROOT = Path(__file__).resolve().parent
parser=argparse.ArgumentParser()
parser.add_argument('--provider',choices=('openai','lunaroute'),default='openai')
provider=parser.parse_args().provider
provider_name='LunaRoute' if provider=='lunaroute' else 'OpenAI'
fixture_key='lr_fixture_lunaroute_key_for_local_transport_only' if provider=='lunaroute' else 'fixture-openai-api-key-never-public'
with tempfile.TemporaryDirectory(prefix='tred-ai-scan-') as temporary:
    current = [1800000000.0]
    model_requests = []
    model_mode = ['valid']
    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert self.path == '/v1/responses' and body['store'] is False
            assert self.headers['Authorization'] == 'Bearer '+fixture_key
            value = json.loads(body['input'][1]['content']); model_requests.append(value)
            assert 0 < len(value['pairs']) <= 64
            assert not any(key in json.dumps(value) for key in ('account_id', 'balance', 'api_key', 'token', 'user_id'))
            decision=FixtureAI().submit(1,value).result()['decision']
            decision={k:decision[k] for k in ('action','symbol','direction','summary','risk','evidence')}
            text='```json\n'+json.dumps(decision, ensure_ascii=False)+'\n```' if model_mode[0]=='valid' else 'fixture-private-malformed-response'
            reply = json.dumps({'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': text}]}]}).encode()
            content_type='application/json'
            if body.get('stream'):
                content_type='text/event-stream'
                reply=('data: '+json.dumps({'type':'response.created'})+'\n\n'+'data: '+json.dumps({'type':'response.completed','response':json.loads(reply)})+'\n\n').encode()
            self.send_response(200); self.send_header('Content-Type', content_type); self.send_header('Content-Length', str(len(reply))); self.end_headers(); self.wfile.write(reply)
    model_server = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    threading.Thread(target=model_server.serve_forever, daemon=True).start()
    save(temporary, fixture_key, allowed_users=[101], provider=provider)
    engine = OpenAIAnalyzer(temporary, endpoint=f'http://127.0.0.1:{model_server.server_port}/v1/responses')
    ledger = Ledger(Path(temporary)/'ledger.sqlite3', clock=lambda: current[0], ai=engine)
    token = ledger.claim(ledger.pair(101)['code'])['token']
    ledger.settings(101, {'market': 'otc', 'allow_otc': True})
    key = Ed25519PrivateKey.generate()
    Parent = handler(ledger, public_key=key.public_key().public_bytes_raw())
    class App(Parent):
        def do_GET(self):
            if self.path == '/app-config.json': return self.reply(200, {'mode': 'api', 'version': 3})
            return super().do_GET()
    server = ThreadingHTTPServer(('127.0.0.1', 0), App)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    child = subprocess.Popen(['node', 'check_market_pipeline.js', base, token, str(current[0]), '--auto-fixture'], cwd=ROOT,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    def command(value):
        child.stdin.write(json.dumps(value)+'\n'); child.stdin.flush()
        raw = child.stdout.readline()
        assert raw, child.stderr.read()
        result = json.loads(raw); assert result.get('ok'), result; assert not result.get('lastError'), result
        return result
    def frame(name, value): return command({'action': 'frame', 'frame': '42'+json.dumps([name, value])})
    try:
        assert json.loads(child.stdout.readline()) == {'ready': True}
        frame('successauth', {'uid':456, 'isDemo':0, 'balance':94, 'currency':'USD'})
        catalog = []
        for i in range(130):
            row = [None]*16; row[1] = f'PAIR{i}_otc'; row[3] = 'currency' if i < 16 else 'stock'; row[5] = 92 if i < 16 else 0
            row[9] = 1; row[14] = i < 16; row[15] = [{'time':300}, {'time':600}, {'time':900}]; catalog.append(row)
        frame('updateAssets', catalog)
        histories = {}
        for i in range(16):
            histories[f'PAIR{i}_otc'] = {}
            for period in (60, 300):
                rows = fixture(period, int(current[0]), reaction=i == 0)
                histories[f'PAIR{i}_otc'][period] = [{**row, 'time':row['time']+7200} for row in rows]
        command({'action':'fixtures', 'histories':histories})
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path='/usr/bin/chromium', args=['--no-sandbox', '--disable-dev-shm-usage'])
            try:
                context = browser.new_context(viewport={'width':390, 'height':844})
                script = 'window.Telegram={WebApp:{initData:'+json.dumps(signed_data(key))+',initDataUnsafe:{user:{id:101}},ready(){},expand(){}}};'
                context.route('https://telegram.org/js/telegram-web-app.js', lambda r:r.fulfill(content_type='application/javascript', body=script))
                page = context.new_page(); errors = []; page.on('pageerror', lambda e:errors.append(str(e))); page.goto(base)
                expect(page.locator('#feed-coverage')).to_contain_text('Каталог: 130')
                assert page.locator('#coverage-list > p').count() == 130
                expect(page.locator('#ai-status')).to_contain_text('gpt-5-mini')
                expect(page.locator('#ai-status')).to_contain_text(provider_name)
                page.locator('#request-signal').click()
                expect(page.locator('#signal-result')).to_contain_text('Загружаем данные всех пар')
                command({'action':'auto'})
                for i in range(55):
                    current[0] += .4; command({'action':'advance', 'seconds':.4}); command({'action':'tick'})
                    if i % 10 == 9: command({'action':'auto'})
                state = ledger.state({'id':101})
                assert state['feed']['ready'] == 16, [(p['symbol'],p['bars'],p['reason']) for p in state['feed']['pairs'][:16]]
                expect(page.locator('#signal-result h2')).to_contain_text('PAIR0_otc', timeout=15000)
                expect(page.locator('#signal-result')).to_contain_text('ИИ-СЦЕНАРИЙ')
                expect(page.locator('#signal-result')).to_contain_text(provider_name)
                expect(page.locator('#signal-result')).to_contain_text('рассмотрено пар: 16')
                expect(page.locator('#close-preview')).to_contain_text('завершение примерно')
                assert {pair['symbol'] for request in model_requests for pair in request['pairs']}=={f'PAIR{i}_otc' for i in range(16)}
                generated = command({'action':'outgoing'})['frames']
                assert {f[0] for f in generated} == {'subfor','loadHistoryPeriod'}
                assert len({f[1]['asset'] for f in generated if f[0]=='loadHistoryPeriod'}) == 16
                assert all(0 < f[1]['offset'] <= 144000 for f in generated if f[0]=='loadHistoryPeriod')
                page.locator('#signal-result summary').click()
                expect(page.locator('#signal-result details')).to_contain_text('gpt-5-mini')
                page.locator('#watch-entry').click()
                expect(page.locator('#live-sessions')).to_contain_text('До завершения')
                assert ledger.state({'id':101})['accounts'][0]['balance'] == '94.00'
                assert ledger.state({'id':202})['signal_sessions'] == []
                assert fixture_key not in page.content()
                # A completed but malformed model response has its own safe UI
                # diagnostic, separate from a genuine wait or transport outage.
                model_mode[0]='malformed';current[0]+=3
                page.locator('#request-signal').click()
                expect(page.locator('#signal-result')).to_contain_text('Ответ ИИ не прошёл проверку',timeout=15000)
                expect(page.locator('#signal-result')).to_contain_text('Код проверки: decision_json')
                assert 'fixture-private-malformed-response' not in page.content()
                assert fixture_key not in page.content()
                assert not errors, errors
                (ROOT/'artifacts').mkdir(exist_ok=True)
                page.screenshot(path=str(ROOT/'artifacts/ai-scan-mobile.png'), full_page=True)
                report = {'ok':True, 'provider':provider_name, 'catalog_130_not_truncated':True, 'all_16_eligible_pairs_auto_requested':True, 'one_click_waits_for_history_then_ai':True,
                          'actual_responses_client_with_fixture_provider':True, 'market_only_payload':True, 'key_absent_from_browser':True,
                          'entry_time_and_duration_visible':True, 'no_order_or_chart_switch_requests':True, 'balance_unchanged':True, 'other_user_isolated':True,
                          'completed_fenced_json_with_evidence_refs_accepted':True, 'malformed_response_has_specific_safe_ui_error':True,
                          'page_errors':errors, 'native_broker_or_real_provider_service_verified':False}
                (ROOT/'artifacts/ai-scan-check.json').write_text(json.dumps(report,indent=2)+'\n'); print(json.dumps(report))
            finally: browser.close()
    finally:
        child.stdin.close()
        try: child.wait(timeout=5)
        except subprocess.TimeoutExpired: child.terminate(); child.wait(timeout=5)
        server.shutdown(); server.server_close(); model_server.shutdown(); model_server.server_close()
