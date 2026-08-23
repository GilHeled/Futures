"""Trade-record schema (maps engine recommendation + outcome onto the agreed minimal fields) and the
stats aggregator (expectancy / win-rate / v1 TRADE-vs-PASS edge)."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ict_live.engine import execution_quality as EQ
from ict_live.engine import outcomes as OUT
from ict_live.engine import pipeline
from ict_live.journal import record as REC
from ict_live.journal import stats as STATS
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


def test_record_from_live_setup_and_outcome():
    for seed in range(1, 200):
        sig = _series(260, seed=seed)
        ms = pipeline.analyze(sig[:240], "15m")
        win = ms.recommendation.setup
        if win is None:
            continue
        outcome = OUT.label_setup(id=win.id, symbol="MNQ", tf="15m", direction=win.direction,
                                  entry=win.entry, stop=win.stop, target=win.target,
                                  decision_index=239, bars=sig)
        tr = REC.build(ms, scene_id="sc1", symbol="MNQ", timestamp=sig[239].open_time.isoformat(),
                       outcome=outcome)
        assert tr.engine_direction in ("LONG", "SHORT")
        assert tr.execution in ("TRADE", "PASS")
        assert tr.risk > 0 and tr.entry == win.entry and tr.stop == win.stop
        # execution + score are consistent with the frozen v1 layer
        ea = EQ.assess(ms)
        assert tr.execution == ea.execution and tr.reasoning["execution_score"] == ea.confidence
        assert tr.reasoning["weakest_factor"] in EQ.FACTOR_NAMES
        assert set(tr.reasoning) == {"manipulation", "mss", "fvg", "dealing_range",
                                     "execution_score", "weakest_factor"}
        assert tr.tp_mode == REC.TP_MODE_V1
        d = tr.to_dict()
        assert d["scene_id"] == "sc1" and "result_R" in d
        return
    raise AssertionError("no live setup found in the synthetic series")


def test_build_none_when_no_setup():
    t0 = datetime(2026, 6, 1, 18, tzinfo=ET)
    flat = [Bar("15m", t0 + timedelta(minutes=15 * i), t0 + timedelta(minutes=15 * (i + 1)),
                100, 100.5, 99.5, 100, 1.0) for i in range(80)]
    ms = pipeline.analyze(flat, "15m")
    assert REC.build(ms, scene_id="x", symbol="MES", timestamp="t") is None


def _rec(execution, result_R, triggered=True):
    return {"scene_id": "s", "symbol": "MES", "engine_direction": "LONG", "execution": execution,
            "entry": 1.0, "stop": 0.5, "target": 2.0, "risk": 0.5, "reward_R": 2.0,
            "reasoning": {"weakest_factor": "pd_location"}, "tp_mode": REC.TP_MODE_V1,
            "triggered": triggered, "hit_stop": result_R is not None and result_R < 0,
            "hit_tp": result_R is not None and result_R > 0, "mfe_R": 1.0, "mae_R": -0.5,
            "result_R": result_R, "win": (result_R or 0) > 0, "note": ""}


def test_stats_expectancy_and_v1_edge():
    rows = [_rec("TRADE", 2.0), _rec("TRADE", 2.0), _rec("TRADE", -1.0),   # TRADE: mean (2+2-1)/3=1.0
            _rec("PASS", -1.0), _rec("PASS", -1.0), _rec("PASS", 2.0),     # PASS:  mean (-1-1+2)/3=0.0
            _rec("TRADE", None, triggered=False)]                          # NO_FILL excluded from R
    s = STATS.summarize(rows)
    assert s["by_execution"]["TRADE"]["expectancy_R"] == 1.0
    assert s["by_execution"]["PASS"]["expectancy_R"] == 0.0
    assert s["by_execution"]["TRADE"]["scored"] == 3                       # the NO_FILL is not scored
    assert s["by_execution"]["TRADE"]["no_fill"] == 1
    assert s["v1_filter_edge_R"] == 1.0                                    # TRADE beats PASS by 1R
    assert s["overall"]["n"] == 7
    md = STATS.render_md(s)
    assert "v1 filter edge" in md
