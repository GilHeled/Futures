"""ICT v2 pipeline: four-layer cascade (4H context -> 1H setup -> 15m confirmation -> 1m execution),
each reusing the frozen v1 engine on its own timeframe."""
from types import SimpleNamespace

from ict_live.market.bar import Bar
from ict_v2 import pipeline as v2


def test_htf_bias_range_breach_invalidation(monkeypatch):
    """A last-completed-leg bias is neutralised once price CLOSES beyond the range that defines it:
    close below the low voids an up-leg (long); close above the high voids a down-leg (short)."""
    from datetime import datetime, timedelta, timezone

    def _bar(close):
        t = datetime(2026, 8, 26, tzinfo=timezone.utc)
        return Bar("4H", t, t + timedelta(hours=4), close, close + 1, close - 1, close, 0.0)

    def fake_ms(direction, low, high):
        dr = SimpleNamespace(direction=direction, low=low, high=high, ce=(low + high) / 2)
        return SimpleNamespace(ranges=[dr], active_erl=[])

    # up-leg (long) with price still inside the range -> stays long
    monkeypatch.setattr(v2.v1, "analyze", lambda bars, tf: fake_ms("up", 100.0, 110.0))
    assert v2.htf_context([_bar(105.0)], "4H").bias == "long"
    # up-leg but a close BELOW the range low -> neutralised
    c = v2.htf_context([_bar(99.0)], "4H")
    assert c.bias == "neutral" and "BELOW" in c.bias_note
    # down-leg (short) with a close ABOVE the range high -> neutralised
    monkeypatch.setattr(v2.v1, "analyze", lambda bars, tf: fake_ms("down", 100.0, 110.0))
    assert v2.htf_context([_bar(105.0)], "4H").bias == "short"
    c2 = v2.htf_context([_bar(111.0)], "4H")
    assert c2.bias == "neutral" and "ABOVE" in c2.bias_note


def test_four_layer_cascade_runs():
    st = v2.demo_state(seed=7)
    assert st.context.tf == "4H" and st.setup.tf == "1H" and st.confirmation.tf == "15m"
    assert st.execution.tf == "1m"
    assert st.context.bias in ("long", "short", "neutral")
    assert isinstance(st.setup.candidates, list) and isinstance(st.confirmation.gated, list)
    d = st.describe()
    assert "CONTEXT" in d and "SETUP" in d and "CONFIRM" in d and "EXECUTION" in d


def test_execution_only_fires_when_whole_cascade_holds():
    st = v2.demo_state(seed=7)
    c = st.context
    if st.execution.executables:                          # a real trade requires the FULL chain
        assert c.bias in ("long", "short")
        assert st.setup.gated and st.confirmation.gated
        for ex in st.execution.executables:
            assert ex.direction == c.bias                 # every layer aligned to the context bias
    else:
        assert st.execution.decision.startswith("NO-TRADE")


def test_execution_for_reports_the_stage_reached():
    # staged NO-TRADE messages (early returns, no bars needed)
    long_ctx = SimpleNamespace(bias="long")
    gated = SimpleNamespace(gated=[SimpleNamespace(setup=SimpleNamespace(direction="long"))])
    empty = SimpleNamespace(gated=[])
    assert "no context bias" in v2.execution_for(None, "1m", None, gated, gated).decision
    assert "no context bias" in v2.execution_for(None, "1m", SimpleNamespace(bias="neutral"), gated, gated).decision
    assert "no 1H setup" in v2.execution_for(None, "1m", long_ctx, empty, gated).decision
    assert "awaiting 15m confirmation" in v2.execution_for(None, "1m", long_ctx, gated, empty).decision
