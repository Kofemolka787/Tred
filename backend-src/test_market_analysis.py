import math
import random
import unittest
from market_analysis import analyze, rank


def fixture(period=60, now=1800000000, *, reaction=True):
    rng=random.Random(0)
    rows=[]
    for i in range(160):
        o=100+i*.04+math.sin(i*.65)*.22+rng.uniform(-.035,.035)
        c=o+rng.uniform(-.07,.1)
        rows.append({"time":now-(160-i)*period,"open":o,"high":max(o,c)+rng.uniform(.02,.09),"low":min(o,c)-rng.uniform(.02,.09),"close":c})
    # A completed bullish displacement, then a recent return to its origin.
    updates = {154:(106.08,106.50,106.05,106.45),155:(106.45,106.60,106.30,106.48),
               156:(106.48,106.52,106.18,106.30),157:(106.30,106.46,106.20,106.40),
               158:(106.40,106.50,106.28,106.43),159:(106.43,106.48,105.99,106.30)}
    for i,(o,h,l,c) in (updates.items() if reaction else []):
        rows[i].update(open=o,high=h,low=l,close=c)
    return rows


class AnalysisTests(unittest.TestCase):
    def test_confluence_produces_explained_research_candidate_without_probability(self):
        result=analyze(fixture(),60,1800000000)
        self.assertEqual(result["status"],"candidate")
        self.assertEqual(result["direction"],"up")
        self.assertGreaterEqual(len(result["evidence"]),3)
        self.assertTrue(any(z["kind"]=="order_block" for z in result["zones"]))
        self.assertNotIn("probability",result)

    def test_unfinished_and_future_bars_cannot_change_current_analysis(self):
        rows=fixture()
        before=analyze(rows,60,1800000000)
        for stamp in (1800000000,1800000060):
            rows.append({"time":stamp,"open":10000,"high":10000,"low":.001,"close":.001})
        self.assertEqual(analyze(rows,60,1800000000),before)

    def test_missing_or_stale_history_blocks_recommendation(self):
        for rows,now in ((fixture()[:79],1800000000),(fixture()[:-1],1800001000),(fixture()[:100]+fixture()[101:],1800000000)):
            self.assertEqual(analyze(rows,60,now)["status"],"skipped")

    def test_old_gap_does_not_discard_a_complete_recent_run(self):
        from market_strategies import frame
        now=1800000000
        rows=fixture()
        old_gap=rows[:20]+rows[21:]
        result=analyze(old_gap,60,now)
        self.assertTrue(result.get('history_ready'),result)
        features=frame(old_gap,60,now)
        self.assertEqual(len(features['rows']),139)
        self.assertEqual(features['rows'][0]['time'],rows[21]['time'])
        recent_gap=rows[:100]+rows[101:]
        self.assertFalse(analyze(recent_gap,60,now).get('history_ready'))
        self.assertIsNone(frame(recent_gap,60,now))
        damaged=[dict(r) for r in old_gap]
        damaged[-1]['low']=1000
        self.assertIsNone(frame(damaged,60,now),'A malformed recent bar is never skipped to make an entry')

    def test_multi_timeframe_alignment_and_live_asset_availability_are_required(self):
        now=1800000000
        a={"symbol":"EURUSD","market":"normal","is_open":True,"payout":.92,"updated_at":now,"quote_time":now,"price":106.30,"expiries":[5,15]}
        histories={("EURUSD",60):fixture(),("EURUSD",300):fixture(300),("EURUSD",900):fixture(900)}
        result,_=rank([a],histories,expiry=15,market="normal",symbol=None,allow_otc=False,now=now)
        self.assertEqual(result[0]["timeframes"],[300,900])
        for change in ({"is_open":False},{"quote_time":now-11},{"updated_at":now-61},{"expiries":[5]}):
            result,_=rank([{**a,**change}],histories,expiry=15,market="normal",symbol=None,allow_otc=False,now=now)
            self.assertEqual(result,[])

    def test_closed_regular_market_does_not_silently_switch_to_otc(self):
        now=1800000000
        a={"symbol":"EURUSD_otc","market":"otc","is_open":True,"payout":.92,"updated_at":now,"quote_time":now,"price":106.30,"expiries":[5]}
        histories={(a["symbol"],p):fixture(p) for p in (60,300)}
        result,_=rank([a],histories,expiry=5,market="normal",symbol=None,allow_otc=True,now=now)
        self.assertEqual(result,[])
        result,_=rank([a],histories,expiry=5,market="otc",symbol=None,allow_otc=False,now=now)
        self.assertEqual(result,[])


class StructureGuardTests(unittest.TestCase):
    def test_channel_support_reaction_requires_a_sweep(self):
        rows=fixture(reaction=False)
        self.assertEqual(analyze(rows,60,1800000000)["status"],"skipped")
        rows[-1].update(low=105.70,close=106.15)
        result=analyze(rows,60,1800000000)
        self.assertEqual(result["status"],"candidate",result)
        self.assertIsNotNone(result["channel"])
        self.assertTrue(any("канала" in e for e in result["evidence"]))
        self.assertTrue(any(e["kind"]=="sweep" for e in result["events"]))

    def test_short_is_symmetric_and_explained(self):
        rows=fixture()
        for row in rows:
            o,h,l,c=(row[k] for k in ("open","high","low","close"))
            row.update(open=220-o,high=220-l,low=220-h,close=220-c)
        result=analyze(rows,60,1800000000)
        self.assertEqual(result["status"],"candidate")
        self.assertEqual(result["direction"],"down")
        self.assertTrue(any(z["kind"]=="order_block" for z in result["zones"]))

    def test_payout_priority_then_explicit_floor_and_expiry(self):
        now=1800000000
        assets=[{"symbol":name,"market":"otc","is_open":True,"payout":payout,"updated_at":now,"quote_time":now,"price":106.30,"expiries":[5,10,15]} for name,payout in [("A_OTC",.85),("B_OTC",.92),("C_OTC",.84)]]
        histories={(a["symbol"],p):fixture(p) for a in assets for p in (60,300,900)}
        for expiry in (5,10,15):
            results,_=rank(assets,histories,expiry=expiry,market="otc",symbol=None,allow_otc=True,now=now)
            self.assertEqual([r["symbol"] for r in results],["B_OTC","A_OTC"])
            self.assertTrue(all(r["payout"]>=.85 for r in results))
        results,_=rank(assets,histories,expiry=5,market="otc",symbol=None,allow_otc=True,now=now,min_payout=.92)
        self.assertEqual([r["symbol"] for r in results],["B_OTC"])

    def test_price_that_has_left_the_zone_is_never_chased(self):
        now=1800000000
        a={"symbol":"EURUSD","market":"normal","is_open":True,"payout":.92,"updated_at":now,"quote_time":now,"price":200,"expiries":[5]}
        results,_=rank([a],{("EURUSD",p):fixture(p) for p in (60,300)},expiry=5,market="normal",symbol=None,allow_otc=False,now=now)
        self.assertEqual(results,[])

    def test_aggregation_does_not_fill_missing_minutes_or_use_open_bars(self):
        from market_analysis import aggregate
        rows=fixture()
        full=aggregate(rows,60,300,1800000000)
        missing=aggregate(rows[:156]+rows[157:],60,300,1800000000)
        self.assertEqual(len(full)-len(missing),1)
        self.assertTrue(all(r["time"]+300<=1800000000 for r in full))
        extra={**rows[-1],"time":1800000000,"high":999}
        self.assertEqual(aggregate(rows+[extra],60,300,1800000000),full)

    def test_coverage_separates_catalog_prices_and_history(self):
        from market_analysis import coverage
        now=1800000000
        a={"symbol":"EURUSD","market":"otc","is_open":True,"payout":.92,"updated_at":now,"quote_time":now,"price":106.30,"expiries":[5]}
        c=coverage([a],{},market="otc",expiry=5,min_payout=.85,now=now)
        self.assertEqual((c["catalog"],c["eligible"],c["quoted"],c["ready"]),(1,1,1,0))
        c=coverage([a],{("EURUSD",p):fixture(p) for p in (60,300)},market="otc",expiry=5,min_payout=.85,now=now)
        self.assertEqual(c["ready"],1)
        c=coverage([a,{**a,"symbol":"UNQUOTED","price":None,"quote_time":None}],{("EURUSD",p):fixture(p) for p in (60,300)},market="otc",expiry=5,min_payout=.85,now=now)
        self.assertEqual((c["catalog"],c["quoted"],c["ready"]),(2,1,1))

if __name__=="__main__": unittest.main()
