import tempfile
from pathlib import Path
import unittest

from trading_core import Ledger,DomainError
from test_market_analysis import fixture
from market_analysis import rank,history

FEED="11111111-1111-4111-8111-111111111111"
OTHER="22222222-2222-4222-8222-222222222222"


class MarketFeedTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.now=1800000000.0
        self.ledger=Ledger(Path(self.tmp.name)/'db.sqlite3',clock=lambda:self.now)
        self.token=self.ledger.claim(self.ledger.pair(101)['code'])['token']
        self.ledger.settings(101,{'market':'otc','allow_otc':True})
        self.asset={'type':'asset','symbol':'PAIR_otc','market':'otc','is_open':True,'payout':.92,'expiries':[5,10,15],'time':self.now,'feed_id':FEED}
        self.ingest(self.asset,self.quote())

    def ingest(self,*events):return self.ledger.ingest(self.token,{'events':list(events)})
    def quote(self,**changes):return {'type':'quote','symbol':'PAIR_otc','price':106.30,'time':self.now,'feed_id':FEED,**changes}
    def state(self):return self.ledger.state({'id':101})

    def test_catalog_does_not_expire_after_60s_in_same_live_socket_and_time_not_forged(self):
        initial=self.now;self.now+=180
        self.ingest(self.quote())
        state=self.state();self.assertEqual(state['feed']['eligible'],1)
        pair=state['feed']['pairs'][0]
        self.assertEqual(pair['catalog_age'],180)
        self.assertEqual(pair['catalog_source'],'live_socket')
        self.assertEqual(pair['stage'],'history')
        self.assertEqual(pair['bars'],[0,0])
        with self.ledger.db() as db:self.assertEqual(db.execute('SELECT updated_at FROM assets').fetchone()[0],initial)
        result=self.ledger.signal(101)
        self.assertEqual(result['status'],'skipped');self.assertIn('80',result['reason'])
        self.assertNotIn('direction',result)

    def test_legacy_snapshot_still_expires_and_no_other_user_continuity(self):
        self.now+=1
        legacy={k:v for k,v in self.asset.items() if k!='feed_id'}
        self.ingest({**legacy,'time':self.now})
        self.now+=61;self.ingest(self.quote())
        self.assertEqual(self.state()['feed']['eligible'],0)
        self.assertEqual(self.state()['feed']['pairs'][0]['stage'],'catalog')
        self.assertEqual(self.ledger.state({'id':202})['feed']['catalog'],0)

    def test_disconnect_and_new_socket_without_catalog_never_reconfirm_metadata(self):
        self.ingest({'type':'feed','feed_id':FEED,'connected':False,'time':self.now})
        self.now+=1;self.ingest(self.quote())
        self.assertEqual(self.state()['feed']['eligible'],0)
        self.ingest(self.quote(feed_id=OTHER,time=self.now+.01))
        self.assertEqual(self.state()['feed']['eligible'],0)
        self.now+=1;self.ingest({**self.asset,'feed_id':OTHER,'time':self.now},self.quote(feed_id=OTHER))
        self.assertEqual(self.state()['feed']['eligible'],1)
        with self.assertRaises(DomainError):self.ingest({'type':'feed','feed_id':OTHER,'connected':True,'time':self.now})

    def test_payout_closed_zero_and_expiry_changes_apply_immediately(self):
        for change,stage in [({'payout':.84},'payout'),({'is_open':False,'payout':0},'closed'),({'expiries':[1,2]},'expiry')]:
            self.now+=1;self.ingest({**self.asset,**change,'time':self.now},self.quote())
            self.assertEqual(self.state()['feed']['pairs'][0]['stage'],stage)
            self.assertEqual(self.state()['feed']['eligible'],0)

    def test_live_ticks_create_closed_bars_only_and_broker_ohlc_wins(self):
        # Initial 106.30 point at :00, then complete coverage at five-second steps.
        initial=self.now
        for i in range(5,125,5):
            self.now=initial+i;self.ingest(self.quote(price=106.30+i*.001))
        with self.ledger.db() as db:bars=[dict(r) for r in db.execute('SELECT * FROM candles ORDER BY time')]
        self.assertEqual(len(bars),2);self.assertTrue(all(b['origin']=='ticks' for b in bars))
        self.assertEqual(bars[0]['time'],initial)
        self.assertAlmostEqual(bars[0]['close'],106.355)
        native={'type':'candle','symbol':'PAIR_otc','period':60,'time':initial,'open':106.30,'high':106.5,'low':106.2,'close':106.4}
        self.ingest(native,{**native,'origin':'ticks','close':106.3})
        with self.ledger.db() as db:b=dict(db.execute('SELECT * FROM candles ORDER BY time LIMIT 1').fetchone())
        self.assertEqual(b['origin'],'broker');self.assertEqual(b['close'],106.4)

    def test_partial_and_gapped_ticks_do_not_become_fabricated_candles(self):
        initial=self.now
        for seconds in (10,15,30,35,40,45,50,55,60):
            self.now=initial+seconds;self.ingest(self.quote())
        with self.ledger.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM candles').fetchone()[0],0)

    def test_compatible_m2_m10_history_can_be_analyzed_without_changing_expiry(self):
        histories={('PAIR_otc',p):fixture(p) for p in (120,600)}
        assets=[{**self.asset,'updated_at':self.now,'quote_time':self.now,'price':106.30,'catalog_feed':FEED,'quote_feed':FEED,'feed_connected':1}]
        for expiry in (5,10,15):
            candidates,_=rank(assets,histories,expiry=expiry,market='otc',symbol=None,allow_otc=True,now=self.now)
            self.assertTrue(candidates)
            self.assertEqual(candidates[0]['timeframes'],[120,600])

    def test_higher_frames_use_only_complete_subintervals(self):
        rows=fixture(120)
        derived=history({('PAIR_otc',120):rows},'PAIR_otc',600,self.now)
        broken=history({('PAIR_otc',120):rows[:-4]+rows[-3:]},'PAIR_otc',600,self.now)
        self.assertEqual(len(derived)-len(broken),1)
        self.assertTrue(all(r['time']+600<=self.now for r in derived))

    def test_history_format_status_is_bounded_and_user_scoped(self):
        status={'type':'history_status','symbol':'PAIR_otc','event':'loadHistoryPeriodFast','period':60,'rows':100,'bars':99,'code':'ohlc','time':self.now}
        self.ingest(status)
        self.assertEqual(self.state()['feed']['pairs'][0]['history_status'],{k:v for k,v in status.items() if k not in ('type','symbol')})
        self.assertEqual(self.ledger.state({'id':202})['feed']['pairs'],[])
        for extra in ({'session':'fixture-not-allowed'},{'event':'auth'},{'rows':60001},{'code':'raw_payload'}):
            with self.assertRaises(DomainError):self.ingest({**status,**extra})


if __name__=='__main__':unittest.main()
