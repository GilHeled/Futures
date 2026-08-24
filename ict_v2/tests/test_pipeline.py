"""ICT v2 pipeline: four-layer cascade (4H context -> 1H setup -> 15m confirmation -> 1m execution),
each reusing the frozen v1 engine on its own timeframe."""
from types import SimpleNamespace

from ict_live.market.bar import Bar
from ict_v2 import pipeline as v2


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
