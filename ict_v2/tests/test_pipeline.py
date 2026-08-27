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


def test_entry_models_registry_is_course_scoped():
    """v2 implements THIS course, whose entry PD array is the FVG. FVG is the sole execution model;
    broader-ICT constructs the course does not teach (Order Block / Breaker / Mitigation Block / IFVG
    / IOFED) are intentionally absent. The layer is still pluggable — adding a model is a registry
    entry — but only a course-defined model is registered."""
    from ict_v2 import entry_models as EM
    cat = EM.catalog()
    assert set(cat) == {"fvg"} and cat["fvg"]["implemented"] is True
    for m in ("order_block", "breaker", "mitigation_block", "ifvg", "iofed"):
        assert m not in cat and m not in EM.LIFECYCLE           # not this course's methodology
    assert EM.resolve(None) == ("fvg",)
    assert EM.resolve(["fvg"]) == ("fvg",)
    assert EM.resolve(["order_block", "breaker"]) == ("fvg",)   # unknown models drop → FVG kept
    # every candidate that HAS an entry is tagged with the model that produced it (fvg);
    # partial candidates (no entry object yet) carry no model
    st = v2.demo_state(seed=7)
    assert all(c["entry_model"] == "fvg" for c in st.setup.cand_info if c["entry_obj"])
    assert all(c["entry_model"] == "" for c in st.setup.cand_info if not c["entry_obj"])


def test_detect_v11_bars_arg_is_inert_for_fvg():
    """CONTRACT v1.1: detect() carries a `bars` argument (raw OHLC handed to EVERY model, so a future
    candle-based course model could read candles v1 never pre-computes). FVG sources its gaps off `ms`,
    so `bars` must be inert for it — threading real bars vs None must yield byte-identical candidates
    across the whole cascade. This guards the "FVG behaves identically" requirement."""
    import json
    from ict_v2 import entry_models as EM

    def collect(seed):
        st = v2.demo_state(seed)
        return [json.dumps(c, sort_keys=True, default=str)
                for stage in (st.setup, st.confirmation) for c in getattr(stage, "cand_info", [])]

    base = {s: collect(s) for s in range(1, 30)}
    orig = EM.detect
    v2.EM.detect = lambda model, disp, mss, ms, direction, bars: orig(model, disp, mss, ms, direction, None)
    try:
        alt = {s: collect(s) for s in range(1, 30)}
    finally:
        v2.EM.detect = orig
    assert sum(len(v) for v in base.values()) > 0
    assert base == alt                                   # FVG output invariant to the new bars arg


def test_entry_common_contract():
    """Every entry exposes the SAME contract with a two-level state (common + model lifecycle); FVG
    conforms; geometry is assembled generically off the common state."""
    from types import SimpleNamespace
    from ict_v2 import entry_models as EM
    FIELDS = {"model", "direction", "ref", "invalidation", "state", "lifecycle", "quality", "reason"}
    assert set(EM.Entry(model="x", direction="long", ref=1.0, invalidation=0.0).to_dict()) == FIELDS
    # common state is DERIVED from the model-specific lifecycle (FVG's own vocab)
    assert EM.Entry(model="fvg", direction="long", ref=1, invalidation=0, lifecycle="mitigated").state == "completed"
    assert EM.Entry(model="fvg", direction="long", ref=1, invalidation=0, lifecycle="valid").state == "valid"
    assert EM.common_state("fvg", "waiting") == "waiting"
    # the two-level mechanism is generic, not FVG-specific: a model/sub-state absent from LIFECYCLE
    # falls back to the safe common state (so a future course-defined model just registers its own)
    assert EM.common_state("some_future_course_model", "anything") == "waiting"
    # FVG candidates expose the contract; common state ∈ COMMON_STATES; lifecycle is FVG's own
    st = v2.demo_state(seed=7)
    withentry = [c for c in st.setup.cand_info if c["entry_obj"]]
    assert withentry and all(set(c["entry_obj"]) == FIELDS for c in withentry)
    assert all(c["entry_obj"]["state"] in EM.COMMON_STATES for c in withentry)
    assert all(c["entry_obj"]["lifecycle"] in EM.LIFECYCLE["fvg"]["vocab"] for c in withentry)
    # assemble() reads only the COMMON state (model-agnostic): a "completed" entry is not enterable;
    # a valid one is. State is set explicitly here to exercise the geometry without any model lifecycle.
    good = EM.Entry(model="fvg", direction="long", ref=100.0, invalidation=98.0, state="valid")
    g = EM.assemble(good, sweep_extreme=97.0, active_erl=[SimpleNamespace(kind="high", price=110.0)], min_stop=2.0)
    assert g["stop"] == 97.0 and g["target"] == 110.0 and g["reject"] == "" and g["rr"] == 3.33
    done = EM.Entry(model="fvg", direction="long", ref=100.0, invalidation=98.0, state="completed")
    assert EM.assemble(done, 97.0, [SimpleNamespace(kind="high", price=110.0)], 2.0)["reject"] != ""


def test_engine_has_no_model_specific_branching():
    """The invariant, encoded as a test: the engine (pipeline) must never branch on a model NAME.
    Models are pure registry plugins, so any future course-defined model plugs in without engine edits."""
    import pathlib
    src = pathlib.Path(v2.__file__).read_text()
    lowered = src.lower()
    for needle in ('== "fvg"', "== 'fvg'", 'model == ', 'entry_model == '):
        assert needle not in lowered, f"engine branches on a model name: {needle!r}"


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
