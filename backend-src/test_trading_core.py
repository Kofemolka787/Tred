import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from trading_core import DomainError, Ledger, money


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = 1800000000.0
        self.path = Path(self.tmp.name)/"ledger.sqlite3"
        self.ledger = Ledger(self.path,clock=lambda:self.now)
        self.user = {"id":101,"first_name":"Первый"}
        self.token = self.ledger.claim(self.ledger.pair(101)["code"])["token"]

    def event(self, **changes):
        return {"type":"account","mode":"real","account_id":"456","currency":"USD","balance":"100.00","time":self.now,**changes}

    def trade(self, **changes):
        return {"type":"trade","mode":"real","account_id":"456","id":"trade-1","symbol":"EURUSD","direction":"up","stake":"10.00","opened_at":self.now-300,"expires_at":self.now,"closed_at":self.now,"pnl":"8.00","status":"win","time":self.now,**changes}

    def ingest(self,*events):
        return self.ledger.ingest(self.token,{"events":list(events)})

    def state(self):
        return self.ledger.state(self.user)

    def insert_paper(self, *, stake=100, pnl=None, status="open", expiry=None):
        with self.ledger.db() as db:
            db.execute("INSERT INTO paper_trades(user_id,symbol,direction,stake,payout,opened_at,expires_at,entry_price,pnl,status) VALUES (101,'EURUSD','up',?,0.8,?,?,1.1,?,?)",(stake,self.now-300,self.now if expiry is None else expiry,pnl,status))

    def test_money_rejects_nonfinite_boolean_fractional_cents_and_negative_positive(self):
        self.assertEqual(money("12.34"),1234)
        self.assertEqual(money("0.10"),10)
        for value in (True,"nan","inf","1e999999999999","1e-999999999999","1.001",{},None):
            with self.assertRaises(DomainError): money(value)
        with self.assertRaises(DomainError): money("-1",positive=True)

    def test_signed_identity_owns_only_its_data(self):
        self.ingest(self.event(),self.trade())
        first = self.state()
        second = self.ledger.state({"id":202})
        self.assertEqual(first["accounts"][0]["balance"],"100.00")
        self.assertEqual(first["trades"][0]["id"] if "id" in first["trades"][0] else first["trades"][0]["trade_id"],"trade-1")
        self.assertEqual(second["accounts"],[])
        self.assertEqual(second["trades"],[])
        self.assertEqual(second["goal"]["earned"],"0.00")

    def test_pairing_code_is_one_use_and_replacement_revokes_old_token(self):
        pair = self.ledger.pair(101)
        new = self.ledger.claim(pair["code"])["token"]
        with self.assertRaises(DomainError): self.ledger.claim(pair["code"])
        with self.assertRaises(DomainError): self.ingest(self.event())
        self.ledger.ingest(new,{"events":[self.event()]})
        with self.ledger.db() as db:
            row = db.execute("SELECT * FROM pairing").fetchone()
            self.assertNotIn(new,tuple(row))

    def test_expired_pairing_code_and_unlink_fail_closed(self):
        pair = self.ledger.pair(101)
        self.now += 301
        with self.assertRaises(DomainError): self.ledger.claim(pair["code"])
        self.ledger.unlink(101)
        with self.assertRaises(DomainError): self.ingest(self.event())

    def test_duplicate_trade_and_late_open_do_not_change_settlement(self):
        self.ingest(self.event(),self.trade(),self.trade())
        self.now += 1
        self.ingest(self.trade(status="open",pnl=None,closed_at=None))
        s = self.state()
        self.assertEqual(s["statistics"]["wins"],1)
        self.assertEqual(s["goal"]["earned"],"8.00")
        self.assertEqual(s["trades"][0]["status"],"win")

    def test_deposit_never_becomes_goal_profit_and_demo_is_separate(self):
        self.ingest(self.event(),self.trade())
        self.now += 1
        self.ingest(self.event(balance="1000.00"),self.trade(mode="demo",id="demo"))
        self.assertEqual(self.state()["goal"]["earned"],"8.00")
        self.assertAlmostEqual(self.state()["goal"]["progress"],8/900)

    def test_demo_balance_does_not_appear_as_a_real_goal(self):
        self.ingest(self.event(mode="demo"))
        self.assertFalse(self.state()["goal"]["available"])

    def test_stale_account_update_cannot_overwrite_new_balance(self):
        self.ingest(self.event(balance="110.00"))
        self.ingest(self.event(balance="80.00",time=self.now-1))
        self.assertEqual(self.state()["accounts"][0]["balance"],"110.00")
        self.now += 16
        self.assertFalse(self.state()["accounts"][0]["live"])

    def test_batch_is_atomic_and_rejects_credentials_or_other_user_fields(self):
        for extra in ({"session":"never-export"},{"user_id":202}):
            with self.assertRaises(DomainError): self.ingest(self.event(),self.event(**extra))
        self.assertEqual(self.state()["accounts"],[])

    def test_switching_account_requires_unlink(self):
        self.ingest(self.event())
        with self.assertRaises(DomainError): self.ingest(self.event(account_id="999"))
        self.ledger.unlink(101)
        token = self.ledger.claim(self.ledger.pair(101)["code"])["token"]
        self.ledger.ingest(token,{"events":[self.event(account_id="999")]})
        self.assertEqual(self.state()["accounts"][0]["id"],"999")

    def test_daily_stop_is_persistent_cannot_be_raised_same_day(self):
        self.insert_paper(stake=1000,pnl=-1000,status="loss")
        self.assertTrue(self.state()["risk"]["locked"])
        reopened = Ledger(self.path,clock=lambda:self.now)
        with self.assertRaises(DomainError): reopened.configure_risk(101,{"daily_loss":"100.00"})
        with self.assertRaises(DomainError): reopened.toggle(101,True)
        self.now = (int(self.now//86400)+1)*86400+1
        self.assertFalse(reopened.state(self.user)["risk"]["locked"])
        self.assertFalse(reopened.state(self.user)["risk"]["running"])

    def test_open_stakes_are_reserved_before_new_entry(self):
        self.ledger.configure_risk(101,{"max_open":3,"stake":"4.00"})
        self.insert_paper(stake=700,expiry=self.now+300)
        with self.ledger.db() as db:
            guard = self.ledger._risk(db,101,additional=400)
        self.assertIn("лимит",guard["reason"])
        self.assertEqual(guard["reserved"],"7.00")

    def test_daily_profit_goal_also_stops_simulation(self):
        self.insert_paper(stake=1000,pnl=2000,status="win")
        state = self.state()["risk"]
        self.assertTrue(state["locked"])
        self.assertIn("цель",state["reason"])

    def test_pausing_does_not_allow_loosening_risk_after_first_trade(self):
        self.insert_paper(stake=100,pnl=-100,status="loss")
        self.ledger.toggle(101,False)
        with self.assertRaises(DomainError): self.ledger.configure_risk(101,{"daily_loss":"20.00"})
        self.ledger.configure_risk(101,{"daily_loss":"9.00"})
        self.assertEqual(self.state()["risk"]["config"]["daily_loss"],"9.00")

    def test_no_martingale_or_implicit_real_execution(self):
        with self.assertRaises(DomainError): self.ledger.configure_risk(101,{"max_overlays":2})
        with self.assertRaises(DomainError): self.ledger.configure_risk(101,{"real_orders_supported":True})
        self.assertFalse(self.state()["risk"]["real_orders_supported"])
        with self.assertRaises(DomainError): self.ledger.toggle(101,True)

    def test_valid_simulation_settles_once_and_does_not_create_real_trade(self):
        candidate = {"status":"candidate","symbol":"EURUSD","direction":"up","price":1.1,"payout":0.8,"expiry":5,"timeframes":[60,300]}
        self.ingest({"type":"asset","symbol":"EURUSD","market":"normal","is_open":True,"payout":.8,"expiries":[5],"time":self.now},{"type":"quote","symbol":"EURUSD","price":1.1,"time":self.now})
        with patch.object(self.ledger,"_candidate",return_value=candidate):
            self.ledger.toggle(101,True)
            self.ledger.paper_step(101)
            self.ledger.paper_step(101)
        self.assertEqual(len(self.state()["paper_trades"]),1)
        self.assertEqual(self.state()["statistics"]["signals"],0)
        self.now += 300
        self.ingest({"type":"asset","symbol":"EURUSD","market":"normal","is_open":True,"payout":.8,"expiries":[5],"time":self.now},{"type":"quote","symbol":"EURUSD","price":1.2,"time":self.now})
        self.ledger.paper_step(101)
        self.ledger.paper_step(101)
        s = self.state()
        self.assertEqual(s["paper_trades"][0]["status"],"win")
        self.assertEqual(s["risk"]["balance"],"100.80")
        self.assertEqual(s["trades"],[])

    def test_missing_expiry_quote_is_unknown_never_a_win_and_reservation_remains(self):
        self.insert_paper()
        self.now += 11
        self.ledger.paper_step(101)
        state = self.state()
        self.assertEqual(state["paper_trades"][0]["status"],"unresolved")
        self.assertEqual(state["risk"]["reserved"],"1.00")
        self.assertTrue(state["risk"]["locked"])
        self.assertEqual(state["risk"]["balance"],"100.00")

    def test_no_data_means_no_direction_and_no_probability(self):
        result = self.ledger.signal(101)
        self.assertEqual(result["status"],"skipped")
        self.assertNotIn("direction",result)
        self.assertNotIn("probability",result)


if __name__ == "__main__": unittest.main()
