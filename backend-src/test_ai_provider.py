"""Provider routing, real setup transport and failure isolation; dummy keys only."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from ai_market import AIError, OpenAIAnalyzer
from ai_provider import load_config
from configure_ai import lunaroute_models, main, read_lunaroute_model, save
from test_support import FixtureAI
from trading_core import Ledger

KEY = 'lr_fixture_key_only_not_a_real_credential'
MODEL = 'openai/gpt-5-mini'


def reply(value):
    return io.BytesIO(json.dumps({'status':'completed','output':[{'content':[{'type':'output_text','text':json.dumps(value)}]}]}).encode())


class AIProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict('os.environ', {}, clear=True); self.env.start(); self.addCleanup(self.env.stop)

    def test_saved_provider_wins_over_unrelated_env_key_and_unknown_destinations_are_disabled(self):
        save(self.tmp.name, KEY, MODEL, allowed_users=[101], provider='lunaroute')
        with patch.dict('os.environ', {'OPENAI_API_KEY':'fixture-openai-key-must-not-override-lunaroute','OPENAI_MODEL':'other-model'}):
            config = load_config(self.tmp.name)
            self.assertEqual((config['name'],config['api_key'],config['model']), ('LunaRoute',KEY,MODEL))
            self.assertEqual(config['endpoint'], 'https://gw.lunaroute.com/v1/responses')
        with patch.dict('os.environ', {'TRED_AI_PROVIDER':'https://untrusted.invalid'}):
            self.assertFalse(load_config(self.tmp.name)['api_key'])
        with self.assertRaises(ValueError): save(self.tmp.name, KEY, allowed_users=[101])
        with self.assertRaises(ValueError): save(self.tmp.name, 'sk-fixture-openai-must-not-go-to-lunaroute', allowed_users=[101], provider='lunaroute')

    def test_legacy_openai_settings_remain_compatible_and_environment_provider_does_not_cross_keys(self):
        path = Path(self.tmp.name)/'ai-settings.json'
        path.write_text(json.dumps({'api_key':'fixture-old-openai-key-from-0317','model':'gpt-5-mini','allowed_users':[101]}))
        self.assertEqual(load_config(self.tmp.name)['name'], 'OpenAI')
        with patch.dict('os.environ', {'TRED_AI_PROVIDER':'lunaroute'}):
            self.assertFalse(load_config(self.tmp.name)['api_key'])

    def test_lunaroute_review_uses_official_gateway_and_keeps_key_out_of_result(self):
        save(self.tmp.name, KEY, MODEL, allowed_users=[101], provider='lunaroute')
        calls = []
        def opener(request, timeout):
            calls.append(request.full_url)
            self.assertEqual(request.full_url, 'https://gw.lunaroute.com/v1/responses')
            self.assertEqual(request.get_header('Authorization'), 'Bearer '+KEY)
            body=json.loads(request.data)
            self.assertEqual(body['model'], MODEL)
            self.assertFalse(body['store'])
            self.assertEqual(body['text']['format']['type'], 'json_schema')
            value=json.loads(body['input'][1]['content'])
            self.assertNotIn(KEY, json.dumps(value))
            return reply({'action':'wait','symbol':'','direction':'none','summary':'Нет подтверждённой реакции на зону.','risk':'Нужны свежие свечи.','evidence':[]})
        analyzer=OpenAIAnalyzer(self.tmp.name, opener=opener);self.addCleanup(analyzer.executor.shutdown)
        result=analyzer.review({'pairs':[],'qualified':[]})
        self.assertEqual(result['provider'],'LunaRoute')
        self.assertEqual(result['model'], MODEL)
        self.assertEqual(calls, ['https://gw.lunaroute.com/v1/responses'])
        self.assertNotIn(KEY,json.dumps(result))
        self.assertTrue(analyzer.status(101)['configured']);self.assertFalse(analyzer.status(202)['configured'])
        self.assertNotIn(KEY,json.dumps(analyzer.status(101)))

    def test_provider_change_discards_inflight_decision(self):
        save(self.tmp.name, KEY, MODEL, allowed_users=[101], provider='lunaroute')
        def opener(request, timeout):
            save(self.tmp.name, 'fixture-new-openai-config-key', allowed_users=[101])
            return reply({'action':'wait','symbol':'','direction':'none','summary':'Нет входа.','risk':'Дождаться реакции.','evidence':[]})
        analyzer=OpenAIAnalyzer(self.tmp.name,opener=opener);self.addCleanup(analyzer.executor.shutdown)
        with self.assertRaisesRegex(AIError,'Настройки ИИ изменились'):
            analyzer.review({'pairs':[],'qualified':[]})

    def test_setup_fetches_real_catalog_checks_inference_and_preserves_existing_pairing(self):
        ledger=Ledger(Path(self.tmp.name)/'ledger.sqlite3')
        token=ledger.claim(ledger.pair(101)['code'])['token']
        calls=[]
        def opener(request,timeout):
            calls.append((request.get_method(), request.full_url))
            self.assertEqual(request.get_header('Authorization'), 'Bearer '+KEY)
            if request.get_method()=='GET':
                return io.BytesIO(json.dumps({'data':[{'id':'embedding-model','capabilities':{'embeddings':True}}, {'id':MODEL}]}).encode())
            body=json.loads(request.data)
            self.assertEqual(body['model'], MODEL)
            self.assertEqual(body['text']['format']['name'],'tred_connection_check')
            return reply({'ok':True})
        out=io.StringIO()
        with patch('sys.argv',['configure_ai.py','--provider','lunaroute','--data-dir',self.tmp.name]), patch('builtins.input',side_effect=[KEY,'']), patch('configure_ai.urlopen',side_effect=opener), redirect_stdout(out):
            main()
        self.assertEqual(calls,[('GET','https://gw.lunaroute.com/v1/models'),('POST','https://gw.lunaroute.com/v1/responses')])
        config=load_config(self.tmp.name)
        self.assertEqual((config['name'],config['model'],config['allowed_users']),('LunaRoute',MODEL,[101]))
        self.assertIn('Ключ сохранён',out.getvalue());self.assertNotIn(KEY,out.getvalue())
        self.assertTrue(ledger.bridge_plan(token,{'code':'ready'})['ok'])

    def test_lunaroute_wallet_failure_preserves_prior_config_and_shows_only_safe_error(self):
        ledger=Ledger(Path(self.tmp.name)/'ledger.sqlite3');ledger.claim(ledger.pair(101)['code'])
        save(self.tmp.name,'fixture-current-openai-key',allowed_users=[101])
        before=(Path(self.tmp.name)/'ai-settings.json').read_bytes()
        def opener(request,timeout):
            if request.get_method()=='GET': return io.BytesIO(json.dumps({'data':[{'id':MODEL}]}).encode())
            raise HTTPError(request.full_url,402,KEY,{},io.BytesIO(KEY.encode()))
        with patch('sys.argv',['configure_ai.py','--provider','lunaroute','--data-dir',self.tmp.name]),patch('builtins.input',side_effect=[KEY,'']),patch('configure_ai.urlopen',side_effect=opener),redirect_stdout(io.StringIO()),self.assertRaises(ValueError) as caught:
            main()
        self.assertIn('LunaRoute',str(caught.exception));self.assertIn('HTTP 402',str(caught.exception));self.assertNotIn(KEY,str(caught.exception))
        self.assertEqual((Path(self.tmp.name)/'ai-settings.json').read_bytes(),before)

    def test_model_selection_rejects_unknown_id_and_supports_gateway_namespaced_models(self):
        with patch('builtins.input',side_effect=['not-an-enabled-model','1']),redirect_stdout(io.StringIO()):
            self.assertEqual(read_lunaroute_model([MODEL]),MODEL)
        with patch('configure_ai.urlopen',return_value=io.BytesIO(b'{"data":[{"id":"bad\\nterminal"}]}')),self.assertRaisesRegex(ValueError,'не передал'):
            lunaroute_models(KEY)


if __name__=='__main__':unittest.main()
