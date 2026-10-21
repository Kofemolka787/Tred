from concurrent.futures import Future
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from ai_market import OpenAIAnalyzer, AIError, snapshot, validate
from configure_ai import save
from trading_core import Ledger, DomainError
from test_market_analysis import fixture
from test_support import FixtureAI, wire_decision

FEED = "11111111-1111-4111-8111-111111111111"


class AIMarketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.now = 1800000000.0
        self.ai = FixtureAI()
        self.ledger = Ledger(Path(self.tmp.name) / 'ledger.sqlite3', clock=lambda: self.now, ai=self.ai)
        self.token = self.ledger.claim(self.ledger.pair(101)['code'])['token']
        self.ledger.settings(101, {'market': 'otc', 'allow_otc': True})
        self.asset = {'type': 'asset', 'symbol': 'PAIR_otc', 'market': 'otc', 'is_open': True, 'payout': .92, 'expiries': [5, 10, 15], 'time': self.now, 'feed_id': FEED}
        self.ingest(self.asset, self.quote())

    def ingest(self, *events): return self.ledger.ingest(self.token, {'events': list(events)})
    def quote(self, **changes): return {'type': 'quote', 'symbol': 'PAIR_otc', 'price': 106.30, 'time': self.now, 'feed_id': FEED, **changes}
    def history(self):
        for period in (60, 300):
            self.ingest(*[{'type': 'candle', 'symbol': 'PAIR_otc', 'period': period, **r} for r in fixture(period)])

    def test_missing_data_waits_then_same_click_gets_ai_decision_once(self):
        result = self.ledger.start_scan(101)
        self.assertEqual(result['status'], 'loading'); self.assertEqual(self.ai.calls, 0)
        self.history()
        done = self.ledger.poll_scan(101, {'scan_id': result['scan_id']})
        self.assertEqual(done['status'], 'candidate'); self.assertEqual(done['ai']['provider'], 'OpenAI')
        self.assertEqual(done['total_pairs'], 1); self.assertEqual(done['checked_pairs'], 1)
        self.assertIsNone(done['probability'])
        self.assertEqual(self.ledger.poll_scan(101, {'scan_id': result['scan_id']}), done)
        self.assertEqual(self.ai.calls, 1)
        with self.assertRaises(DomainError): self.ledger.poll_scan(202, {'scan_id': result['scan_id']})

    def test_silent_pairs_do_not_hold_ready_charts_until_the_collection_deadline(self):
        self.history()
        for i in range(40):
            self.ingest({**self.asset,'symbol':f'SILENT{i}_otc'})
        job=self.ledger.start_scan(101)
        self.assertEqual(job['status'],'loading');self.assertEqual(self.ai.calls,0)
        self.now+=11;self.ingest(self.quote())
        self.assertEqual(self.ledger.poll_scan(101,{'scan_id':job['scan_id']})['status'],'loading')
        self.now+=1;self.ingest(self.quote())
        result=self.ledger.poll_scan(101,{'scan_id':job['scan_id']})
        self.assertEqual(result['status'],'candidate',result)
        self.assertLess(self.now,job['deadline'])
        self.assertEqual((result['total_pairs'],result['checked_pairs']),(41,1))
        self.assertEqual(result['ai']['reviewed_pairs'],41)
        reviewed=self.ai.tasks[job['scan_id']].result()['snapshot']
        self.assertEqual(sum(p['data_ready'] for p in reviewed['pairs']),1)
        self.assertEqual(self.ai.calls,1)

    def test_empty_early_shortlist_waits_for_later_graphs_before_paid_review(self):
        self.ingest(self.quote(price=100),{**self.asset,'symbol':'LATER_otc'},{**self.asset,'symbol':'SILENT_otc'})
        for period in (60,300):
            self.ingest(*[{'type':'candle','symbol':'PAIR_otc','period':period,
                          **{**r,'open':100,'close':100,'high':100.2,'low':99.8}} for r in fixture(period)])
        job=self.ledger.start_scan(101)
        self.assertEqual(job['status'],'loading')
        self.now+=12;self.ingest(self.quote(price=100))
        waiting=self.ledger.poll_scan(101,{'scan_id':job['scan_id']})
        self.assertEqual(waiting['phase'],'setups')
        self.assertEqual(waiting['progress']['analysis_ready'],1)
        self.assertEqual(self.ai.calls,0,'Do not freeze an empty early shortlist into a paid refusal')
        for period in (60,300):
            self.ingest(*[{'type':'candle','symbol':'LATER_otc','period':period,**r} for r in fixture(period)])
        self.ingest(self.quote(symbol='LATER_otc'))
        result=self.ledger.poll_scan(101,{'scan_id':job['scan_id']})
        self.assertEqual(result['status'],'candidate',result)
        self.assertEqual(result['symbol'],'LATER_otc')
        self.assertEqual(result['ai']['analyzed_charts'],2)
        self.assertEqual(self.ai.calls,1)

    def test_no_setup_collection_still_stops_at_deadline_once(self):
        self.ingest(self.quote(price=100),{**self.asset,'symbol':'SILENT_otc'})
        for period in (60,300):
            self.ingest(*[{'type':'candle','symbol':'PAIR_otc','period':period,
                          **{**r,'open':100,'close':100,'high':100.2,'low':99.8}} for r in fixture(period)])
        job=self.ledger.start_scan(101);self.now=job['deadline']+.1
        self.ingest(self.quote(price=100))
        result=self.ledger.poll_scan(101,{'scan_id':job['scan_id']})
        self.assertEqual(result['status'],'skipped',result)
        self.assertEqual(self.ai.calls,1)
        self.assertNotIn('direction',result)
        self.assertEqual(self.ledger.poll_scan(101,{'scan_id':job['scan_id']}),result)
        self.assertEqual(self.ai.calls,1)

    def test_selected_ai_scenario_pins_its_price_until_entry_check_or_cancel(self):
        self.history()
        class DelayedAI(FixtureAI):
            def submit(self, job_id, value):
                super().submit(job_id,value)
                self.finished=self.tasks[job_id].result()
                self.tasks[job_id]=Future()
                return self.tasks[job_id]
        self.ledger.ai=DelayedAI()
        job=self.ledger.start_scan(101)
        self.assertEqual(job['phase'],'ai')
        plan=self.ledger.bridge_plan(self.token,{'code':'ready'})['plan']
        self.assertFalse(plan['pairs'][0]['priority'],'Research does not block collection by pinning every graph')
        self.now+=11
        self.ledger.ai.tasks[job['scan_id']].set_result(self.ledger.ai.finished)
        self.assertEqual(self.ledger.poll_scan(101,{'scan_id':job['scan_id']})['phase'],'entry_quote')
        plan=self.ledger.bridge_plan(self.token,{'code':'ready'})['plan']
        self.assertTrue(plan['pairs'][0]['priority'])
        self.assertTrue(plan['pairs'][0]['quote'],'A focused task stays present between genuine ticks')
        self.ledger.poll_scan(101,{'scan_id':job['scan_id']},cancel=True)
        plan=self.ledger.bridge_plan(self.token,{'code':'ready'})['plan']
        self.assertFalse(plan['pairs'][0]['priority'])

    def test_unchanged_smc_zone_is_revalidated_when_a_bar_closes_during_model_review(self):
        self.history()
        class DelayedAI(FixtureAI):
            def submit(self,job_id,value):
                super().submit(job_id,value)
                self.original=self.tasks[job_id].result()
                self.tasks[job_id]=Future()
                return self.tasks[job_id]
        engine=DelayedAI();self.ledger.ai=engine
        job=self.ledger.start_scan(101)
        started=self.now;self.now+=61
        self.ingest({'type':'candle','symbol':'PAIR_otc','period':60,'time':started,'open':106.30,'high':106.36,'low':106.24,'close':106.30},self.quote())
        engine.tasks[job['scan_id']].set_result(engine.original)
        result=self.ledger.poll_scan(101,{'scan_id':job['scan_id']})
        self.assertEqual(result['status'],'candidate',result)
        self.assertEqual(result['bar_time'],started)
        original=engine.original['snapshot']['qualified'][0]
        self.assertEqual(result['setup_id'],original['setup_id'])
        self.assertGreaterEqual(result['entry_low'],original['entry_low'])
        self.assertLessEqual(result['entry_high'],original['entry_high'])

    def test_confirming_ai_signal_across_bar_close_requires_the_same_fresh_zone(self):
        self.history()
        class DelayedAI(FixtureAI):
            def submit(self,job_id,value):
                super().submit(job_id,value)
                self.original=self.tasks[job_id].result()
                self.tasks[job_id]=Future()
                return self.tasks[job_id]
        engine=DelayedAI();self.ledger.ai=engine
        started=self.now;job=self.ledger.start_scan(101)
        self.now+=59;self.ingest(self.quote())
        engine.tasks[job['scan_id']].set_result(engine.original)
        result=self.ledger.poll_scan(101,{'scan_id':job['scan_id']})
        self.assertEqual(result['status'],'candidate')
        self.now+=2
        self.ingest({'type':'account','mode':'real','account_id':'456','currency':'USD','balance':'94','time':self.now},
            {'type':'candle','symbol':'PAIR_otc','period':60,'time':started,'open':106.30,'high':106.36,'low':106.24,'close':106.30},self.quote())
        sid=self.ledger.watch(101,{'analysis_id':result['analysis_id'],'mode':'real'})
        self.assertIsInstance(sid,int)

    def test_plan_filters_market_payout_expiry_and_retains_all_catalog_rows(self):
        for i in range(130):
            self.ingest({**self.asset, 'symbol': f'CLOSED{i}_otc', 'is_open': False, 'payout': 0})
        self.ingest({**self.asset, 'symbol': 'LOW_otc', 'payout': .84}, {**self.asset, 'symbol': 'SHORT_otc', 'expiries': [1, 2]})
        self.assertEqual(len(self.ledger.state({'id': 101})['feed']['pairs']), 133)
        plan = self.ledger.bridge_plan(self.token, {'code': 'ready'})['plan']
        self.assertEqual([p['symbol'] for p in plan['pairs']], ['PAIR_otc'])
        self.assertEqual([p['period'] for p in plan['pairs'][0]['history']], [60, 300])
        for value in ({'code': 'openOrder'}, {'code': 'ready', 'user_id': 202}):
            with self.assertRaises(DomainError): self.ledger.bridge_plan(self.token, value)
        with self.assertRaises(DomainError): self.ledger.bridge_plan('not-a-token-but-long-enough-for-validation', {'code': 'ready'})
        self.ingest({'type': 'feed', 'feed_id': FEED, 'connected': False, 'time': self.now})
        self.assertEqual(self.ledger.bridge_plan(self.token, {'code': 'ready'})['plan']['pairs'], [])

    def test_api_key_is_local_user_scoped_and_absent_from_state(self):
        key = 'fixture-openai-key-not-a-real-credential'
        save(self.tmp.name, key, allowed_users=[101])
        analyzer = OpenAIAnalyzer(self.tmp.name)
        with patch.dict('os.environ', {}, clear=True):
            self.assertTrue(analyzer.status(101)['configured']); self.assertFalse(analyzer.status(202)['configured'])
        self.ledger.ai = analyzer
        self.assertNotIn(key, json.dumps(self.ledger.state({'id': 101})))
        self.assertEqual(self.ledger.start_scan(202)['status'], 'setup_required')
        with self.assertRaises(ValueError): save(self.tmp.name, key, allowed_users=[])

    def test_model_uses_market_evidence_without_account_or_credentials(self):
        self.history()
        with self.ledger.db() as db: prefs, assets, histories = self.ledger._market(db, 101)
        value, _ = snapshot(assets, histories, prefs, self.now)
        raw = json.dumps(value)
        for forbidden in ('account_id', 'user_id', 'balance', 'token', 'api_key'):
            self.assertNotIn(forbidden, raw)
        reference = FixtureAI().submit(1, value).result()['decision']
        decision = {k: reference[k] for k in ('action', 'symbol', 'direction', 'strategy', 'summary', 'risk', 'evidence')}
        seen = []
        def opener(request, timeout):
            body = json.loads(request.data); seen.append(body)
            self.assertEqual(body['text']['format']['type'], 'json_schema'); self.assertFalse(body['store'])
            self.assertEqual(timeout, 35)
            payload = json.loads(body['input'][1]['content'])
            self.assertIn('Верни только один JSON-объект', body['input'][0]['content'])
            self.assertEqual(body['text']['format']['schema']['properties']['evidence']['items']['required'], ['ref', 'explanation'])
            return io.BytesIO(json.dumps({'status': 'completed', 'output': [{'content': [{'type': 'output_text', 'text': json.dumps(wire_decision(decision, payload))}]}]}).encode())
        save(self.tmp.name, 'fixture-key-only-for-contract-check', allowed_users=[101])
        analyzer = OpenAIAnalyzer(self.tmp.name, opener=opener)
        result = analyzer.review(value)
        self.assertEqual(result['symbol'], 'PAIR_otc'); self.assertEqual(len(seen), 1)
        for change in ({'symbol': 'INVENTED_otc'}, {'direction': 'down'}, {'evidence': [{**decision['evidence'][0], 'time': self.now+9999}]}):
            with self.assertRaises(ValueError): validate({**decision, **change}, value)

    def test_wait_is_not_replaced_with_rule_signal_and_model_error_is_visible(self):
        self.history()
        job = self.ledger.start_scan(101)
        # Completed jobs stay idempotent; a fresh job tests model refusal.
        class WaitAI(FixtureAI):
            def submit(self, job_id, value):
                f = super().submit(job_id, value)
                d = f.result()['decision']; d.update(action='wait', symbol='', direction='none', strategy='', evidence=[], summary='Реакция недостаточно убедительна.')
                return f
        self.ledger.ai = WaitAI(); self.now += 3
        result = self.ledger.start_scan(101)
        self.assertEqual(result['status'], 'skipped'); self.assertIn('недостаточно', result['reason'])
        class BrokenAI(FixtureAI):
            def submit(self, job_id, value):
                f = Future(); f.set_exception(AIError('Проверь API-ключ.')); self.tasks[job_id] = f; return f
        self.ledger.ai = BrokenAI(); self.now += 3
        result = self.ledger.start_scan(101)
        self.assertEqual(result['status'], 'ai_unavailable'); self.assertIn('API-ключ', result['reason'])

    def test_slow_ai_does_not_lock_balance_ingestion_and_stale_entry_is_rejected(self):
        self.history()
        class DelayedAI(FixtureAI):
            def submit(self, job_id, value):
                super().submit(job_id, value)
                self.reviewed = self.tasks[job_id].result()
                self.tasks[job_id] = Future()
                return self.tasks[job_id]
        self.ai = DelayedAI(); self.ledger.ai = self.ai
        job = self.ledger.start_scan(101)
        self.assertEqual(job.get('phase'), 'ai')
        self.ingest({'type':'account','mode':'demo','account_id':'456','currency':'USD','balance':'105.20','time':self.now})
        self.assertEqual(self.ledger.state({'id':101})['accounts'][0]['balance'], '105.20')
        self.now += 1; self.ingest(self.quote(price=120))
        self.ai.tasks[job['scan_id']].set_result(self.ai.reviewed)
        result = self.ledger.poll_scan(101, {'scan_id':job['scan_id']})
        self.assertEqual(result['status'], 'skipped'); self.assertIn('изменились', result['reason'])

    def test_cancel_and_settings_change_discard_pending_analysis(self):
        job = self.ledger.start_scan(101)
        self.assertEqual(self.ledger.poll_scan(101, {'scan_id':job['scan_id']}, cancel=True)['status'], 'cancelled')
        other = self.ledger.start_scan(101)
        self.ledger.settings(101, {'expiry':10})
        self.assertEqual(self.ledger.poll_scan(101, {'scan_id':other['scan_id']})['status'], 'cancelled')

    def test_large_catalog_reviews_every_pair_then_compares_batch_winners(self):
        self.history()
        with self.ledger.db() as db: prefs, assets, histories = self.ledger._market(db, 101)
        base, _ = snapshot(assets, histories, prefs, self.now)
        pairs, qualified = [], []
        for i in range(34):
            name = f'PAIR{i}_otc'
            pairs.append({**base['pairs'][0], 'symbol':name})
            qualified.append({**base['qualified'][0], 'symbol':name})
        data = {**base, 'pairs':pairs, 'qualified':qualified}
        calls = []
        def opener(request, timeout):
            value = json.loads(json.loads(request.data)['input'][1]['content']); calls.append(value)
            d = FixtureAI().submit(1, value).result()['decision']
            d = {k:d[k] for k in ('action','symbol','direction','strategy','summary','risk','evidence')}
            return io.BytesIO(json.dumps({'status':'completed','output':[{'content':[{'type':'output_text','text':json.dumps(d)}]}]}).encode())
        save(self.tmp.name, 'fixture-key-for-batching-only', allowed_users=[101])
        result = OpenAIAnalyzer(self.tmp.name, opener=opener).review(data)
        self.assertEqual(result['reviewed_pairs'], 34)
        self.assertEqual(result['batches'], 4)
        self.assertEqual({p['symbol'] for c in calls for p in c['pairs']}, {p['symbol'] for p in pairs})
        self.assertTrue(all(len(c['pairs'])<=16 for c in calls))

    def test_429_provider_codes_reach_scan_result_without_provider_secrets(self):
        self.history()
        secret = 'fixture-private-provider-message-do-not-expose'
        save(self.tmp.name, 'fixture-openai-key-for-error-check', allowed_users=[101])
        for code, marker in [('insufficient_quota', 'API-квота исчерпана'), ('rate_limit_exceeded', 'временный лимит')]:
            with self.subTest(code=code):
                def opener(request, timeout):
                    payload = {'error': {'code':code, 'message':secret}}
                    raise HTTPError(request.full_url, 429, secret, {}, io.BytesIO(json.dumps(payload).encode()))
                analyzer = OpenAIAnalyzer(self.tmp.name, opener=opener)
                self.addCleanup(analyzer.executor.shutdown)
                self.ledger.ai = analyzer
                self.now += 3
                job = self.ledger.start_scan(101)
                analyzer.get(job['scan_id']).exception(timeout=2)
                result = self.ledger.poll_scan(101, {'scan_id':job['scan_id']})
                self.assertEqual(result['status'], 'ai_unavailable')
                self.assertIn(marker, result['reason'])
                self.assertIn(code, result['reason'])
                self.assertNotIn(secret, json.dumps(result))
                self.assertNotIn('fixture-openai-key-for-error-check', json.dumps(result))


    def test_history_only_ai_review_still_waits_for_genuine_entry_quote(self):
        self.history();self.now+=11
        result=self.ledger.start_scan(101)
        self.assertEqual(result['phase'],'entry_quote')
        self.assertEqual(result['progress']['history_ready'],1)
        self.assertEqual(result['progress']['quoted'],0)
        self.assertEqual(self.ai.calls,1)
        with self.ledger.db() as db:
            self.assertEqual(db.execute('SELECT quote_time FROM assets').fetchone()[0],self.now-11)
        self.assertNotIn('direction',result,'A historical scenario is not a live entry')
        self.ingest(self.quote())
        result=self.ledger.poll_scan(101,{'scan_id':result['scan_id']})
        self.assertEqual(result['status'],'candidate')
        self.assertEqual(self.ai.calls,1)

    def test_missing_selected_quote_times_out_without_entry_or_another_ai_call(self):
        self.history();self.now+=11
        result=self.ledger.start_scan(101)
        self.assertEqual(result['phase'],'entry_quote')
        self.now+=13
        result=self.ledger.poll_scan(101,{'scan_id':result['scan_id']})
        self.assertEqual(result['status'],'skipped',result)
        self.assertTrue(result['waiting_data'])
        self.assertIn('свежая цена',result['reason'])
        self.assertNotIn('direction',result)
        self.assertEqual(self.ai.calls,1)

    def test_history_only_choice_is_not_an_entry_when_new_live_price_has_left_zone(self):
        self.history();self.now+=11
        result=self.ledger.start_scan(101)
        self.assertEqual(result['phase'],'entry_quote')
        self.ingest(self.quote(price=120))
        result=self.ledger.poll_scan(101,{'scan_id':result['scan_id']})
        self.assertEqual(result['status'],'skipped',result)
        self.assertNotIn('direction',result)
        self.assertEqual(self.ai.calls,1)

    def test_two_stage_entry_confirmation_preserves_5_10_15_minute_expiry(self):
        self.history()
        self.ingest(*[{'type':'candle','symbol':'PAIR_otc','period':900,**r} for r in fixture(900)])
        for expiry in (5,10,15):
            with self.subTest(expiry=expiry):
                self.ledger.settings(101,{'expiry':expiry})
                self.now+=11
                pending=self.ledger.start_scan(101)
                self.assertEqual(pending['phase'],'entry_quote')
                self.assertNotIn('direction',pending)
                self.now+=1;self.ingest(self.quote())
                result=self.ledger.poll_scan(101,{'scan_id':pending['scan_id']})
                self.assertEqual(result['status'],'candidate',result)
                self.assertEqual(result['expiry'],expiry)
                self.assertEqual(result['expires_at'],self.now+expiry*60)
                self.assertEqual(result['quote_time'],self.now)
        self.assertEqual(self.ai.calls,3)

    def test_verified_aggregated_periods_are_not_downloaded_again_as_full_native_histories(self):
        self.history()
        self.ledger.settings(101, {'expiry':15})
        with self.ledger.db() as db:
            from market_analysis import frame_data
            _,assets,histories=self.ledger._market(db,101)
        periods,_,checks=frame_data(histories,'PAIR_otc',15,self.now)
        self.assertEqual(periods,(120,600));self.assertTrue(all(c.get('history_ready') for c in checks))
        plan=self.ledger.bridge_plan(self.token,{'code':'ready'})['plan']
        self.assertEqual(plan['pairs'][0]['history'],[])
        # A newly closed M2 still needs an update; complete source history
        # permits a small refresh, rather than a redundant 160-bar download.
        self.now+=120
        plan=self.ledger.bridge_plan(self.token,{'code':'ready'})['plan']
        self.assertIn({'period':120,'bars':5},plan['pairs'][0]['history'])
        self.assertNotIn({'period':120,'bars':160},plan['pairs'][0]['history'])


    def test_all_39_closed_graphs_reach_model_even_with_only_one_current_quote(self):
        self.history()
        with self.ledger.db() as db:prefs,assets,histories=self.ledger._market(db,101)
        rows=[{**assets[0],'symbol':f'PAIR{i}_otc','quote_time':self.now if i==0 else self.now-30} for i in range(39)]
        bars={(a['symbol'],period):histories[('PAIR_otc',period)] for a in rows for period in (60,300)}
        value,_=snapshot(rows,bars,prefs,self.now)
        self.assertEqual(sum(p['data_ready'] for p in value['pairs']),1)
        self.assertEqual(sum(p['analysis_ready'] for p in value['pairs']),39)
        self.assertTrue(all('recent_ohlc' in f for p in value['pairs'] for f in p['frames']))
        self.assertTrue(all(not q['entry_ready'] for q in value['qualified'] if q['symbol']!='PAIR0_otc'))
        calls=[]
        def opener(request,timeout):
            body=json.loads(request.data);v=json.loads(body['input'][1]['content']);calls.append(v)
            self.assertTrue(body['stream']);self.assertLessEqual(len(v['pairs']),64)
            self.assertLessEqual(len(json.dumps(v,ensure_ascii=False,separators=(',',':')).encode()),48000)
            d=FixtureAI().submit(1,v).result()['decision'];d={k:d[k] for k in ('action','symbol','direction','strategy','summary','risk','evidence')}
            return io.BytesIO(json.dumps({'status':'completed','output':[{'content':[{'type':'output_text','text':json.dumps(d)}]}]}).encode())
        save(self.tmp.name,'lr_fixture_small_payload_for_39_pairs',allowed_users=[101],provider='lunaroute')
        ai=OpenAIAnalyzer(self.tmp.name,opener=opener);self.addCleanup(ai.executor.shutdown)
        result=ai.review(value)
        self.assertEqual(result['reviewed_pairs'],39)
        self.assertEqual({p['symbol'] for v in calls for p in v['pairs']},{p['symbol'] for p in value['pairs']})
        self.assertEqual(result['action'],'enter')


if __name__ == '__main__': unittest.main()
