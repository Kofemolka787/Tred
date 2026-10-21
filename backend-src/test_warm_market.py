"""Warm collection and delayed entry contracts, without a real broker/model."""
from concurrent.futures import Future
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from ai_market import snapshot
from entry_validation import revalidate_early
from test_active_signals import active_market
from test_market_analysis import fixture
from test_support import FixtureAI
from trading_core import Ledger,DomainError

NOW=1800000000
FEED='11111111-1111-4111-8111-111111111111'


class WarmMarketTests(unittest.TestCase):
    def setUp(self):
        self.tmp=self.enterContext(tempfile.TemporaryDirectory());self.now=float(NOW);self.ai=FixtureAI()
        self.ledger=Ledger(Path(self.tmp)/'db.sqlite3',clock=lambda:self.now,ai=self.ai)
        self.token=self.ledger.claim(self.ledger.pair(101)['code'])['token']
        self.ledger.settings(101,{'market':'otc','allow_otc':True})
        self.asset={'type':'asset','symbol':'PAIR_otc','market':'otc','payout':.92,'is_open':True,
                    'expiries':[5,10,15],'time':self.now,'feed_id':FEED,'history_scoped':True}
        self.ingest(self.asset,{**self.asset,'symbol':'OTHER_otc'})

    def ingest(self,*events):self.ledger.ingest(self.token,{'events':list(events)})
    def plan(self):return self.ledger.bridge_plan(self.token,{'code':'ready'})['plan']

    def test_warms_before_any_request_and_never_calls_ai(self):
        plan=self.plan()
        self.assertTrue(plan['scanner_active']);self.assertIsNone(plan['scan_id'])
        self.assertEqual(plan['warm_total'],2)
        self.assertEqual([h['period'] for h in plan['pairs'][0]['history']],[60,300])
        self.assertEqual([h['period'] for h in plan['pairs'][0]['warm_history']],[900])
        self.assertEqual(self.ai.calls,0)

    def test_manual_priority_keeps_the_other_charts_warm(self):
        self.ledger.settings(101,{'selection':'manual','symbol':'OTHER_otc'})
        plan=self.plan();self.assertEqual(plan['total'],1);self.assertEqual(plan['warm_total'],2)
        self.assertEqual(len(plan['pairs']),2)
        selected=next(p for p in plan['pairs'] if p['symbol']=='OTHER_otc')
        self.assertTrue(selected['priority'])
        self.assertFalse(next(p for p in plan['pairs'] if p['symbol']=='PAIR_otc')['priority'])
        self.assertEqual(self.ai.calls,0)

    def test_repeated_requests_and_duration_change_reuse_the_same_history(self):
        for pair in ('PAIR_otc','OTHER_otc'):
            self.ingest({'type':'quote','symbol':pair,'price':106.3,'time':self.now,'feed_id':FEED})
            for period in (60,300,900):
                self.ingest(*[{'type':'candle','symbol':pair,'period':period,'feed_id':FEED,**r} for r in fixture(period)])
        ids=[]
        for expiry in (5,10,15):
            self.ledger.settings(101,{'expiry':expiry})
            result=self.ledger.start_scan(101);self.assertEqual(result['status'],'candidate',result)
            ids.append(result['scan_id'])
            plan=self.plan();self.assertTrue(plan['scanner_active'])
            self.assertTrue(all(not p['history'] and not p['warm_history'] for p in plan['pairs']))
        self.assertEqual(len(set(ids)),3);self.assertEqual(self.ai.calls,3)
        with self.ledger.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM candles').fetchone()[0],960)
        self.assertEqual(self.ledger.state({'id':202})['feed']['catalog'],0)


class EarlyContractTests(unittest.TestCase):
    def contract(self,kind='trend_continuation',direction='up'):
        asset,histories,prefs=active_market(kind,direction=direction)
        asset.update(catalog_feed=FEED,quote_feed=FEED,feed_connected=True,history_scoped=True)
        _,candidates=snapshot([asset],histories,prefs,NOW)
        prior=next(c for c in candidates if c['strategy']==kind and c['direction']==direction)
        # A real next closed bar is added; prior history is never shifted.
        price=prior['reference_price'];later=deepcopy(histories)
        later[('PAIR_otc',60)].append({'time':NOW,'open':price,'high':price+.04,'low':price-.04,'close':price})
        return prior,{**asset,'quote_time':NOW+70},later,prefs

    def test_unchanged_reviewed_trigger_survives_one_new_bar_in_both_directions(self):
        for direction in ('up','down'):
            prior,asset,bars,prefs=self.contract(direction=direction)
            checked,reason=revalidate_early(prior,asset,bars,prefs,NOW+70)
            self.assertIsNone(reason);self.assertIsNotNone(checked)
            self.assertEqual(checked['setup_id'],prior['setup_id'])
            self.assertEqual(checked['bar_time'],prior['bar_time'])
            self.assertEqual(checked['evaluation_bar_time'],NOW)

    def test_missing_new_history_quote_zone_source_and_modified_anchor_are_rejected(self):
        prior,asset,bars,prefs=self.contract()
        for changes,code in [({'quote_time':NOW},'quote_missing'),({'price':200},'price_left_zone'),({'catalog_feed':'22222222-2222-4222-8222-222222222222'},'source_changed')]:
            self.assertEqual(revalidate_early(prior,{**asset,**changes},bars,prefs,NOW+70)[1],code)
        changed=deepcopy(bars);changed[('PAIR_otc',60)][-2]['low']-=.01
        self.assertEqual(revalidate_early(prior,asset,changed,prefs,NOW+70)[1],'anchor_changed')
        changed=deepcopy(bars);del changed[('PAIR_otc',60)][-5]
        self.assertEqual(revalidate_early(prior,asset,changed,prefs,NOW+70)[1],'history_missing')
        self.assertEqual(revalidate_early(prior,asset,bars,prefs,NOW+121)[1],'setup_expired')

    def test_a_broken_level_cannot_recover_into_the_old_contract(self):
        prior,asset,bars,prefs=self.contract('momentum_breakout')
        level=prior['events'][0]['level'];r=bars[('PAIR_otc',60)][-1]
        r.update(open=asset['price'],close=level-.2*prior['atr'],high=asset['price']+.05,low=level-.3*prior['atr'])
        checked,_=revalidate_early(prior,asset,bars,prefs,NOW+70)
        self.assertIsNone(checked)

    def test_real_scan_result_and_watch_use_the_same_reviewed_contract_after_delay(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[float(NOW)]
            class Delayed(FixtureAI):
                def submit(self,job,value):
                    future=super().submit(job,value);self.result=future.result();self.tasks[job]=Future();return self.tasks[job]
            engine=Delayed();ledger=Ledger(Path(tmp)/'db.sqlite3',clock=lambda:now[0],ai=engine)
            token=ledger.claim(ledger.pair(101)['code'])['token']
            ledger.settings(101,{'market':'otc','allow_otc':True,'signal_mode':'active','selection':'manual','symbol':'PAIR_otc'})
            asset,bars,prefs=active_market('trend_continuation')
            events=[{'type':'asset',**{k:asset[k] for k in ('symbol','market','is_open','payout','expiries')},'time':now[0],'feed_id':FEED,'history_scoped':True},
                    {'type':'quote','symbol':'PAIR_otc','price':asset['price'],'time':now[0],'feed_id':FEED},
                    {'type':'account','mode':'demo','account_id':'456','currency':'USD','balance':'105.20','time':now[0]}]
            events += [{'type':'candle','symbol':s,'period':p,'feed_id':FEED,**r} for (s,p),rows in bars.items() for r in rows]
            for i in range(0,len(events),200):ledger.ingest(token,{'events':events[i:i+200]})
            job=ledger.start_scan(101);self.assertEqual(job['phase'],'ai')
            now[0]+=70;price=asset['price']
            ledger.ingest(token,{'events':[{'type':'candle','symbol':'PAIR_otc','period':60,'time':NOW,'open':price,'high':price+.04,'low':price-.04,'close':price,'feed_id':FEED},
                                           {'type':'quote','symbol':'PAIR_otc','price':price,'time':now[0],'feed_id':FEED}]})
            engine.tasks[job['scan_id']].set_result(engine.result)
            result=ledger.poll_scan(101,{'scan_id':job['scan_id']})
            self.assertEqual(result['status'],'candidate',result)
            self.assertEqual(result['bar_time'],NOW-60)
            self.assertEqual(result['evaluation_bar_time'],NOW)
            self.assertEqual(engine.calls,1)
            sid=ledger.watch(101,{'analysis_id':result['analysis_id'],'mode':'demo'})
            self.assertIsInstance(sid,int)
            self.assertEqual(ledger.state({'id':101})['accounts'][0]['balance'],'105.20')
            self.assertEqual(ledger.state({'id':202})['signal_sessions'],[])

