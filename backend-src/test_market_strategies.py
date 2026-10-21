"""Pattern recognition and counterexamples, not measured trading performance."""
from copy import deepcopy
import io
import json
import math
from pathlib import Path
import tempfile
import unittest

from ai_decision import DecisionError, prepare_payload
from ai_market import SCHEMA, OpenAIAnalyzer, snapshot, validate
from configure_ai import save
from market_analysis import analyze, rank
from market_strategies import STRATEGIES, detect, ema, evaluate, frame
from test_market_analysis import fixture
from test_support import FixtureAI
from trading_core import Ledger

NOW = 1800000000


def pattern(kind, period=60, direction="up"):
    rows = []
    for i in range(160):
        close = 100+i*.04 if kind == "ema_pullback" else 100+math.sin(i*math.pi/10-.3*math.pi)
        rows.append({"time": NOW-(160-i)*period, "open": close-.06, "close": close,
                     "high": close+(.18 if kind == "ema_pullback" else .4),
                     "low": close-(.2 if kind == "ema_pullback" else .46)})
    updates = {
        "ema_pullback": [(106.22,106.30,105.92,106.0), (106.0,106.05,105.83,105.90), (105.90,106.18,105.85,106.12)],
        "range_rebound": [(99.0,99.45,98.60,99.15)],
        "false_breakout": [(98.6,99.2,98.2,99.05)],
        "breakout_retest": [(100.9,101.2,100.8,101.1), (101.1,102.2,101.0,102.0),
                            (102.0,102.1,101.6,101.8), (101.8,101.9,101.2,101.45), (101.45,101.95,101.35,101.8)],
    }[kind]
    for row, values in zip(rows[-len(updates):], updates):
        row.update(zip(("open", "high", "low", "close"), values))
    if direction == "down":
        for row in rows:
            o, h, l, c = (row[k] for k in ("open", "high", "low", "close"))
            row.update(open=220-o, high=220-l, low=220-h, close=220-c)
    return rows


def market(kind, expiry=5, direction="up"):
    periods = (300,900) if expiry == 15 else (60,300)
    rows = pattern(kind, periods[0], direction)
    context_kind = "ema_pullback" if kind == "ema_pullback" else "range_rebound"
    histories = {("PAIR_otc",periods[0]):rows, ("PAIR_otc",periods[1]):pattern(context_kind, periods[1], direction)}
    asset = {"symbol":"PAIR_otc", "market":"otc", "is_open":True, "payout":.92,
             "updated_at":NOW, "quote_time":NOW, "price":rows[-1]["close"], "expiries":[5,10,15]}
    prefs = {"expiry":expiry, "market":"otc", "symbol":None, "allow_otc":True, "min_payout":85}
    return asset, histories, prefs


class StrategyRecognitionTests(unittest.TestCase):
    def test_each_new_family_is_recognized_in_both_directions_on_all_expiries(self):
        for kind in (s["id"] for s in STRATEGIES if s["id"] != "smart_money"):
            for direction in ("up", "down"):
                for expiry in (5,10,15):
                    with self.subTest(strategy=kind, direction=direction, expiry=expiry):
                        asset, histories, prefs = market(kind, expiry, direction)
                        data, candidates = snapshot([asset], histories, prefs, NOW)
                        chosen = next((c for c in candidates if c["strategy"] == kind and c["direction"] == direction), None)
                        self.assertIsNotNone(chosen, [(c["strategy"],c["direction"]) for c in candidates])
                        self.assertTrue(data["pairs"][0]["data_ready"])
                        self.assertEqual(chosen["strategy_name"], next(s["name"] for s in STRATEGIES if s["id"] == kind))
                        self.assertTrue(chosen["strategy_risk"])
                        self.assertNotIn("probability", chosen)
                        self.assertTrue(chosen["invalidation"] < asset["price"] if direction == "up" else chosen["invalidation"] > asset["price"])

    def test_ema_entry_does_not_require_smc_pivots_or_order_blocks(self):
        asset, histories, prefs = market("ema_pullback")
        self.assertEqual(analyze(histories[("PAIR_otc",60)],60,NOW)["status"], "skipped")
        data, candidates = snapshot([asset],histories,prefs,NOW)
        self.assertTrue(any(c["strategy"] == "ema_pullback" for c in candidates))
        self.assertEqual(data["pairs"][0]["frames"][0]["smc_status"], "skipped")
        self.assertEqual(data["pairs"][0]["frames"][0]["status"], "observed")
        self.assertTrue(prepare_payload(data)["evidence_refs"])

    def test_range_entry_does_not_require_higher_frame_trend(self):
        asset, histories, prefs = market("range_rebound")
        data, candidates = snapshot([asset],histories,prefs,NOW)
        self.assertTrue(all(f["regime"] == "range" for f in data["pairs"][0]["frames"]))
        self.assertTrue(any(c["strategy"] == "range_rebound" for c in candidates))
        self.assertFalse(any(c["strategy"] == "ema_pullback" for c in candidates))

    def test_contradictory_higher_trend_rejects_continuation_and_range_entries(self):
        for kind in ("ema_pullback", "range_rebound", "breakout_retest", "false_breakout"):
            with self.subTest(kind=kind):
                asset, histories, prefs = market(kind)
                histories[("PAIR_otc",300)] = pattern("ema_pullback",300,"down")
                _, candidates = snapshot([asset],histories,prefs,NOW)
                self.assertFalse(any(c["strategy"] == kind and c["direction"] == "up" for c in candidates))

    def test_no_level_no_bounce_or_false_breakout_and_no_retest_no_breakout_entry(self):
        rows = pattern("ema_pullback")
        self.assertFalse(any(s["strategy"] in ("range_rebound", "false_breakout", "breakout_retest") for s in detect(frame(rows,60,NOW))))
        rows = pattern("breakout_retest")
        rows[-1].update(open=102.1, low=102.0, high=102.5, close=102.4)
        self.assertFalse(any(s["strategy"] == "breakout_retest" for s in detect(frame(rows,60,NOW))))
        rows = pattern("false_breakout")
        rows[-1].update(open=98.6, low=98.2, high=98.7, close=98.3)
        self.assertFalse(any(s["strategy"] == "false_breakout" and s["direction"] == "up" for s in detect(frame(rows,60,NOW))))

    def test_first_touch_cannot_create_a_level_for_itself(self):
        rows = pattern("false_breakout")
        for i, row in enumerate(rows[:-3]):
            close = 100+i*.015
            row.update(open=close-.02, close=close, low=close-.1, high=close+.1)
        self.assertFalse(any(s["strategy"] == "false_breakout" for s in detect(frame(rows,60,NOW))))

    def test_indicators_are_calculated_not_fabricated_probabilities(self):
        self.assertEqual(ema([1,2,3,4,5],3),[None,None,2,3,4])
        rows = pattern("range_rebound")
        for r in rows:r.update(open=100,close=100,high=100.2,low=99.8)
        f = frame(rows,60,NOW)
        self.assertEqual(f["indicators"]["rsi14"],50)
        self.assertEqual(f["indicators"]["macd_histogram"],0)
        self.assertEqual(f["indicators"]["bollinger20"],{"middle":100,"upper":100,"lower":100})
        self.assertEqual(detect(f),[])

    def test_unfinished_future_missing_corrupt_stale_and_late_data_never_force_entries(self):
        asset, histories, prefs = market("range_rebound")
        before = snapshot([asset],histories,prefs,NOW)
        for period in (60,300):
            histories[("PAIR_otc",period)].append({"time":NOW,"open":1,"close":900,"high":1000,"low":.1})
        self.assertEqual(snapshot([asset],histories,prefs,NOW),before)
        for change in ("gap","corrupt","few"):
            bad=deepcopy(histories)
            if change=="gap":del bad[("PAIR_otc",60)][-8]
            elif change=="corrupt":bad[("PAIR_otc",60)][-8]["low"]=99999
            else:bad={key:rows[-79:] for key,rows in bad.items()}
            self.assertEqual(snapshot([asset],bad,prefs,NOW)[1],[],change)
        quote_wait=snapshot([{**asset,'quote_time':NOW-11}],histories,prefs,NOW)[1]
        self.assertTrue(quote_wait)
        self.assertTrue(all(not s['entry_ready'] for s in quote_wait))
        for changes in ({"price":asset["price"]+10},{"payout":.84},{"is_open":False}):
            self.assertEqual(snapshot([{**asset,**changes}],histories,prefs,NOW)[1],[])
        self.assertIsNone(frame(pattern("range_rebound"),60,NOW+1000))

    def test_smart_money_remains_an_independent_family(self):
        asset, histories, prefs = market("range_rebound")
        histories={("PAIR_otc",p):fixture(p) for p in (60,300)}
        asset["price"]=106.3
        self.assertTrue(any(c["strategy"]=="smart_money" for c in snapshot([asset],histories,prefs,NOW)[1]))


class StrategyModelContractTests(unittest.TestCase):
    def test_responses_client_reviews_each_family_with_explicit_strategy_and_exact_refs(self):
        with tempfile.TemporaryDirectory() as tmp:
            save(tmp,"lr_fixture_strategy_contract_only","deepseek-4.1-flash",allowed_users=[101],provider="lunaroute")
            for kind in (s["id"] for s in STRATEGIES if s["id"] != "smart_money"):
                for direction in ("up","down"):
                    with self.subTest(kind=kind,direction=direction):
                        asset,histories,prefs=market(kind,direction=direction)
                        data,_=snapshot([asset],histories,prefs,NOW)
                        calls=[]
                        def opener(request, timeout):
                            body=json.loads(request.data);calls.append(body)
                            payload=json.loads(body["input"][1]["content"])
                            payload["qualified"]=[q for q in payload["qualified"] if q["strategy"] == kind]
                            decision=FixtureAI().submit(1,payload).result()["decision"]
                            value={k:decision[k] for k in SCHEMA["required"]}
                            return io.BytesIO(json.dumps({"status":"completed","output":[{"content":[{"type":"output_text","text":json.dumps(value)}]}]}).encode())
                        engine=OpenAIAnalyzer(tmp,opener=opener)
                        try:result=engine.review(data)
                        finally:engine.executor.shutdown()
                        self.assertEqual((result["strategy"],result["direction"]),(kind,direction))
                        self.assertEqual(len(calls),1)

    def test_strategy_choice_is_verified_even_when_symbol_and_direction_are_valid(self):
        asset, histories, prefs = market("ema_pullback")
        data,_ = snapshot([asset],histories,prefs,NOW)
        decision=FixtureAI().submit(1,data).result()["decision"]
        decision={k:decision[k] for k in SCHEMA["required"]}
        validate(decision,data)
        self.assertEqual(decision["strategy"],"ema_pullback")
        for wrong in ("invented", "smart_money", "range_rebound"):
            with self.subTest(strategy=wrong),self.assertRaises(DecisionError):
                validate({**decision,"strategy":wrong},data)
        self.assertEqual(data["period_unit"],"seconds")
        self.assertEqual([f["timeframe"] for f in data["pairs"][0]["frames"]],["M1","M5"])

    def test_non_smc_ai_choice_survives_scan_tracking_and_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            ai=FixtureAI()
            ledger=Ledger(Path(tmp)/"ledger.sqlite3",clock=lambda:NOW,ai=ai)
            token=ledger.claim(ledger.pair(101)["code"])["token"]
            asset,histories,prefs=market("ema_pullback")
            ledger.settings(101,{"market":"otc","allow_otc":True})
            events=[{"type":"asset","symbol":"PAIR_otc","market":"otc","payout":.92,"is_open":True,"expiries":[5,10,15],"time":NOW},
                    {"type":"quote","symbol":"PAIR_otc","price":asset["price"],"time":NOW},
                    {"type":"account","mode":"demo","account_id":"456","currency":"USD","balance":"105.20","time":NOW}]
            events += [{"type":"candle","symbol":symbol,"period":period,**row} for (symbol,period),rows in histories.items() for row in rows]
            for i in range(0,len(events),200):ledger.ingest(token,{"events":events[i:i+200]})
            result=ledger.start_scan(101)
            self.assertEqual(result["status"],"candidate",result)
            self.assertEqual(result["strategy"],"ema_pullback")
            sid=ledger.watch(101,{"analysis_id":result["analysis_id"],"mode":"demo"})
            state=Ledger(ledger.path,clock=lambda:NOW,ai=ai).state({"id":101})
            self.assertEqual(state["signal_sessions"][0]["setup"]["strategy"],"ema_pullback")
            self.assertEqual(state["signal_sessions"][0]["id"],sid)
            self.assertEqual(state["accounts"][0]["balance"],"105.20")
            self.assertEqual(state["trades"],[])


if __name__ == "__main__":
    unittest.main()
