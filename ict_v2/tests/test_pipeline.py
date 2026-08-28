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


def test_session_and_killzone_context(monkeypatch):
    """METHODOLOGY §11 (lesson 5): track Asia/London/NY-AM/NY-PM, ET/DST-correct, as CONTEXT (not a
    gate). session_of maps a timestamp to (session, killzone); every candidate is tagged with the
    session/killzone of its manipulation; the fields serialize for the dashboard."""
    from datetime import datetime, timezone
    utc = timezone.utc
    # 2026-06-01 is EDT (UTC-4): 09:00 ET = 13:00 UTC (NY-AM), 03:00 ET = 07:00 UTC (London),
    # 18:00 ET = 22:00 UTC (Asia, not a trading killzone), 12:00 ET = 16:00 UTC (between windows).
    assert v2.session_of(datetime(2026, 6, 1, 13, 0, tzinfo=utc)) == ("ny_am", "ny_am")
    assert v2.session_of(datetime(2026, 6, 1, 7, 0, tzinfo=utc)) == ("london_active", "london_active")
    assert v2.session_of(datetime(2026, 6, 1, 22, 0, tzinfo=utc)) == ("asia", "")   # asia ≠ killzone
    assert v2.session_of(datetime(2026, 6, 1, 16, 0, tzinfo=utc)) == ("", "")        # no window
    # naive datetimes are assumed UTC (mirrors live _et_iso), so ET conversion is still correct
    assert v2.session_of(datetime(2026, 6, 1, 13, 0)) == ("ny_am", "ny_am")
    assert v2.session_of(None) == ("", "")
    # every candidate carries the session/killzone of its manipulation, and it serializes
    st = v2.demo_state(seed=7)
    allowed = {"asia", "london_active", "ny_am", "ny_pm", ""}
    for c in st.setup.cand_info:
        assert c["session"] in allowed and c["killzone"] in {"london_active", "ny_am", "ny_pm", ""}
        if c["killzone"]:
            assert c["killzone"] == c["session"]           # a trading killzone is that same window


def test_htf_context_labels(monkeypatch):
    """METHODOLOGY §17 (lesson 8/15): each setup is LABELLED by its HTF context relationship — a
    label, never a veto. Alignment axis is computable now (aligned/counter/neutral); the AMD-phase
    refinement (possible-manipulation/distribution) is deferred to §10 and not invented here."""
    assert v2.context_label("long", "long") == "htf-aligned"
    assert v2.context_label("short", "short") == "htf-aligned"
    assert v2.context_label("long", "short") == "counter-context"
    assert v2.context_label("short", "long") == "counter-context"
    assert v2.context_label("long", "neutral") == "neutral-context"
    assert v2.context_label("long", "") == "neutral-context"
    for lbl in ("htf-aligned", "counter-context", "neutral-context"):
        assert lbl in v2.CONTEXT_LABELS
    # every candidate carries a valid label, and it matches direction-vs-bias when a bias exists
    st = v2.demo_state(seed=7)
    bias = st.context.bias
    for c in st.setup.cand_info:
        assert c["context_label"] in v2.CONTEXT_LABELS
        if bias in ("long", "short"):
            expect = "htf-aligned" if c["direction"] == bias else "counter-context"
            assert c["context_label"] == expect
        else:
            assert c["context_label"] == "neutral-context"


def test_amd_phase():
    """Power-of-3 / AMD (Lesson 16, §10): manipulation (the counter-move sweep) → distribution (the
    real move WITH the trend), the transition being a confirmed MSS aligned with the HTF bias.
    'accumulation' is never emitted (consolidation detector is parameter-undefined)."""
    assert v2.amd_phase("long", "long", "confirmed") == "distribution"   # aligned + confirmed = real move
    assert v2.amd_phase("short", "short", "confirmed") == "distribution"
    assert v2.amd_phase("long", "long", "candidate") == "manipulation"   # not yet confirmed
    assert v2.amd_phase("long", "short", "confirmed") == "manipulation"  # counter to HTF → still manip
    assert v2.amd_phase("long", "neutral", "confirmed") == "manipulation"
    assert v2.amd_phase("long", "long", "") == "manipulation"            # only swept
    assert "accumulation" not in {v2.amd_phase("long", "long", s) for s in ("", "potential", "candidate", "confirmed")}
    assert set(v2.AMD_PHASES) == {"accumulation", "manipulation", "distribution", ""}
    # every candidate carries a valid phase, serialized
    st = v2.demo_state(seed=7)
    assert all(c["amd_phase"] in v2.AMD_PHASES for c in st.setup.cand_info)


def test_trend_state_verdict():
    """Lesson 15 / §2/§21: trend = up (higher highs AND higher lows) / down (lower/lower) / none;
    change = confirmed (a confirmed MSS) / potential (potential-or-candidate MSS) / ''. A verdict over
    v1's existing skeleton + MSS — no new structural detection."""
    def sw(kind, price, i):
        return SimpleNamespace(kind=kind, price=price, index=i)

    def ms(highs, lows, mss_states=()):
        structural = [sw("high", p, 2 * i) for i, p in enumerate(highs)] + \
                     [sw("low", p, 2 * i + 1) for i, p in enumerate(lows)]
        ranked = [SimpleNamespace(item=SimpleNamespace(state=s)) for s in mss_states]
        return SimpleNamespace(structural=structural, ranked_mss=ranked)

    assert v2.trend_state(ms([100, 110], [90, 95]))["trend"] == "up"      # HH + HL
    assert v2.trend_state(ms([110, 100], [95, 90]))["trend"] == "down"    # LH + LL
    assert v2.trend_state(ms([100, 110], [95, 90]))["trend"] == "none"    # HH but LL → transition
    assert v2.trend_state(ms([100], [90]))["trend"] == "none"             # not enough swings
    assert v2.trend_state(ms([100, 110], [90, 95], ("confirmed",)))["change"] == "confirmed"
    assert v2.trend_state(ms([100, 110], [90, 95], ("candidate",)))["change"] == "potential"
    assert v2.trend_state(ms([100, 110], [90, 95], ()))["change"] == ""
    # surfaced on the context + snapshot
    st = v2.demo_state(seed=7)
    assert st.context.trend in ("up", "down", "none")
    assert st.context.trend_change in ("confirmed", "potential", "")


def test_erl_irl_classification():
    """METHODOLOGY §4 / Lesson 10: a price ABOVE the range high or BELOW the range low is EXTERNAL
    range liquidity (ERL); a price BETWEEN low and high is INTERNAL (IRL). Relative to the active
    dealing range; None without a range."""
    dr = SimpleNamespace(source_tf="4H", low=100.0, high=200.0, ce=150.0, direction="up")
    ctx = v2.HTFContext(tf="4H", bias="long", dealing_range=dr)
    assert ctx.erl_irl(250.0) == "ERL" and ctx.erl_irl(90.0) == "ERL"   # outside the range
    assert ctx.erl_irl(150.0) == "IRL" and ctx.erl_irl(120.0) == "IRL"  # inside the range
    assert ctx.erl_irl(100.0) == "IRL" and ctx.erl_irl(200.0) == "IRL"  # boundaries are inclusive-inside
    assert ctx.erl_irl(None) is None
    assert v2.HTFContext(tf="4H", bias="neutral", dealing_range=None).erl_irl(150.0) is None
    # surfaced: every pool in the snapshot carries an ERL/IRL loc tag
    st = v2.demo_state(seed=7)
    # (demo has a range; pools should be classified) — checked via the live snapshot in test_pdarrays/live


def test_fib_ladder_levels_and_orientation():
    """METHODOLOGY §6 / Lesson 8: the fib ladder = 0/0.5/0.62/0.79/1 on the dealing range, 0.5 =
    equilibrium. Orientation per the course: UPtrend → 0 at HIGH, 1 at LOW; DOWNtrend → 0 at LOW,
    1 at HIGH. Premium/discount by price vs equilibrium (Lesson 9, direction-agnostic)."""
    dr_up = SimpleNamespace(source_tf="4H", low=100.0, high=200.0, ce=150.0, direction="up")
    ctx = v2.HTFContext(tf="4H", bias="long", dealing_range=dr_up)
    lv = {round(x["level"], 2): x for x in ctx.fib_levels()}
    assert set(lv) == {0.0, 0.5, 0.62, 0.79, 1.0}
    assert lv[0.0]["price"] == 200.0 and lv[1.0]["price"] == 100.0      # up: 0 at high, 1 at low
    assert lv[0.5]["price"] == 150.0 and lv[0.5]["zone"] == "equilibrium"
    assert lv[0.62]["price"] == 138.0 and lv[0.62]["zone"] == "discount"  # 200-0.62*100
    assert lv[0.79]["price"] == 121.0 and lv[0.79]["zone"] == "discount"  # OTE in discount → longs
    # downtrend mirrors: 0 at low, 1 at high; OTE in premium
    dr_dn = SimpleNamespace(source_tf="4H", low=100.0, high=200.0, ce=150.0, direction="down")
    lv2 = {round(x["level"], 2): x for x in v2.HTFContext(tf="4H", bias="short", dealing_range=dr_dn).fib_levels()}
    assert lv2[0.0]["price"] == 100.0 and lv2[1.0]["price"] == 200.0
    assert lv2[0.62]["price"] == 162.0 and lv2[0.62]["zone"] == "premium"   # OTE in premium → shorts
    assert v2.HTFContext(tf="4H", bias="neutral", dealing_range=None).fib_levels() == []


def test_full_pool_set_and_nested_ranges_surfaced():
    """§3 (Lesson 6): the FULL liquidity-pool set (BSL/SSL) is surfaced, not just the single draw.
    §5/§6: each cascade stage's dealing range is exposed (source_tf-tagged) as the nested hierarchy."""
    from ict_v2 import live as v2live
    from ict_live.market.bar import Bar
    from datetime import datetime, timedelta, timezone
    t0 = datetime(2026, 6, 1, tzinfo=timezone.utc)
    bars = []
    x = 5
    px = 20000.0
    for i in range(1600):
        x = (1103515245 * x + 12345) % (2 ** 31)
        o = px; c = px + ((x % 25) - 12) * 0.6
        ot = t0 + timedelta(minutes=i)
        bars.append(Bar("1m", ot, ot + timedelta(minutes=1), o, max(o, c) + (x % 5) * 0.4,
                        min(o, c) - (x % 4) * 0.4, c, 100.0)); px = c
    v = v2live.run_bars(bars)
    snap = v.snapshot()
    ctx = snap["strategic"]                               # H4 strategic context
    assert ctx is not None
    assert isinstance(ctx["pools"], list) and isinstance(ctx["fib"], list)  # full set + ladder present
    assert "dealing_ranges" in snap and isinstance(snap["dealing_ranges"], list)
    for r in snap["dealing_ranges"]:                     # each nested range is source_tf-tagged
        assert set(r) == {"tf", "low", "high", "ce", "direction"} and r["tf"]
    for p in ctx["pools"]:
        assert p["kind"] in ("BSL", "SSL")
        assert p.get("loc") in ("ERL", "IRL", None)          # §4 ERL/IRL tag surfaced per pool


def test_four_layer_cascade_runs():
    st = v2.demo_state(seed=7)
    assert st.context.tf == "4H" and st.setup.tf == "1H" and st.confirmation.tf == "15m"
    assert st.execution.tf == "1m"
    assert st.context.bias in ("long", "short", "neutral")
    assert isinstance(st.setup.candidates, list) and isinstance(st.confirmation.gated, list)
    d = st.describe()
    assert "CONTEXT" in d and "SETUP" in d and "CONFIRM" in d and "EXECUTION" in d


def test_liquidity_floor_15m_and_pullback():
    """Lesson 6/8: (a) liquidity/structure TFs must be ≥15m ('do not mark liquidity below 15m') —
    the execution trigger may be finer; (b) a pullback should retrace ≥50% of the leg (a quality read)."""
    import pytest
    from ict_v2.engine import MTFEngine
    assert v2.tf_minutes("4H") == 240 and v2.tf_minutes("15m") == 15 and v2.tf_minutes("1m") == 1
    assert v2.tf_minutes("D") == 1440 and v2.tf_minutes("W") == 10080
    v2.assert_liquidity_floor("4H", "1H", "15m")             # ok — all ≥15m
    v2.assert_liquidity_floor("15m", "15m", "15m")           # ok — exactly the floor
    with pytest.raises(ValueError):
        v2.assert_liquidity_floor("4H", "5m", "15m")         # 5m structural TF violates the floor
    # the engine enforces it: a <15m structural TF is rejected; a <15m TRIGGER is fine
    MTFEngine("4H", "1H", "15m", "1m")                       # ok (1m is the trigger, not liquidity)
    with pytest.raises(ValueError):
        MTFEngine("4H", "5m", "15m", "1m")                   # 5m as the SETUP TF → rejected
    # pullback depth of the entry into the displacement leg
    disp = SimpleNamespace(start_price=100.0, end_price=200.0)   # a 100-pt up leg
    assert v2.pullback_pct(disp, 150.0) == 0.5               # entry at the midpoint = 50% retrace
    assert v2.pullback_pct(disp, 130.0) == 0.7               # deeper (from the 200 end back to 130)
    assert v2.pullback_pct(disp, 180.0) == 0.2               # shallow
    assert v2.pullback_pct(None, 150.0) is None
    # surfaced on candidates that reached an entry
    st = v2.demo_state(seed=7)
    withentry = [c for c in st.setup.cand_info if c["entry_obj"]]
    assert withentry and all(c["pullback"] is None or 0.0 <= c["pullback"] <= 2.0 for c in withentry)


def test_fvg_tiebreak_is_explicit_res():
    """The multi-FVG tie-break is an EXPLICIT [RES:fvg_tiebreak] placeholder (the course defines no
    rule), not silent behaviour: a named rule constant exists, and every candidate reports how many
    unmitigated FVGs its entry was chosen among (>1 ⇒ the tie-break was exercised, flagged live)."""
    from ict_v2 import entry_models as EM
    assert EM.FVG_TIEBREAK_RULE == "v1_rank"                # documented placeholder, NOT course methodology
    exercised = 0
    for seed in range(1, 40):
        for c in v2.demo_state(seed).setup.cand_info:
            assert "fvg_tiebreak" in c and c["fvg_tiebreak"] >= 0   # count of UNMITIGATED FVGs on the leg
            if c["fvg_tiebreak"] > 1:
                exercised += 1                            # the [RES] tie-break actually chose among >1
    assert exercised > 0                                  # the rare multi-unmitigated-FVG case occurs & is surfaced


def test_no_duplicate_candidates():
    """Equal-high/low sweeps at the SAME bar/level share one displacement → one FVG → the same trade
    idea twice. generate_candidates de-duplicates exact matches (dir+entry+stop+target+model), so no
    two candidates on a stage describe the identical setup."""
    for seed in range(1, 40):
        for stage in (v2.demo_state(seed).setup,):
            keys = [(c["direction"], c["entry"], c["stop"], c["target"], c["entry_model"])
                    for c in stage.cand_info]
            assert len(keys) == len(set(keys)), f"duplicate candidate on seed {seed}"
            ids = [c["id"] for c in stage.cand_info]
            assert len(ids) == len(set(ids)), f"repeated setup id on seed {seed}"


def test_incomplete_candidates_say_what_they_wait_for():
    """WATCH candidates explain precisely what is awaited — displacement / MSS / entry FVG / retrace —
    instead of a bare 'entry waiting'."""
    wanted = {"displacement", "market-structure shift", "entry FVG", "retrace"}
    seen = set()
    for seed in range(1, 40):
        for c in v2.demo_state(seed).setup.cand_info:
            if c["recommendation"] == "WATCH":
                r = " ".join(c["reasons"]).lower()
                assert r.strip(), "a WATCH candidate must say what it is waiting for"
                for w in wanted:
                    if w.lower() in r:
                        seen.add(w)
            # an armed (valid + filters pass) but un-retraced FVG is WATCH with a 'retrace' reason
            if c["structure"] == "valid" and c["entry_obj"] and c["entry_obj"]["lifecycle"] == "waiting" \
                    and all(f["ok"] for f in c["filters"]):
                assert c["recommendation"] == "WATCH"
                assert any("retrace" in r.lower() for r in c["reasons"])
    assert {"displacement", "market-structure shift", "entry FVG"} <= seen   # the forming reasons appear
    # each candidate names WHICH displacement leg it tracks, so look-alikes are distinguishable
    st = v2.demo_state(seed=11)
    withleg = [c for c in st.setup.cand_info if c["leg"]]
    assert withleg
    for c in withleg:
        lg = c["leg"]
        assert set(lg) == {"from", "to", "bars", "dir", "id"} and lg["from"] is not None
    # the "waiting for FVG" (mss-state) reasons embed the leg span (price arrow) so they differ
    fvgwait = [c for c in st.setup.cand_info if "entry FVG on the displacement leg" in " ".join(c["reasons"])]
    assert len(fvgwait) >= 2 and len({r for c in fvgwait for r in c["reasons"]}) == len(fvgwait)  # all distinct


def test_four_layer_semantics_and_no_bias_veto():
    """The semantic layer on real candidates: every candidate carries structure / filters /
    recommendation; TAKE ⟺ valid structure + all course filters pass; and — critically — a
    counter-context (counter-HTF-bias) setup can still be TAKE (HTF bias is quality, NOT a veto)."""
    from ict_v2 import recommend as REC
    seen_take = seen_counter_take = seen_skip_filter = 0
    for seed in range(1, 40):
        st = v2.demo_state(seed)
        for c in st.setup.cand_info:
            assert c["structure"] in ("forming", "valid", "invalid")
            assert c["recommendation"] in REC.RECOMMENDATIONS
            # TAKE iff valid structure AND all course filters pass
            if c["recommendation"] == "TAKE":
                assert c["structure"] == "valid" and all(f["ok"] for f in c["filters"])
                seen_take += 1
                if c["context_label"] == "counter-context":
                    seen_counter_take += 1                 # counter-bias TAKE ⇒ bias is not a veto
            # a valid setup with a failing filter is SKIP (not invalid, not WATCH)
            if c["structure"] == "valid" and c["filters"] and not all(f["ok"] for f in c["filters"]):
                assert c["recommendation"] == "SKIP"
                seen_skip_filter += 1
            # forming ⇒ WATCH; invalid ⇒ SKIP
            if c["structure"] == "forming":
                assert c["recommendation"] == "WATCH"
            if c["structure"] == "invalid":
                assert c["recommendation"] == "SKIP"
    assert seen_take and seen_counter_take and seen_skip_filter    # all three behaviours actually occur


def test_execution_only_fires_when_whole_cascade_holds():
    st = v2.demo_state(seed=7)
    if st.execution.executables:                          # a real trade requires the FULL chain of TAKEs
        assert st.setup.gated and st.confirmation.gated   # every layer produced a TAKE (structure+filters)
        for ex in st.execution.executables:
            assert ex.direction in ("long", "short")      # a concrete side (NOT required to equal HTF bias —
            #                                               HTF bias is context/quality now, not a gate)
    else:
        assert st.execution.decision.startswith("NO-TRADE")


def test_execution_for_reports_the_stage_reached():
    # staged NO-TRADE messages (early returns, no bars needed). HTF bias is NOT a gate anymore, so a
    # neutral/absent bias no longer short-circuits — the stage reported is purely about the cascade.
    ctx = SimpleNamespace(bias="neutral")                 # neutral bias must NOT block (context, not veto)
    gated = SimpleNamespace(gated=[SimpleNamespace(setup=SimpleNamespace(direction="long"))])
    empty = SimpleNamespace(gated=[])
    assert "no 1H setup" in v2.execution_for(None, "1m", ctx, empty, gated).decision
    assert "awaiting 15m confirmation" in v2.execution_for(None, "1m", ctx, gated, empty).decision
    # neutral bias with a full gated cascade still proceeds (no "context bias" veto)
    assert "no context bias" not in v2.execution_for(None, "1m", ctx, gated, gated).decision
