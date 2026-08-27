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
    assert cat["order_block"]["implemented"] is True                 # first from-scratch plugin, built
    for m in ("breaker", "mitigation_block", "ifvg", "iofed"):
        assert m in cat and cat[m]["implemented"] is False           # declared, not yet built
    assert EM.resolve(None) == ("fvg",)                              # OB off by default
    assert EM.resolve(["breaker", "ifvg"]) == ("fvg",)               # planned models drop → FVG kept
    assert EM.resolve(["order_block"]) == ("order_block",)            # OB is opt-in and now resolves
    assert EM.resolve(["fvg", "order_block"]) == ("fvg", "order_block")
    assert EM.resolve(["fvg"]) == ("fvg",)
    # every candidate that HAS an entry is tagged with the model that produced it (fvg today);
    # partial candidates (no entry object yet) carry no model
    st = v2.demo_state(seed=7)
    assert all(c["entry_model"] == "fvg" for c in st.setup.cand_info if c["entry_obj"])
    assert all(c["entry_model"] == "" for c in st.setup.cand_info if not c["entry_obj"])


def test_detect_v11_bars_arg_is_inert_for_fvg():
    """CONTRACT v1.1: detect() gains a `bars` argument (raw OHLC handed to EVERY model, so a
    candle-body model like Order Block can read candles v1 never pre-computes). FVG sources its gaps
    off `ms`, so `bars` must be inert for it — threading real bars vs None must yield byte-identical
    candidates across the whole cascade. This guards the "FVG behaves identically" requirement."""
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
    # common state is DERIVED from the model-specific lifecycle
    assert EM.Entry(model="fvg", direction="long", ref=1, invalidation=0, lifecycle="mitigated").state == "completed"
    assert EM.Entry(model="fvg", direction="long", ref=1, invalidation=0, lifecycle="valid").state == "valid"
    assert EM.Entry(model="order_block", direction="long", ref=1, invalidation=0,
                    lifecycle="awaiting_retest").state == "waiting"
    assert EM.common_state("breaker", "confirmed") == "valid"
    # FVG candidates expose the contract; common state ∈ COMMON_STATES; lifecycle is FVG's own
    st = v2.demo_state(seed=7)
    withentry = [c for c in st.setup.cand_info if c["entry_obj"]]
    assert withentry and all(set(c["entry_obj"]) == FIELDS for c in withentry)
    assert all(c["entry_obj"]["state"] in EM.COMMON_STATES for c in withentry)
    assert all(c["entry_obj"]["lifecycle"] in EM.LIFECYCLE["fvg"]["vocab"] for c in withentry)
    # assemble() reads only the COMMON state: a "completed" entry is not enterable; a valid one is
    good = EM.Entry(model="order_block", direction="long", ref=100.0, invalidation=98.0, lifecycle="validated")
    g = EM.assemble(good, sweep_extreme=97.0, active_erl=[SimpleNamespace(kind="high", price=110.0)], min_stop=2.0)
    assert g["stop"] == 97.0 and g["target"] == 110.0 and g["reject"] == "" and g["rr"] == 3.33
    done = EM.Entry(model="order_block", direction="long", ref=100.0, invalidation=98.0, lifecycle="mitigated")
    assert EM.assemble(done, 97.0, [SimpleNamespace(kind="high", price=110.0)], 2.0)["reject"] != ""


def _ob_bar(o, h, l, c):
    from datetime import datetime, timedelta, timezone
    from ict_live.market.bar import Bar
    t = datetime(2026, 6, 1, tzinfo=timezone.utc)
    return Bar("1m", t, t + timedelta(minutes=1), o, h, l, c, 100.0)


def _ob_disp(direction, start_index, end_index, exhausted=True):
    return SimpleNamespace(direction=direction, start_index=start_index, end_index=end_index,
                           exhausted=exhausted, id="disp-x")


def test_order_block_detector_geometry_and_lifecycle():
    """Order Block (the first from-scratch, candle-body plugin): picks the last opposing-close candle
    before the impulse, ref = its 50%, invalidation = its far edge, and a causal lifecycle read from
    realised bars only. No engine involvement."""
    from ict_v2 import entry_models as EM
    # bar0 = down candle (the OB) before a bullish impulse (bars 1..3), then a pullback into the zone
    ob = _ob_bar(110, 112, 98, 100)                 # 50% = 105, invalidation (low) = 98
    up1, up2, top = _ob_bar(100, 120, 99, 118), _ob_bar(118, 125, 117, 124), _ob_bar(124, 130, 123, 129)
    retest = _ob_bar(129, 129, 108, 112)            # low 108 ≤ OB.high 112 → traded back into the zone
    bars = [ob, up1, up2, top, retest]
    disp = _ob_disp("bullish", start_index=1, end_index=3)
    got = EM.order_block_entries(disp, None, None, "long", bars)
    assert len(got) == 1
    e = got[0]
    assert e.model == "order_block" and e.direction == "long"
    assert e.ref == 105.0 and e.invalidation == 98.0 and e.origin_index == 0
    assert e.lifecycle == "validated" and e.state == "valid"     # retest with no prior invalidation

    # awaiting_retest: impulse exhausted but price never returns to the zone and never breaks it
    away = [ob, up1, up2, top, _ob_bar(129, 135, 128, 134)]
    e2 = EM.order_block_entries(_ob_disp("bullish", 1, 3), None, None, "long", away)[0]
    assert e2.lifecycle == "awaiting_retest" and e2.state == "waiting"

    # invalidated: a close below the OB low before any retest
    broke = [ob, up1, _ob_bar(118, 119, 90, 92)]     # close 92 < OB.low 98
    e3 = EM.order_block_entries(_ob_disp("bullish", 1, 2), None, None, "long", broke)[0]
    assert e3.lifecycle == "invalidated" and e3.state == "rejected"

    # identified: impulse not yet exhausted (still in progress at the cursor)
    e4 = EM.order_block_entries(_ob_disp("bullish", 1, 3, exhausted=False), None, None, "long",
                                [ob, up1, up2, top])[0]
    assert e4.lifecycle == "identified" and e4.state == "waiting"

    # bearish mirror: last up-close candle before a down impulse → short, invalidation = OB high
    obs = _ob_bar(100, 112, 98, 110)                 # up candle, 50% = 105, invalidation (high) = 112
    dn1, dn2, bot = _ob_bar(110, 111, 90, 92), _ob_bar(92, 93, 80, 82), _ob_bar(82, 83, 70, 71)
    es = EM.order_block_entries(_ob_disp("bearish", 1, 3, exhausted=False), None, None, "short",
                                [obs, dn1, dn2, bot])[0]
    assert es.direction == "short" and es.ref == 105.0 and es.invalidation == 112.0

    # empty bars → no entry (the v1.1 bars arg is mandatory for a candle-body model)
    assert EM.order_block_entries(_ob_disp("bullish", 1, 3), None, None, "long", None) == []


def test_order_block_uses_the_same_contract_and_generic_geometry():
    """An OB entry is the SAME Entry contract as FVG and assembles through the universal geometry —
    the engine treats it identically (proof the architecture is generic)."""
    from ict_v2 import entry_models as EM
    FIELDS = {"model", "direction", "ref", "invalidation", "state", "lifecycle", "quality", "reason"}
    ob = _ob_bar(110, 112, 98, 100)
    bars = [ob, _ob_bar(100, 120, 99, 118), _ob_bar(118, 125, 117, 124), _ob_bar(124, 130, 123, 129),
            _ob_bar(129, 129, 108, 112)]
    e = EM.order_block_entries(_ob_disp("bullish", 1, 3), None, None, "long", bars)[0]
    assert set(e.to_dict()) == FIELDS
    g = EM.assemble(e, sweep_extreme=97.0, active_erl=[SimpleNamespace(kind="high", price=130.0)], min_stop=2.0)
    assert g["stop"] == 97.0 and g["target"] == 130.0 and g["reject"] == "" and g["rr"] == 3.12


def test_engine_has_no_model_specific_branching():
    """The invariant, encoded as a test: the engine (pipeline) must never branch on a model NAME.
    Adding Order Block required ZERO such branches — models are pure registry plugins."""
    import pathlib
    src = pathlib.Path(v2.__file__).read_text()
    lowered = src.lower()
    for needle in ('== "fvg"', "== 'fvg'", '== "order_block"', "== 'order_block'",
                   'model == ', 'entry_model == '):
        assert needle not in lowered, f"engine branches on a model name: {needle!r}"


def test_order_block_flows_through_the_cascade_generically(monkeypatch):
    """Enabling Order Block runs the full four-layer cascade with no engine change; every OB candidate
    that carries an entry is tagged `order_block` and conforms to the contract."""
    from ict_v2 import entry_models as EM
    found_ob = False
    for seed in range(1, 40):
        base = v2._base_1m(20000, seed)
        st = v2.analyze_mtf(v2.resample(base, 240, "4H"), v2.resample(base, 60, "1H"),
                            v2.resample(base, 15, "15m"), base[-400:],
                            entry_models=("fvg", "order_block"))
        for stage in (st.setup, st.confirmation):
            for c in stage.cand_info:
                if c["entry_model"] == "order_block":
                    found_ob = True
                    eo = c["entry_obj"]
                    assert eo and eo["model"] == "order_block"
                    assert eo["lifecycle"] in EM.LIFECYCLE["order_block"]["vocab"]
                    assert eo["state"] in EM.COMMON_STATES
    assert found_ob, "Order Block never produced a candidate across 39 seeds — detector not wired"
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
