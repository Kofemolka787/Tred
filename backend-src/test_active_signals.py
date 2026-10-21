"""Active-mode recognition and counterexamples; no performance claims."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from ai_market import snapshot
from market_strategies import ACTIVE_STRATEGIES, frame, detect
from test_market_strategies import NOW, market, pattern
from test_support import FixtureAI
from trading_core import DomainError, Ledger


def active_market(kind, expiry=5, direction="up"):
    asset, histories, prefs = market("range_rebound",expiry)
    primary, higher = ((300,900) if expiry==15 else (60,300))
    if kind=="trend_continuation":
        rows=pattern("ema_pullback",primary)
        for i,row in enumerate(rows):
            close=100+i*.04
            row.update(open=close-.12,close=close,high=close+.12,low=close-.18)
    else:
        rows=pattern("range_rebound",primary)
        if kind=="momentum_breakout":
            rows[-1].update(open=101.15,close=102,high=102.1,low=101)
        elif kind=="level_engulfing":
            rows[-2].update(open=99.15,close=98.85,high=99.3,low=98.5)
            rows[-1].update(open=98.8,close=99.35,high=99.45,low=98.45)
        else:raise AssertionError(kind)
    if direction=="down":
        for row in rows:
            o,h,l,c=(row[k] for k in ("open","high","low","close"))
            row.update(open=220-o,high=220-l,low=220-h,close=220-c)
    histories[("PAIR_otc",primary)]=rows
    histories[("PAIR_otc",higher)]=pattern("range_rebound",higher,direction)
    asset["price"]=rows[-1]["close"]
    prefs["signal_mode"]="active"
    return asset,histories,prefs


class ActiveSignalsTests(unittest.TestCase):
    def test_new_families_on_both_directions_and_all_durations(self):
        for spec in ACTIVE_STRATEGIES:
            for direction in ("up","down"):
                for expiry in (5,10,15):
                    with self.subTest(family=spec["id"],direction=direction,expiry=expiry):
                        asset,histories,prefs=active_market(spec["id"],expiry,direction)
                        data,rows=snapshot([asset],histories,prefs,NOW)
                        matches=[r for r in rows if (r["strategy"],r["direction"])==(spec["id"],direction)]
                        self.assertTrue(matches,[(r["strategy"],r["direction"]) for r in rows])
                        self.assertTrue(matches[0]["entry_ready"])
                        self.assertEqual(matches[0]["risk_level"],"elevated")
                        self.assertIn("Повышенный риск",matches[0]["strategy_risk"])
                        self.assertEqual(len(data["strategies"]),8)
                        _,strict=snapshot([asset],histories,{**prefs,"signal_mode":"strict"},NOW)
                        self.assertFalse(any(r["strategy"]==spec["id"] for r in strict))

    def test_strong_opposite_context_still_blocks_active_entries(self):
        for spec in ACTIVE_STRATEGIES:
            asset,histories,prefs=active_market(spec["id"])
            histories[("PAIR_otc",300)]=pattern("ema_pullback",300,"down")
            self.assertEqual(snapshot([asset],histories,prefs,NOW)[1],[])

    def test_active_ema_does_not_require_a_second_trend_or_smc_pattern(self):
        asset,histories,prefs=market("ema_pullback")
        histories[("PAIR_otc",300)]=pattern("range_rebound",300)
        _,strict=snapshot([asset],histories,prefs,NOW)
        _,active=snapshot([asset],histories,{**prefs,"signal_mode":"active"},NOW)
        self.assertFalse(any(r['strategy']=='ema_pullback' for r in strict))
        self.assertTrue(any(r['strategy']=='ema_pullback' for r in active))

    def test_flat_data_without_a_trigger_cannot_force_an_entry(self):
        asset,histories,prefs=active_market("trend_continuation")
        for rows in histories.values():
            for row in rows:row.update(open=100,close=100,low=99.8,high=100.2)
        self.assertEqual(snapshot([asset],histories,prefs,NOW)[1],[])

    def test_no_trigger_invalid_zone_gap_and_stale_source_are_not_overridden(self):
        asset,histories,prefs=active_market("momentum_breakout")
        primary=histories[("PAIR_otc",60)]
        changed=deepcopy(histories)
        changed[("PAIR_otc",60)][-1].update(open=101,close=101.1,high=102.1,low=100.9)
        self.assertFalse(any(c["strategy"]=="momentum_breakout" for c in snapshot([asset],changed,prefs,NOW)[1]))
        changed=deepcopy(histories);del changed[("PAIR_otc",60)][-4]
        self.assertEqual(snapshot([asset],changed,prefs,NOW)[1],[])
        self.assertEqual(snapshot([{**asset,"price":90}],histories,prefs,NOW)[1],[])
        self.assertEqual(snapshot([{**asset,"is_open":False}],histories,prefs,NOW)[1],[])
        self.assertEqual(snapshot([{**asset,"payout":.84}],histories,prefs,NOW)[1],[])
        self.assertIsNone(frame(primary,60,NOW+1000))

    def test_mode_is_validated_persisted_and_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger=Ledger(Path(tmp)/"ledger.sqlite3",clock=lambda:NOW,ai=FixtureAI())
            self.assertEqual(ledger.state({"id":101})["preferences"]["signal_mode"],"strict")
            ledger.settings(101,{"signal_mode":"active"})
            self.assertEqual(ledger.state({"id":101})["preferences"]["signal_mode"],"active")
            self.assertEqual(ledger.state({"id":202})["preferences"]["signal_mode"],"strict")
            with self.assertRaises(DomainError):ledger.settings(101,{"signal_mode":"always_win"})
            ledger=Ledger(ledger.path,clock=lambda:NOW,ai=FixtureAI())
            self.assertEqual(ledger.state({"id":101})["preferences"]["signal_mode"],"active")

