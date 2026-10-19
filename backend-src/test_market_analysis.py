import math
import random
import unittest
from market_analysis import analyze, rank


def fixture(period=60, now=1800000000):
    rng=random.Random(0)
    rows=[]
    for i in range(160):
        o=100+i*.04+math.sin(i*.65)*.22+rng.uniform(-.035,.035)
        c=o+rng.uniform(-.07,.1)
        rows.append({"time":now-(160-i)*period,"open":o,"high":max(o,c)+rng.uniform(.02,.09),"low":min(o,c)-rng.uniform(.02,.09),"close":c})
    return rows


class AnalysisTests(unittest.TestCase):
    def test_confluence_produces_explained_research_candidate_without_probability(self):
        result=analyze(fixture(),60,1800000000)
        self.assertEqual(result["status"],"candidate")
        self.assertEqual(result["direction"],"up")
        self.assertGreaterEqual(len(result["evidence"]),4)
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

    def test_multi_timeframe_alignment_and_live_asset_availability_are_required(self):
        now=1800000000
        a={"symbol":"EURUSD","market":"normal","is_open":True,"payout":.8,"updated_at":now,"quote_time":now,"price":106,"expiries":[5,30]}
        histories={("EURUSD",60):fixture(),("EURUSD",300):fixture(300),("EURUSD",900):fixture(900)}
        result,_=rank([a],histories,expiry=30,market="normal",symbol=None,allow_otc=False,now=now)
        self.assertEqual(result[0]["timeframes"],[300,900])
        for change in ({"is_open":False},{"quote_time":now-11},{"updated_at":now-61},{"expiries":[5]}):
            result,_=rank([{**a,**change}],histories,expiry=30,market="normal",symbol=None,allow_otc=False,now=now)
            self.assertEqual(result,[])

    def test_closed_regular_market_does_not_silently_switch_to_otc(self):
        now=1800000000
        a={"symbol":"EURUSD_otc","market":"otc","is_open":True,"payout":.8,"updated_at":now,"quote_time":now,"price":106,"expiries":[5]}
        histories={(a["symbol"],p):fixture(p) for p in (60,300)}
        result,_=rank([a],histories,expiry=5,market="normal",symbol=None,allow_otc=True,now=now)
        self.assertEqual(result,[])
        result,_=rank([a],histories,expiry=5,market="otc",symbol=None,allow_otc=False,now=now)
        self.assertEqual(result,[])


if __name__=="__main__": unittest.main()
