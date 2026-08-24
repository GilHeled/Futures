"""Live TradeTicket: assembles the frozen engine + execution v1 + fixed-2R exit into one verdict,
with a correct mechanical exit target and a consistent action (TAKE/SKIP/NO_SETUP)."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ict_live.engine import execution_quality as EQ
from ict_live.engine import pipeline
from ict_live.live import signal as SIG
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


def test_ticket_on_live_setup_matches_frozen_layers():
    for seed in range(1, 200):
        bars = _series(260, seed=seed)
        ms = pipeline.analyze(bars[:240], "15m", min_stop=SIG._MIN_STOP)
        if ms.recommendation.setup is None:
            continue
        t = SIG.build_ticket(bars[:240], symbol="MNQ", signal_tf="15m")
        win = ms.recommendation.setup
        ea = EQ.assess(ms)
        # structural + execution pass through unchanged
        assert t.structural == ms.recommendation.decision
        assert t.execution == ea.execution and t.confidence == ea.confidence
        assert t.action == ("TAKE" if ea.execution == "TRADE" else "SKIP")
        # mechanical exit is exactly +2R in the trade direction; structural target kept separately
        risk = abs(win.entry - win.stop)
        exp = win.entry + 2.0 * risk if win.direction == "long" else win.entry - 2.0 * risk
        assert abs(t.exit_target - round(exp, 4)) < 1e-6
        assert t.exit_target_R == 2.0
        assert t.structural_target == win.target        # analytical objective preserved, != exit
        assert t.ticket_id.endswith(bars[239].open_time.isoformat())
        assert set(t.reasoning) == {"manipulation", "mss", "fvg", "dealing_range",
                                    "execution_score", "weakest_factor"}
        d = t.to_dict()
        assert isinstance(d["reasons"], list)
        return
    raise AssertionError("no live setup produced")


def test_min_stop_is_per_instrument():
    from ict_live import config as C
    assert C.min_stop_for("CME_MINI:MNQ1!") == 2.0          # 8 * 0.25 (index futures, unchanged)
    assert round(C.min_stop_for("COMEX:GC1!"), 4) == 0.8    # 8 * 0.10 (gold)
    assert round(C.min_stop_for("NYMEX:CL1!"), 4) == 0.08   # 8 * 0.01 (crude)
    assert C.min_stop_for("UNKNOWN") == 2.0                 # falls back to a 0.25 tick


def test_no_setup_ticket():
    t0 = datetime(2026, 6, 1, 18, tzinfo=ET)
    flat = [Bar("15m", t0 + timedelta(minutes=15 * i), t0 + timedelta(minutes=15 * (i + 1)),
                100, 100.5, 99.5, 100, 1.0) for i in range(80)]
    t = SIG.build_ticket(flat, symbol="MES", signal_tf="15m")
    assert t.action == "NO_SETUP" and t.structural == "NO-TRADE"
    assert t.entry is None and t.exit_target is None
