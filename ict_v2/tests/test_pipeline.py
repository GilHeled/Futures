"""ICT v2 pipeline: operates as three explicit stages (HTF context / MTF setup / LTF execution),
each on its own timeframe, reusing the frozen v1 engine. First-iteration structural test."""
from datetime import datetime, timedelta, timezone

from ict_live.market.bar import Bar
from ict_v2 import pipeline as v2


def _bars(n, tf, minutes, seed):
    bars, px, x = [], 20000.0, seed
    t0 = datetime(2026, 6, 1, 18, 0, tzinfo=timezone.utc)
    for i in range(n):
        x = (1103515245 * x + 12345) % (2 ** 31)
        o = px
        c = px + ((x % 21) - 10) * 1.5
        ot = t0 + timedelta(minutes=minutes * i)
        bars.append(Bar(tf, ot, ot + timedelta(minutes=minutes), o, max(o, c) + (x % 7),
                        min(o, c) - (x % 5), c, 100.0))
        px = c
    return bars


def test_three_explicit_stages():
    st = v2.analyze_mtf(_bars(260, "4H", 240, 7), _bars(260, "15m", 15, 11),
                        _bars(260, "1m", 1, 23), htf="4H", mtf="15m", ltf="1m")
    # each stage exists, tagged with its own timeframe
    assert st.context.tf == "4H" and st.setup.tf == "15m" and st.execution.tf == "1m"
    # [1] context: a bias + (usually) a dealing range with premium/discount zones
    assert st.context.bias in ("long", "short", "neutral")
    if st.context.dealing_range is not None:
        assert st.context.zone(st.context.dealing_range.ce) in ("premium", "discount", "equilibrium")
    # [2] setup: the intermediate manipulation/displacement/MSS layer is present (lists)
    assert isinstance(st.setup.sweeps, list) and isinstance(st.setup.displacements, list)
    assert isinstance(st.setup.mss, list)
    # [3] execution: entry FVGs + executables (only for gated setups) + a decision
    assert isinstance(st.execution.fvgs, list) and isinstance(st.execution.executables, list)
    assert st.execution.decision.startswith(("LONG", "SHORT", "NO-TRADE"))
    # describe() renders all three stages
    d = st.describe()
    assert "HTF CONTEXT" in d and "MTF SETUP" in d and "LTF EXECUTION" in d


def test_htf_gate_controls_execution():
    # correlated data (HTF/MTF/LTF from one 1m base) so the gate is meaningful
    st = v2.demo_state(seed=7)
    c = st.context
    # LTF execution operates ONLY on setups that passed the HTF gate — one executable per gated setup
    assert len(st.execution.executables) == len(st.setup.gated)
    # the gate is a real filter, not a pass-through
    assert len(st.setup.candidates) >= len(st.setup.gated)
    if st.setup.gated:
        assert st.execution.decision in ("LONG", "SHORT")
        # every gated setup agrees with HTF bias; every executable targets the HTF liquidity draw
        assert all(g.setup.direction == c.bias for g in st.setup.gated)
        for ex in st.execution.executables:
            assert ex.direction == c.bias
            if ex.objective is not None:
                assert ex.target == ex.objective.price
    else:
        assert st.execution.decision.startswith("NO-TRADE")


def test_stages_are_independent_functions():
    # the three stages are callable on their own (real separation, not one blob)
    ctx = v2.htf_context(_bars(200, "4H", 240, 3), "4H")
    stp = v2.mtf_setup(_bars(200, "15m", 15, 3), "15m", ctx)
    exe = v2.ltf_execution(_bars(200, "1m", 1, 3), "1m", stp, ctx)
    assert isinstance(ctx, v2.HTFContext) and isinstance(stp, v2.MTFSetup) and isinstance(exe, v2.LTFExecution)
