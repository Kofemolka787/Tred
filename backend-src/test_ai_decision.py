"""Provider compatibility without weakening evidence, freshness or entry gates."""
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

from ai_decision import DecisionError, decode_decision, prepare_payload
from ai_market import OpenAIAnalyzer, snapshot, validate
from ai_provider import ModelReplyError, response_text
from configure_ai import save
from test_market_analysis import fixture
from test_support import FixtureAI, wire_decision
from trading_core import Ledger


class AIDecisionTests(unittest.TestCase):
    def setUp(self):
        self.now = 1800000000.0
        asset = {'symbol':'PAIR_otc','market':'otc','is_open':True,'payout':.92,
                 'expiries':[5,10,15],'updated_at':self.now,'price':106.30,'quote_time':self.now}
        histories = {('PAIR_otc', p):fixture(p) for p in (60,300)}
        self.snapshot, _ = snapshot([asset], histories, {'expiry':5,'market':'otc','symbol':None,
                                      'allow_otc':True,'min_payout':85}, self.now)
        self.payload = prepare_payload(self.snapshot)
        self.decision = FixtureAI().submit(1, self.snapshot).result()['decision']
        self.wire = wire_decision(self.decision, self.payload)

    def decode(self, value):
        result = decode_decision(json.dumps(value), self.payload)
        validate(result, self.snapshot)
        return result

    def test_references_restore_exact_source_times_and_all_original_rows_remain(self):
        before = deepcopy(self.snapshot)
        result = self.decode(self.wire)
        self.assertEqual(result['evidence'], self.decision['evidence'])
        self.assertEqual(self.snapshot, before)
        self.assertEqual(self.payload['pairs'], self.snapshot['pairs'])
        stale = deepcopy(self.snapshot)
        stale['pairs'].append({**stale['pairs'][0], 'symbol':'STALE_otc', 'data_ready':False,'analysis_ready':False})
        prepared = prepare_payload(stale)
        self.assertFalse(any(r['symbol']=='STALE_otc' for r in prepared['evidence_refs']))
        self.assertEqual(len(prepared['pairs']), 2)

    def test_one_complete_json_fence_is_supported_but_prose_partial_or_multiple_objects_are_not(self):
        raw = json.dumps(self.wire)
        for text in (raw, '  ```json\n'+raw+'\n```  ', '```\n'+raw+'\n```'):
            value = decode_decision(text, self.payload); validate(value, self.snapshot)
        for text in ('Вот сценарий: '+raw, raw+' '+raw, raw[:-1], '<think>analysis</think>'+raw,
                     raw.replace('"action": "enter"', '"action": "wait", "action": "enter"'),
                     raw.replace('"ref": "r0"', '"ref": NaN'), '['+raw+']'):
            with self.subTest(text=text[:35]), self.assertRaises(DecisionError):
                self.decode_text(text)

    def decode_text(self, text):
        result = decode_decision(text, self.payload); validate(result, self.snapshot)
        return result

    def test_invented_cross_pair_stale_and_duplicate_refs_cannot_become_an_entry(self):
        wrong = deepcopy(self.wire); wrong['evidence'][0]['ref']='r999999'
        duplicate = deepcopy(self.wire); duplicate['evidence']=[duplicate['evidence'][0]]*2
        stale = deepcopy(self.snapshot); stale['pairs'][0].update(data_ready=False,analysis_ready=False)
        with self.assertRaises(DecisionError) as caught:
            validate(wire_decision(self.decision, self.snapshot), stale)
        self.assertEqual(caught.exception.code, 'decision_reference')
        other = deepcopy(self.payload['evidence_refs'][0]); other.update(ref='other', symbol='OTHER_otc')
        self.payload['evidence_refs'].append(other)
        cross = deepcopy(self.wire); cross['evidence'][0]['ref']='other'
        for value in (wrong, duplicate, cross, {**self.wire,'direction':'down'}, {**self.wire,'symbol':'NEW_otc'}):
            with self.subTest(value=value['symbol']),self.assertRaises(DecisionError): self.decode(value)

    def test_ref_shapes_types_and_explanations_fail_with_allowlisted_diagnostics(self):
        cases = [({'evidence':[{'ref':[], 'explanation':'why'}]}, 'decision_reference'),
                 ({'evidence':[{'ref':'r0', 'explanation':'why', 'time':self.now}]}, 'decision_fields'),
                 ({'evidence':{}}, 'decision_evidence'),
                 ({'risk':''}, 'decision_explanation'),
                 ({'symbol':[]}, 'decision_fields'),
                 ({'action':'wait'}, 'decision_wait')]
        for changes, code in cases:
            with self.subTest(code=code),self.assertRaises(DecisionError) as caught:
                self.decode({**self.wire, **changes})
            self.assertEqual(caught.exception.code, code)

    def test_wait_needs_no_fabricated_evidence_and_is_not_turned_into_entry(self):
        wait = {**self.wire,'action':'wait','symbol':'','direction':'none','strategy':'','evidence':[],
                'summary':'Ни одна реакция на зону не убедительна.'}
        self.assertEqual(self.decode(wait)['action'], 'wait')

    def test_all_three_expiries_keep_exact_references_on_their_requested_periods(self):
        for expiry, periods in ((5,(60,300)), (10,(60,300)), (15,(300,900))):
            asset={'symbol':'PAIR_otc','market':'otc','is_open':True,'payout':.92,'expiries':[5,10,15],
                   'updated_at':self.now,'price':106.30,'quote_time':self.now}
            value, qualified=snapshot([asset], {('PAIR_otc',p):fixture(p) for p in periods},
                {'expiry':expiry,'market':'otc','symbol':None,'allow_otc':True,'min_payout':85},self.now)
            self.assertTrue(qualified)
            payload=prepare_payload(value)
            decision=FixtureAI().submit(1,value).result()['decision']
            result=decode_decision(json.dumps(wire_decision(decision,payload)),payload)
            validate(result,value)
            self.assertEqual({e['period'] for e in result['evidence']},set(periods))

    def test_provider_status_empty_refusal_and_bad_envelope_have_distinct_safe_codes(self):
        secret = 'fixture-private-response-never-publish'
        cases = [({'status':'incomplete','output_text':secret}, 'model_incomplete'),
                 ({'status':'completed','output':[]}, 'model_empty'),
                 ({'status':'completed','output':[{'type':'message','content':[{'type':'refusal','refusal':secret}]}]}, 'model_refusal'),
                 ({'status':'completed','output':[{'content':secret}]}, 'model_format')]
        for reply, code in cases:
            with self.subTest(code=code),self.assertRaises(ModelReplyError) as caught:response_text(reply)
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn(secret,str(caught.exception))

    def test_actual_client_sends_refs_and_accepts_fenced_completed_reply_without_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            save(tmp,'lr_fixture_contract_key_not_real','deepseek-4.1-flash',allowed_users=[101],provider='lunaroute')
            requests=[]
            def opener(request, timeout):
                body=json.loads(request.data);requests.append(body)
                payload=json.loads(body['input'][1]['content'])
                decision=wire_decision(self.decision,payload)
                reply={'status':'completed','output':[{'content':[{'type':'output_text','text':'```json\n'+json.dumps(decision)+'\n```'}]}]}
                return io.BytesIO(json.dumps(reply).encode())
            analyzer=OpenAIAnalyzer(tmp,opener=opener);self.addCleanup(analyzer.executor.shutdown)
            result=analyzer.review(self.snapshot)
            self.assertEqual(result['action'],'enter');self.assertEqual(len(requests),1)
            self.assertEqual(result['evidence'],self.decision['evidence'])

    def test_sanitized_specific_failure_reaches_signed_owner_scan_and_no_auto_paid_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            key='lr_fixture_contract_error_not_real'
            save(tmp,key,'deepseek-4.1-flash',allowed_users=[101],provider='lunaroute')
            requests=[]
            def opener(request,timeout):
                requests.append(request)
                reply={'status':'completed','output':[{'content':[{'type':'output_text','text':'fixture-private-malformed-model-response'}]}]}
                return io.BytesIO(json.dumps(reply).encode())
            analyzer=OpenAIAnalyzer(tmp,opener=opener);self.addCleanup(analyzer.executor.shutdown)
            ledger=Ledger(Path(tmp)/'ledger.sqlite3',clock=lambda:self.now,ai=analyzer)
            token=ledger.claim(ledger.pair(101)['code'])['token']
            ledger.settings(101,{'market':'otc','allow_otc':True})
            asset=self.snapshot['pairs'][0]
            events=[{'type':'asset','symbol':asset['symbol'],'market':'otc','is_open':True,'payout':.92,
                     'expiries':[5,10,15],'time':self.now},
                    {'type':'quote','symbol':asset['symbol'],'price':asset['price'],'time':self.now}]
            events += [{'type':'candle','symbol':asset['symbol'],'period':p,**r} for p in (60,300) for r in fixture(p)]
            for offset in range(0,len(events),250):ledger.ingest(token,{'events':events[offset:offset+250]})
            result=ledger.start_scan(101)
            analyzer.get(result['scan_id']).exception(timeout=2)
            result=ledger.poll_scan(101,{'scan_id':result['scan_id']})
            self.assertEqual(result['status'],'ai_unavailable');self.assertEqual(result['ai_error_code'],'decision_json')
            self.assertNotIn(key,json.dumps(result));self.assertNotIn('fixture-private',json.dumps(result))
            self.assertEqual(len(requests),1)
            self.assertEqual(ledger.poll_scan(101,{'scan_id':result['scan_id']}),result)
            self.assertEqual(len(requests),1)


if __name__=='__main__':unittest.main()
