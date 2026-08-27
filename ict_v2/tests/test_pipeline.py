"""ICT v2 pipeline: four-layer cascade (4H context -> 1H setup -> 15m confirmation -> 1m execution),
each reusing the frozen v1 engine on its own timeframe."""
from types import SimpleNamespace

from ict_live.market.bar import Bar
from ict_v2 import pipeline as v2


def test_daily_weekly_anchor_vetoes_counter_trend_bias(monkeypatch):
    """A Daily/Weekly anchor downgrades a 4H bias that OPPOSES it to neutral; an aligned or absent
    anchor leaves the 4H bias intact (the anchor never forces a direction)."""
    def fake_ms(direction):
        dr = SimpleNamespace(direction=direction, low=100.0, high=110.0, ce=105.0)
        return SimpleNamespace(ranges=[dr], active_erl=[])
    # 4H says up→long
    monkeypatch.setattr(v2.v1, "analyze", lambda bars, tf: fake_ms("up"))
    assert v2.htf_context([], "4H").bias == "long"                       # no anchor → unchanged
    assert v2.htf_context([], "4H", anchor="long").bias == "long"        # aligned anchor → kept
    assert v2.htf_context([], "4H", anchor="short", anchor_tf="D").bias == "neutral"  # opposed → veto
    # htf_bias_of reads a TF's leg direction (used to compute the anchor)
    monkeypatch.setattr(v2.v1, "analyze", lambda bars, tf: fake_ms("down"))
    assert v2.htf_bias_of([], "D") == "short"


def test_entry_models_registry_and_tagging():
    """The execution layer is pluggable: FVG is the implemented default, the other course models are
    registered but not yet implemented (enabling them is inert), and every candidate is tagged."""
    from ict_v2 import entry_models as EM
    cat = EM.catalog()
    assert cat["fvg"]["implemented"] is True
    for m in ("order_block", "breaker", "mitigation_block", "ifvg", "iofed"):
        assert m in cat and cat[m]["implemented"] is False          # declared, not yet built
    assert EM.resolve(None) == ("fvg",)
    assert EM.resolve(["order_block", "breaker"]) == ("fvg",)         # planned models drop → FVG kept
    assert EM.resolve(["fvg"]) == ("fvg",)
    # every generated candidate carries entry_model="fvg" today
    st = v2.demo_state(seed=7)
    assert all(c["entry_model"] == "fvg" for c in st.setup.cand_info)


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
