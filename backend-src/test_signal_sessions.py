"""Real analysis -> observed quote tracking -> user outcome, with no orders."""
import json
from pathlib import Path
import tempfile
import unittest

from test_market_analysis import fixture
from trading_core import DomainError, Ledger


class SignalSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.now=1800000000.0
        self.ledger=Ledger(Path(self.tmp.name)/"db.sqlite3",clock=lambda:self.now)
        self.user={"id":101}
        self.token=self.ledger.claim(self.ledger.pair(101)["code"])["token"]
        self.asset={"type":"asset","symbol":"EURUSD_otc","market":"otc","is_open":True,"payout":.92,"expiries":[5,10,15],"time":self.now}
        self.ledger.settings(101,{"market":"otc","allow_otc":True})
        events=[self.asset,{"type":"account","account_id":"456","mode":"real","currency":"USD","balance":"94.00","time":self.now},{"type":"quote","symbol":"EURUSD_otc","price":106.30,"time":self.now}]
        self.ingest(*events)
        bars=[{"type":"candle","symbol":"EURUSD_otc","period":p,**row} for p in (60,300,900) for row in fixture(p,int(self.now))]
        for i in range(0,len(bars),200):self.ingest(*bars[i:i+200])

    def ingest(self,*events):
        return self.ledger.ingest(self.token,{"events":list(events)})

    def signal(self):
        s=self.ledger.signal(101)
        self.assertEqual(s["status"],"candidate",s)
        self.assertIsNone(s["probability"])
        return s

    def test_full_lifecycle_is_persistent_idempotent_and_user_scoped(self):
        signal=self.signal()
        value={"analysis_id":signal["analysis_id"],"mode":"real"}
        sid=self.ledger.watch(101,value)
        self.assertEqual(self.ledger.watch(101,value),sid)
        self.assertEqual(self.ledger.state({"id":202})["signal_sessions"],[])
        with self.assertRaises(DomainError):self.ledger.record_result(202,{"session_id":sid,"result":"win"})
        with self.assertRaises(DomainError):self.ledger.record_result(101,{"session_id":sid,"result":"win"})
        self.now+=5
        self.ingest({"type":"quote","symbol":"EURUSD_otc","time":self.now,"price":106.33})
        session=self.ledger.state(self.user)["signal_sessions"][0]
        self.assertEqual(session["current_price"],106.33)
        self.assertEqual(session["status"],"open")
        self.assertTrue(session["live"])
        self.assertTrue(session["series"])
        self.now+=20
        self.assertFalse(self.ledger.state(self.user)["signal_sessions"][0]["live"])
        self.now+=300
        self.assertEqual(self.ledger.state(self.user)["signal_sessions"][0]["status"],"awaiting_result")
        self.ledger.record_result(101,{"session_id":sid,"result":"loss"})
        self.ledger.record_result(101,{"session_id":sid,"result":"loss"})
        with self.assertRaises(DomainError):self.ledger.record_result(101,{"session_id":sid,"result":"win"})
        reopened=Ledger(self.ledger.path,clock=lambda:self.now)
        state=reopened.state(self.user)
        self.assertEqual(state["signal_sessions"][0]["result"],"loss")
        self.assertEqual(state["signal_sessions"][0]["setup"]["evidence"],signal["evidence"])
        self.assertEqual(state["accounts"][0]["balance"],"94.00")
        self.assertEqual(state["trades"],[])
        self.assertEqual(state["goal"]["earned"],"0.00")
        self.assertEqual(state["statistics"]["signals"],0,"Self-report never becomes a broker-verified win rate")

    def test_stale_price_out_of_zone_wrong_user_and_unknown_mode_cannot_start(self):
        signal=self.signal();value={"analysis_id":signal["analysis_id"],"mode":"real"}
        for uid,changes in [(202,{}),(101,{"mode":"demo"}),(101,{"entry_price":999}),(101,{"user_id":202})]:
            with self.assertRaises(DomainError):self.ledger.watch(uid,{**value,**changes})
        self.now+=11
        with self.assertRaises(DomainError):self.ledger.watch(101,value)
        self.ingest({"type":"quote","symbol":"EURUSD_otc","time":self.now,"price":200})
        with self.assertRaises(DomainError):self.ledger.watch(101,value)
        self.now+=20
        self.ingest({"type":"quote","symbol":"EURUSD_otc","time":self.now,"price":106.30})
        with self.assertRaises(DomainError):self.ledger.watch(101,value)
        self.assertEqual(self.ledger.state(self.user)["signal_sessions"],[])

    def test_no_signal_without_history_and_no_id_from_other_user(self):
        with self.ledger.db() as db:db.execute("DELETE FROM candles WHERE user_id=101")
        result=self.ledger.signal(101)
        self.assertEqual(result["status"],"skipped")
        self.assertNotIn("direction",result)
        self.assertEqual(self.ledger.state(self.user)["feed"]["ready"],0)

    def test_payout_drop_or_new_closed_bar_invalidates_old_entry(self):
        s=self.signal();v={"analysis_id":s["analysis_id"],"mode":"real"}
        self.now+=1
        self.ingest({**self.asset,"time":self.now,"payout":.84})
        with self.assertRaises(DomainError):self.ledger.watch(101,v)
        self.now+=1
        self.ingest({**self.asset,"time":self.now})
        self.assertGreater(self.ledger.watch(101,{**v,"entry_price":106.31}),0)

    def test_new_completed_bar_invalidates_entry_within_its_window(self):
        initial=self.now
        self.now+=59
        self.ingest({**self.asset,"time":self.now},{"type":"quote","symbol":"EURUSD_otc","time":self.now,"price":106.30})
        signal=self.signal()
        self.now+=1
        self.ingest({"type":"candle","symbol":"EURUSD_otc","period":60,"time":initial,"open":106.30,"high":106.4,"low":106.2,"close":106.31})
        with self.assertRaisesRegex(DomainError,"новая свеча"):
            self.ledger.watch(101,{"analysis_id":signal["analysis_id"],"mode":"real"})

    def test_retry_cannot_change_the_original_entry(self):
        signal=self.signal();value={"analysis_id":signal["analysis_id"],"mode":"real","entry_price":106.30}
        self.ledger.watch(101,value)
        with self.assertRaises(DomainError):self.ledger.watch(101,{**value,"entry_price":106.31})
        with self.assertRaises(DomainError):self.ledger.watch(101,{**value,"mode":"demo"})

    def test_previous_expiry_settings_migrate_without_losing_pairing(self):
        with self.ledger.db() as db:
            db.execute("UPDATE profiles SET settings=? WHERE user_id=101",(json.dumps({"market":"otc","selection":"auto","symbol":None,"expiry":30,"allow_otc":True}),))
        reopened=Ledger(self.ledger.path,clock=lambda:self.now)
        state=reopened.state(self.user)
        self.assertEqual(state["preferences"]["expiry"],15)
        self.assertEqual(state["preferences"]["min_payout"],85)
        self.assertTrue(state["connection"]["authenticated"])
        self.assertEqual(state["accounts"][0]["balance"],"94.00")

    def test_settings_floor_does_not_accept_below_85_or_percent_probability(self):
        for value in (84,93,True,85.5,"85"):
            with self.assertRaises(DomainError):self.ledger.settings(101,{"min_payout":value})
        self.ledger.settings(101,{"min_payout":92})
        self.assertEqual(self.ledger.state(self.user)["preferences"]["min_payout"],92)


if __name__=="__main__":unittest.main()
