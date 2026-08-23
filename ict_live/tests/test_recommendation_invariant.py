"""Invariant: a LONG/SHORT recommendation is EXACTLY the current-rank-1 setup — no stale/global
metadata. The headline's setup id / direction / entry / stop / target / RR must match the
current-ranked winner, and its current_rank must be 1."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ict_live.engine import pipeline
from ict_live.market.bar import Bar

ET = ZoneInfo("America/New_York")


def _series(n, seed):
    bars, px, x = [], 20000.0, seed
    t0 = datetime(2026, 6, 1, 18, 0, tzinfo=ET)
    for i in range(n):
        x = (1103515245 * x + 12345) % (2 ** 31)
        o = px
        c = px + ((x % 21) - 10) * 1.5
        ot = t0 + timedelta(minutes=15 * i)
        bars.append(Bar("15m", ot, ot + timedelta(minutes=15), o, max(o, c) + (x % 7),
                        min(o, c) - (x % 5), c, 100.0))
        px = c
    return bars


def test_recommendation_is_exactly_current_rank_1_setup():
    checked = False
    for seed in range(1, 120):
        ms = pipeline.analyze(_series(240, seed=seed), "15m")
        rec = ms.recommendation
        if rec.decision not in ("LONG", "SHORT"):
            continue
        checked = True
        win = rec.setup
        cr = ms.lifecycle["current_ranking"].get(win.id, {})
        # the winner must be the CURRENT rank-1 setup
        assert cr.get("current_rank") == 1, f"winner current_rank={cr.get('current_rank')}"
        # and it must be a current competitor (lifecycle 'current')
        assert win.id in ms.lifecycle["current_setup_ids"]
        # headline direction matches the setup, and the setup is actionable
        assert rec.decision == ("LONG" if win.direction == "long" else "SHORT")
        assert win.actionable is True
        # the reason references current rank #1, not a global rank
        assert "#1" in rec.reason
        # every current setup ranked below the winner has current_rank > 1
        for sid in ms.lifecycle["current_setup_ids"]:
            if sid != win.id:
                assert ms.lifecycle["current_ranking"][sid]["current_rank"] > 1
        break
    assert checked, "no actionable scene found to check the invariant"


def test_no_current_setup_yields_no_trade():
    # a flat market -> no current competitors -> NO-TRADE with no winning setup
    t0 = datetime(2026, 6, 1, 18, tzinfo=ET)
    flat = [Bar("15m", t0 + timedelta(minutes=15 * i), t0 + timedelta(minutes=15 * (i + 1)),
                100, 100.5, 99.5, 100, 1.0) for i in range(80)]
    ms = pipeline.analyze(flat, "15m")
    assert ms.recommendation.decision == "NO-TRADE" and ms.recommendation.setup is None
