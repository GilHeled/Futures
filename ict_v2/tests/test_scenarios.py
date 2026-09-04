"""Scenario layer: a small STABLE set of theses (target 2 / max 3), built from liquidity objectives,
persisted by structural identity, changed only on structural events (not price noise)."""
from types import SimpleNamespace

from ict_v2 import liquidity as LQ
from ict_v2 import scenarios as SC


def _dr(low=100, high=200):
    ce = (low + high) / 2.0
    return SimpleNamespace(low=low, high=high, ce=ce, direction="up")


def _ctx(bias="long", dr=None):
    return SimpleNamespace(bias=bias, dealing_range=dr or _dr())


def _draw(side, price, kind="swing", tf="4H", cls="ERL", strength=0.8, status="unswept"):
    return LQ.LiquidityObjective(kind=kind, tf=tf, side=side, price=price, liquidity_class=cls,
                                 strength=strength, role="draw", status=status)


def test_build_scenarios_directed_and_ranked():
    strat = _ctx(bias="long")
    objs = [_draw("high", 260, strength=0.8), _draw("high", 240, strength=0.6, cls="IRL"),
            _draw("low", 40, strength=0.7)]
    sc = SC.build_scenarios(strat, _ctx(bias="long"), objs, price=190)
    assert sc and sc[0].direction == "long"                          # top scenario aligned with bias
    assert all(s.direction == ("long" if s.draw.side == "high" else "short") for s in sc)
    assert sc[0].entry_zone == (100, 150)                            # long → discount (low→CE)
    assert [s.rank for s in sc] == list(range(len(sc)))              # ranked 0..n


def test_active_set_capped_and_hysteresis():
    strat = _ctx(bias="long")
    objs = [_draw("high", 260, strength=0.9), _draw("high", 250, strength=0.8),
            _draw("high", 240, strength=0.7), _draw("high", 230, strength=0.6)]
    book = SC.ScenarioBook(target=2, maxn=3)
    props = SC.build_scenarios(strat, strat, objs, price=190)
    active = book.observe(props, context_key="t0", cur_range_key=SC._range_key(strat.dealing_range))
    assert len(active) <= 3                                          # never more than max
    # a 4th, weaker draw must NOT displace the incumbents (admitted only into the top-2 band)
    assert active[0].scenario_id == props[0].scenario_id


def test_stability_same_context_is_identical_set():
    strat = _ctx(bias="long")
    objs = [_draw("high", 260), _draw("high", 240, strength=0.6)]
    book = SC.ScenarioBook()
    rk = SC._range_key(strat.dealing_range)
    a1 = [s.scenario_id for s in book.observe(SC.build_scenarios(strat, strat, objs, price=190),
                                              context_key="t0", cur_range_key=rk)]
    ids1 = [s for s in book.active]
    # same context again (a later context close, nothing structural changed) → identical membership, SAME objects
    a2 = [s.scenario_id for s in book.observe(SC.build_scenarios(strat, strat, objs, price=191),
                                              context_key="t1", cur_range_key=rk)]
    assert a1 == a2                                                  # membership stable
    assert book.active == ids1                                       # persisted IN PLACE (same objects)
    assert book.active[0].created_ctx == "t0"                        # not recreated on the 2nd close


def test_price_noise_does_not_churn_the_set():
    strat = _ctx(bias="long")
    objs = [_draw("high", 260), _draw("high", 240, strength=0.6)]
    book = SC.ScenarioBook()
    rk = SC._range_key(strat.dealing_range)
    book.observe(SC.build_scenarios(strat, strat, objs, price=150), context_key="t0", cur_range_key=rk)
    before = [s.scenario_id for s in book.active]
    # price drifts across many ticks — draws (structural levels) unchanged → set unchanged
    for p in (151, 149, 155, 148, 160, 145):
        book.observe(SC.build_scenarios(strat, strat, objs, price=p), context_key="t", cur_range_key=rk)
    assert [s.scenario_id for s in book.active] == before            # zero churn on noise


def test_draw_taken_resolves_and_frees_a_slot():
    strat = _ctx(bias="long")
    book = SC.ScenarioBook(target=2, maxn=3)
    rk = SC._range_key(strat.dealing_range)
    objs = [_draw("high", 260), _draw("low", 40)]                    # a long + a short = 2 distinct trades
    book.observe(SC.build_scenarios(strat, strat, objs, price=190), context_key="t0", cur_range_key=rk)
    assert len(book.active) == 2
    # the long draw (260) gets taken → drops from the objective set → its scenario leaves the book
    objs2 = [_draw("low", 40)]
    book.observe(SC.build_scenarios(strat, strat, objs2, price=190), context_key="t1", cur_range_key=rk)
    ids = [s.scenario_id for s in book.active]
    assert not any(i.startswith("long:") for i in ids)               # the taken long scenario is gone
    assert any(s.scenario_id.startswith("long:") for s in book.retired)


def test_range_change_invalidates_old_scenarios():
    book = SC.ScenarioBook()
    strat1 = _ctx(bias="long", dr=_dr(100, 200))
    rk1 = SC._range_key(strat1.dealing_range)
    book.observe(SC.build_scenarios(strat1, strat1, [_draw("high", 260)], price=190),
                 context_key="t0", cur_range_key=rk1)
    assert book.active
    # a new dealing range supersedes the old one → scenarios defined against the old range invalidate
    strat2 = _ctx(bias="long", dr=_dr(300, 400))
    rk2 = SC._range_key(strat2.dealing_range)
    book.observe(SC.build_scenarios(strat2, strat2, [_draw("high", 460)], price=390),
                 context_key="t1", cur_range_key=rk2)
    assert all(s.basis["range_key"] == rk2 for s in book.active)     # only current-range theses remain


def test_monitor_updates_state_without_touching_membership():
    strat = _ctx(bias="long")
    book = SC.ScenarioBook()
    rk = SC._range_key(strat.dealing_range)
    book.observe(SC.build_scenarios(strat, strat, [_draw("high", 260)], price=190),
                 context_key="t0", cur_range_key=rk)
    ids_before = [s.scenario_id for s in book.active]
    book.monitor(lambda s: {"state": "armed", "entry": 150})         # execution says "armed"
    assert [s.scenario_id for s in book.active] == ids_before        # membership untouched
    assert book.active[0].state == "armed" and book.active[0].execution["entry"] == 150


# ---- FAITHFUL execution model (Lessons 5-16): the causal chain WHERE→WHEN→retrace-entry -----------
# Mock a full causal reversal chain candidate: a manipulation SWEEP (the WHERE) → a displacement leg →
# a (confirmed?) MSS (the WHEN) → an optional same-leg FVG. The entry is the >=50% retrace of the leg
# (Lesson 8) inside the discount/premium half (Lesson 9) — NOT the swept WHERE reference.
from types import SimpleNamespace as _N


def _fvg_src(ce, top, bottom, direction="long", status="unfilled"):
    """A same-leg displacement FVG (the OPTIONAL confluence sharpener), as v1 exposes it on a candidate."""
    return _N(ce=ce, top=top, bottom=bottom,
              direction=("bullish" if direction == "long" else "bearish"), status=status, id="FVG")


def _draw_obj(price, side="high"):
    return _N(kind="swing", tf="1H", side=side, price=price, top=None, bottom=None, status="unswept",
              label=("BSL" if side == "high" else "SSL"), to_dict=lambda: {"kind": "swing", "price": price})


def _chain(direction="long", manip=100, impulse=140, confirmed=True, fvg=None, significant=True):
    """A causal reversal chain: sweep (manipulation at `manip`, the WHERE) → displacement (manip→impulse,
    the confirming leg) → MSS (confirmed?) → optional same-leg FVG. Long: manip is the low, impulse the
    high; short mirrors. `significant` sets whether the MSS broke a dominant/protected swing (Lessons 6/15);
    False = broke only a minor swing (should be rejected)."""
    pol = "bullish" if direction == "long" else "bearish"
    sw = _N(extreme=manip, pool_price=manip, id="SW", direction=pol)
    disp = _N(start_price=manip, end_price=impulse, net=abs(impulse - manip), span=5, id="D",
              depends_on=("SW",))
    mss = _N(state=("confirmed" if confirmed else "candidate"), direction=pol, confirm_index=9,
             broken_price=impulse, broken_index=3, acceptance=1.0, id="MSS", depends_on=("D",),
             broken_dominant=bool(significant), broken_protected=False)
    return _N(direction=direction, sweep=sw, displacement=disp, mss=mss, entry_obj=_N(source=fvg), tf="1m")


def test_minor_swing_mss_is_rejected_significant_required():
    """FIDELITY (Lessons 6/15, threshold-free): a CONFIRMED MSS that broke only a MINOR swing (not dominant
    nor protected) is NOT the meaningful structural reversal — rejected (watching), never triggered. The
    audit records the broken-swing significance and the accept/reject reason. A dominant/protected break
    proceeds."""
    from ict_v2 import pipeline as P
    sc = _N(direction="long", entry_zone=(100, 150), draw=_N(price=210), tf="1m")
    objs = [_draw_obj(210)]
    # confirmed MSS but broke a MINOR swing → rejected → WATCHING (not triggered), audit says why
    r = P.execution_for_scenario(sc, [_chain("long", 100, 140, significant=False)], price=120, objectives=objs)
    assert r["state"] == "watching" and "MINOR swing" in r["why"]
    assert r["audit"]["when"]["accepted"] is False and r["audit"]["rejected_minor_mss"] == 1
    assert r["audit"]["when"]["broken_dominant"] is False and r["audit"]["when"]["broken_protected"] is False
    # broke a DOMINANT (significant) swing → triggered; audit records the accepted significance
    r2 = P.execution_for_scenario(sc, [_chain("long", 100, 140, significant=True)], price=120, objectives=objs)
    assert r2["state"] == "triggered" and r2["audit"]["when"]["accepted"] is True
    assert r2["audit"]["when"]["broken_dominant"] is True and r2["audit"]["conditions"]["C3_confirmed_significant_mss"]


def test_execution_requires_where_then_when_then_retrace():
    """The causal sequence: a manipulation SWEEP in the correct half (WHERE) → a CONFIRMED MSS on that
    same chain (WHEN) → a >=50% retrace of the confirming leg into the P/D half → entry. Each alone is
    insufficient (Lessons 8/9/15/16)."""
    from ict_v2 import pipeline as P
    sc = _N(direction="long", entry_zone=(100, 150), draw=_N(price=210), tf="1m")
    objs = [_draw_obj(210)]
    # WHERE swept (manip 100 in the discount) but NO confirmed MSS → WATCHING (a FORMING setup: no leg,
    # no entry/stop/target yet — must NOT read as ARMED, which means an order is ready to place)
    r = P.execution_for_scenario(sc, [_chain("long", 100, 140, confirmed=False)], price=130, objectives=objs)
    assert r["state"] == "watching" and r["entry"] is None and "awaiting" in r["why"]
    # WHERE + WHEN + >=50% retrace (leg 100→140, 50%=120; price 120) → TRIGGERED. Entry = the leg 50%
    # (no FVG) = 120, stop = manip extreme 100, target = the opposing draw 210.
    r = P.execution_for_scenario(sc, [_chain("long", 100, 140)], price=120, objectives=objs)
    assert r["state"] == "triggered" and r["entry"] == 120 and r["stop"] == 100 and r["target"] == 210
    # WHERE not in the correct half (manip 160 > equilibrium 150) → no interaction there → watching (None)
    assert P.execution_for_scenario(sc, [_chain("long", 160, 200)], price=120, objectives=objs) is None
    # AUDITABLE: WHERE (the swept manip) / WHEN (the MSS) / the reversal leg + its 50% are recorded
    a = r["audit"]
    assert a["conditions"]["C2_where_sweep"] and a["conditions"]["C3_confirmed_significant_mss"] \
        and a["conditions"]["C4_retrace_50_and_pd"]
    assert a["where"]["manip"] == 100 and a["reversal_leg"]["mid_50pct"] == 120


def test_no_fvg_setup_still_executes_and_fvg_only_sharpens():
    """FVG is OPTIONAL (no FVG != no trade). Without a same-leg FVG the entry is the leg >=50% level; WITH
    one in the valid band the entry SHARPENS to the FVG CE. Lessons 12/8."""
    from ict_v2 import pipeline as P
    sc = _N(direction="long", entry_zone=(100, 150), draw=_N(price=210), tf="1m")
    objs = [_draw_obj(210)]
    # NO FVG → entry = the leg 50% (120), still triggers; model tagged 'retrace'
    r = P.execution_for_scenario(sc, [_chain("long", 100, 140, fvg=None)], price=120, objectives=objs)
    assert r["state"] == "triggered" and r["entry"] == 120 and r["entry_model"] == "retrace"
    # WITH a same-leg FVG at CE 110 (inside the >=50%+discount band [100,120]) → entry sharpens to 110
    fvg = _fvg_src(110, 112, 108, "long")
    r2 = P.execution_for_scenario(sc, [_chain("long", 100, 140, fvg=fvg)], price=110, objectives=objs)
    assert r2["state"] == "triggered" and r2["entry"] == 110 and r2["entry_model"] == "fvg"


def test_entry_is_the_retrace_not_the_swept_where():
    """The entry must NOT be the swept WHERE reference (Lesson 6/point 7): it is the post-confirmation
    >=50% retrace of the leg. Swept WHERE = 100; entry = 120, never 100."""
    from ict_v2 import pipeline as P
    sc = _N(direction="long", entry_zone=(100, 150), draw=_N(price=210), tf="1m")
    r = P.execution_for_scenario(sc, [_chain("long", 100, 140)], price=120, objectives=[_draw_obj(210)])
    assert r["entry"] == 120 and r["entry"] != 100


def test_requires_50pct_retrace_055_is_valid_no_062_gate():
    """C4: entry needs a >=50% retrace of the leg — a 55% retrace is valid (no 0.62 requirement). A 45%
    retrace (price still above the 50% level) is ARMED, not triggered. Lesson 8."""
    from ict_v2 import pipeline as P
    sc = _N(direction="long", entry_zone=(100, 150), draw=_N(price=210), tf="1m")
    objs = [_draw_obj(210)]
    chain = _chain("long", 100, 140)                       # leg 100→140, 50% = 120
    # price 122 = 45% retrace (above the 50% level 120) → ARMED (not yet >=50%)
    assert P.execution_for_scenario(sc, [chain], price=122, objectives=objs)["state"] == "armed"
    # price 118 = 55% retrace (past the 50% level) → TRIGGERED (>=50% satisfied, 0.62 NOT required)
    assert P.execution_for_scenario(sc, [chain], price=118, objectives=objs)["state"] == "triggered"


def test_execution_rejects_a_degenerate_stop():
    """Spec §15 (Lesson 15): a near-zero risk (a same-leg FVG a hair from the manipulation extreme) is
    REJECTED, never executed."""
    from ict_v2 import pipeline as P
    sc = _N(direction="long", entry_zone=(100, 150), draw=_N(price=210), tf="1m")
    objs = [_draw_obj(210)]
    fvg = _fvg_src(100.5, 101, 100, "long")                # CE 100.5 just above manip 100 → risk 0.5
    r = P.execution_for_scenario(sc, [_chain("long", 100, 140, fvg=fvg)], price=100.5,
                                 objectives=objs, min_stop=2.0)
    assert r["state"] == "retracing" and "degenerate" in r["why"]
    # a healthy leg-50% stop (risk 20) with the same floor → triggered
    assert P.execution_for_scenario(sc, [_chain("long", 100, 140)], price=120,
                                    objectives=objs, min_stop=2.0)["state"] == "triggered"


def test_a_missed_entry_stays_armed_not_stale():
    """A MISSED entry does NOT invalidate the setup (STALE_PROGRESS removed). Price that ran past the
    entry but is NOT beyond the stop stays ARMED (awaiting a retrace), never 'stale'."""
    from ict_v2 import pipeline as P
    sc = _N(direction="long", entry_zone=(100, 150), draw=_N(price=210), tf="1m")
    objs = [_draw_obj(210)]
    chain = _chain("long", 100, 140)                       # entry 120, stop 100
    # price 135 (above entry, not beyond stop) → ARMED (awaiting retrace), NOT stale
    r = P.execution_for_scenario(sc, [chain], price=135, objectives=objs)
    assert r["state"] == "armed" and r["state"] != "stale"
    # price 118 (>=50% retrace) → TRIGGERED
    assert P.execution_for_scenario(sc, [chain], price=118, objectives=objs)["state"] == "triggered"


def test_execution_marks_a_beyond_stop_setup_invalidated():
    """Price beyond the stop on the loss side → INVALIDATED ('stale'), even with a confirmed reversal.
    Short: manip high 200 (premium), leg 200→160, 50% = 180, entry 180, stop 200."""
    from ict_v2 import pipeline as P
    sc = _N(direction="short", entry_zone=(150, 200), draw=_N(price=90), tf="1m")
    objs = [_draw_obj(90, side="low")]
    # price 250 is ABOVE the stop 200 → invalidated
    r = P.execution_for_scenario(sc, [_chain("short", 200, 160)], price=250, objectives=objs)
    assert r["state"] == "stale" and "invalidated" in r["why"]
    # price 185 (>=50% retrace up into premium, below the stop) → triggered
    assert P.execution_for_scenario(sc, [_chain("short", 200, 160)], price=185, objectives=objs)["state"] == "triggered"


# ---- sticky trigger + outcome tracking: once triggered it stays open until stop/target -----------
from datetime import datetime, timezone as _tz


def _bar(high, low, close):
    return SimpleNamespace(high=high, low=low, close=close,
                           close_time=datetime(2026, 8, 28, 12, 0, tzinfo=_tz.utc))


def _booked(draw_price=260, side="high"):
    strat = _ctx(bias="long")
    book = SC.ScenarioBook()
    rk = SC._range_key(strat.dealing_range)
    book.observe(SC.build_scenarios(strat, strat, [_draw(side, draw_price)], price=150),
                 context_key="t0", cur_range_key=rk)
    return book, rk


def test_trigger_is_sticky_until_stop_or_target():
    book, rk = _booked()
    trig = {"state": "triggered", "entry": 120, "stop": 110, "target": 210}
    book.monitor(lambda s: trig, bar=_bar(121, 119, 120))            # trigger → open a sticky position
    s = book.active[0]
    assert s.state == "triggered" and s.position and s.position["open"]
    # the entry candidate is GONE next tick (execute_fn None) — MUST NOT revert to watching (the bug)
    book.monitor(lambda s: None, bar=_bar(125, 118, 122))
    assert s.state == "triggered" and s.position["open"]
    # price finally reaches the target → resolves to a WIN, stays until the next context close
    book.monitor(lambda s: None, bar=_bar(212, 205, 210))
    assert s.state == "target" and not s.position["open"] and s.position["result_r"] == 9.0   # (210-120)/(120-110)


def test_scenario_events_record_the_state_timeline():
    """Each state's FIRST-seen ET timestamp is recorded on the scenario (created/armed/triggered/target…)."""
    book, rk = _booked()
    s = book.active[0]
    assert "created" in s.events                                     # stamped on admission (observe)
    book.monitor(lambda s: {"state": "armed", "entry": 120, "stop": 110, "target": 210}, bar=_bar(121, 119, 120))
    assert "armed" in s.events and s.events["armed"].startswith("2026-08-28")
    book.monitor(lambda s: {"state": "triggered", "entry": 120, "stop": 110, "target": 210}, bar=_bar(121, 119, 120))
    assert "triggered" in s.events
    book.monitor(lambda s: None, bar=_bar(212, 205, 210))            # target hit
    assert s.state == "target" and "target" in s.events


def test_no_backdated_fill_open_only_when_entry_reachable_this_bar():
    """NO BACK-DATED FILLS: a trade opens only if the entry is inside the trigger bar's [low, high]
    (reachable now). A 'triggered' signal whose entry is outside the bar (the touch was earlier, price has
    moved away) must NOT open a back-dated position — it stays armed and may open LATER if price returns."""
    book, rk = _booked()
    trig = {"state": "triggered", "entry": 120, "stop": 110, "target": 210}
    # entry 120 is NOT in the bar range [130,140] (price already above it) → no fill, stays armed
    book.monitor(lambda s: trig, bar=_bar(140, 130, 135))
    assert not book.active[0].position and book.active[0].state == "armed" and len(book.trades) == 0
    # price returns and the bar trades THROUGH the entry [119,121] → a real fill opens now (same scenario)
    book.monitor(lambda s: trig, bar=_bar(121, 119, 120))
    assert book.active[0].position and book.active[0].position["open"] and len(book.trades) == 1


def test_stop_resolves_as_loss():
    book, rk = _booked()
    book.monitor(lambda s: {"state": "triggered", "entry": 120, "stop": 110, "target": 210}, bar=_bar(121, 119, 120))
    book.monitor(lambda s: None, bar=_bar(122, 108, 109))           # low 108 ≤ stop 110 → stopped
    assert book.active[0].state == "stop" and book.active[0].position["result_r"] == -1.0


def test_no_duplicate_ticket_for_an_already_open_entry():
    """An OPEN trade occupies its (direction, entry zone). A fresh same-direction thesis at the SAME entry
    zone but a DIFFERENT draw must NOT be admitted as a second ticket — it's the same position (the other
    draw is just a ladder rung). Reproduces the "two shorts, same entry" the user saw."""
    strat = _ctx(bias="short")
    book = SC.ScenarioBook()
    rk = SC._range_key(strat.dealing_range)
    book.observe(SC.build_scenarios(strat, strat, [_draw("low", 40)], price=150), context_key="t0", cur_range_key=rk)
    book.monitor(lambda s: {"state": "triggered", "entry": 160, "stop": 170, "target": 40}, bar=_bar(161, 159, 160))
    assert book.active[0].state == "triggered" and book.active[0].position["open"]
    # a later context close proposes the SAME short at the SAME premium zone but a NEARER draw (SSL 60,
    # a different scenario_id) → it must NOT become a second ticket
    book.observe(SC.build_scenarios(strat, strat, [_draw("low", 60)], price=150), context_key="t1", cur_range_key=rk)
    shorts = [s for s in book.active if s.direction == "short"]
    assert len(shorts) == 1 and shorts[0].position and shorts[0].position["open"]


def test_open_trade_survives_a_context_rebuild():
    book, rk = _booked()
    book.monitor(lambda s: {"state": "triggered", "entry": 120, "stop": 110, "target": 210}, bar=_bar(121, 119, 120))
    sid = book.active[0].scenario_id
    # a context close where the draw is GONE — an OPEN trade must NOT be churned out
    strat = _ctx(bias="long")
    book.observe(SC.build_scenarios(strat, strat, [], price=150), context_key="t1", cur_range_key=rk)
    assert any(s.scenario_id == sid and s.position["open"] for s in book.active)


def test_stats_counts_triggers_and_win_pct():
    book, rk = _booked()
    book.monitor(lambda s: {"state": "triggered", "entry": 120, "stop": 110, "target": 210}, bar=_bar(121, 119, 120))
    book.monitor(lambda s: None, bar=_bar(212, 205, 210))           # → target (win)
    st = book.stats()
    assert st["triggered"] == 1 and st["resolved"] == 1 and st["wins"] == 1
    assert st["win_pct"] == 100.0 and st["total_r"] == 9.0


def test_no_overnight_hold_closes_at_session_end():
    book, rk = _booked()
    # a trade opens during session day "D1"
    book.monitor(lambda s: {"state": "triggered", "entry": 120, "stop": 110, "target": 210},
                 bar=_bar(121, 119, 120), day="D1")
    assert book.active[0].position["open"]
    # session rolls to "D2" without hitting stop/target → the trade is CLOSED (no overnight hold),
    # exited at the prior session's last price (120) → scratch 0R
    book.monitor(lambda s: None, bar=_bar(130, 125, 128), day="D2")
    p = book.active[0].position
    assert book.active[0].state == "eod" and not p["open"] and p["outcome"] == "eod"
    assert p["exit_price"] == 120.0 and p["result_r"] == 0.0
    assert book.stats()["eod"] == 1 and book.stats()["open"] == 0


def test_dollar_pnl_per_trade():
    strat = _ctx(bias="long")
    book = SC.ScenarioBook(point_value=5.0)                          # MES = $5/pt
    rk = SC._range_key(strat.dealing_range)
    book.observe(SC.build_scenarios(strat, strat, [_draw("high", 260)], price=150),
                 context_key="t0", cur_range_key=rk)
    book.monitor(lambda s: {"state": "triggered", "entry": 120, "stop": 110, "target": 210},
                 bar=_bar(121, 119, 120), day="D1")
    assert book.active[0].position["pnl_usd"] == 0.0                 # just opened at 120
    book.monitor(lambda s: None, bar=_bar(125, 122, 124), day="D1")  # still open, close 124 → (124-120)*5
    assert book.active[0].position["pnl_usd"] == 20.0                # running $ since start
    book.monitor(lambda s: None, bar=_bar(212, 205, 210), day="D1")  # target 210 → (210-120)*5
    assert book.active[0].position["outcome"] == "target" and book.active[0].position["pnl_usd"] == 450.0
    assert book.stats()["total_usd"] == 450.0


def test_target_requires_min_2R_and_picks_nearest_qualifying():
    from ict_v2 import pipeline as P
    chain = _chain("long", 110, 130)                       # leg 110→130, 50% = 120 = entry; stop 110 → risk 10
    # draw too NEAR (< 2R): draw 135 → dist 15 < 20 → NOT tradeable even with a confirmed reversal
    sc = _N(direction="long", entry_zone=(100, 150), draw=_N(price=135), tf="1m")
    r = P.execution_for_scenario(sc, [chain], price=120, objectives=[_draw_obj(135)])
    assert r["state"] == "retracing" and "2R" in r["why"]
    # with liquidity: a 145 (2.5R) and a far 300 → pick the NEAREST that clears 2R = 145 → triggered
    r2 = P.execution_for_scenario(sc, [chain], price=120, objectives=[_draw_obj(145), _draw_obj(300)])
    assert r2["state"] == "triggered" and r2["target"] == 145 and r2["rr"] == 2.5


def test_same_direction_theses_collapse_to_one_with_a_ladder():
    # 3 long draws from the same discount zone are ONE trade (shared entry/stop, target = nearest past
    # 2R) — they must collapse to a single scenario, keeping the NEAREST, farther draws as the ladder.
    strat = _ctx(bias="long")
    objs = [_draw("high", 260), _draw("high", 240, strength=0.6), _draw("high", 280, strength=0.5)]
    sc = SC.build_scenarios(strat, strat, objs, price=190)
    longs = [s for s in sc if s.direction == "long"]
    assert len(longs) == 1                                   # not three identical trades
    assert longs[0].draw.price == 240                        # kept the nearest draw (closest to 190)
    assert sorted(o.price for o in longs[0].draw_ladder) == [260, 280]   # farther draws = extensions


# ---- TRADE-LIFECYCLE / duplicate prevention: the tracker must log exactly one record per real trade --
def test_setup_opens_one_trade_only_even_if_execution_keeps_firing():
    """A triggered setup must open exactly ONE trade no matter how many monitor passes see it fire."""
    book, rk = _booked()
    trig = {"state": "triggered", "entry": 120, "stop": 110, "target": 210}
    for _ in range(5):                                           # same setup fires 5×, never resolves
        book.monitor(lambda s: trig, bar=_bar(121, 119, 120))
    assert len(book.trades) == 1                                 # opened once, then sticky-updates only
    assert book.active[0].position["open"]


def test_non_trigger_pass_never_logs_a_trade():
    """bar=None = a context/confirmation pass (not the trigger TF). It must NOT open or log a trade."""
    book, rk = _booked()
    book.monitor(lambda s: {"state": "triggered", "entry": 120, "stop": 110, "target": 210}, bar=None)
    assert book.trades == [] and not book.active[0].position


def _reopen_book_after_stop():
    """Open a trade, stop it out, then re-run the SAME context close so the (now resolved) scenario is
    re-admitted as a fresh proposal — the exact path that used to double-log a trade."""
    strat = _ctx(bias="long")
    book = SC.ScenarioBook()
    rk = SC._range_key(strat.dealing_range)
    props = lambda p=150: SC.build_scenarios(strat, strat, [_draw("high", 260)], price=p)
    book.observe(props(), context_key="t0", cur_range_key=rk)
    book.monitor(lambda s: {"state": "triggered", "entry": 120, "stop": 110, "target": 210},
                 bar=_bar(121, 119, 120))
    book.monitor(lambda s: None, bar=_bar(122, 108, 109))       # stop-out (draw 260 still unswept)
    assert book.active[0].state == "stop" and len(book.trades) == 1
    book.observe(props(), context_key="t1", cur_range_key=rk)   # SAME context → scenario re-admitted fresh
    assert book.active[0].state == "watching" and book.active[0].position is None
    return book, rk


def test_resolved_setup_is_not_reopened_on_readmission():
    """The core bug: after a trade closes and its scenario is re-admitted, the SAME setup re-firing must
    NOT open a second trade."""
    book, rk = _reopen_book_after_stop()
    for _ in range(4):
        book.monitor(lambda s: {"state": "triggered", "entry": 120, "stop": 110, "target": 210},
                     bar=_bar(121, 119, 120))
    assert len(book.trades) == 1                                 # still exactly one — no re-log
    assert book.active[0].state == "armed"                       # shown as armed, but NOT a new position


def test_target_update_is_not_a_new_trade():
    """A re-picked/nearer target (same entry+stop) is the same setup — never a new trade."""
    book, rk = _reopen_book_after_stop()
    for tgt in (200, 190, 180):                                  # same entry/stop, target moves each pass
        book.monitor(lambda s: {"state": "triggered", "entry": 120, "stop": 110, "target": tgt},
                     bar=_bar(121, 119, 120))
    assert len(book.trades) == 1


def test_genuinely_new_setup_opens_a_second_trade():
    """After the previous trade closed, a DIFFERENT entry PD-array (new entry/stop → new signature) is a
    genuinely new setup and MAY open a second trade."""
    book, rk = _reopen_book_after_stop()
    book.monitor(lambda s: {"state": "triggered", "entry": 132, "stop": 124, "target": 210},
                 bar=_bar(133, 131, 132))
    assert len(book.trades) == 2                                 # a new setup → a second logical trade
    assert book.trades[1]["entry"] == 132 and book.active[0].position["open"]


def test_fvg_bounds_distinguish_setups_at_the_same_entry():
    """Same entry/stop but a DIFFERENT FVG (fvg bounds) is a different setup; the same FVG is not."""
    book, rk = _reopen_book_after_stop()
    # NOTE: the closed trade had no fvg bounds (sig fvg=None). A trigger carrying explicit FVG bounds is a
    # different signature → opens; repeating the identical FVG does not open again.
    ex = {"state": "triggered", "entry": 120, "stop": 110, "target": 210, "fvg_top": 121, "fvg_bottom": 118}
    book.monitor(lambda s: ex, bar=_bar(121, 119, 120))
    assert len(book.trades) == 2                                 # new FVG signature → a new trade
    book.monitor(lambda s: None, bar=_bar(122, 108, 109))       # stop it out again
    book.observe(SC.build_scenarios(_ctx(bias="long"), _ctx(bias="long"), [_draw("high", 260)], price=150),
                 context_key="t2", cur_range_key=rk)
    book.monitor(lambda s: ex, bar=_bar(121, 119, 120))         # identical FVG again → NOT a new trade
    assert len(book.trades) == 2


def test_draw_drift_does_not_create_a_duplicate_trade():
    """Same entry/stop within the same dealing range, but a DRIFTING draw/target (a new scenario_id since
    the id carries the draw price) must NOT open a second trade — a target/draw update is not a new setup.
    Regression from the 1m backtest, where the nearest-2R target drifted hourly and re-logged the trade."""
    strat = _ctx(bias="long")
    book = SC.ScenarioBook()
    rk = SC._range_key(strat.dealing_range)
    book.observe(SC.build_scenarios(strat, strat, [_draw("high", 260)], price=150),
                 context_key="t0", cur_range_key=rk)
    book.monitor(lambda s: {"state": "triggered", "entry": 120, "stop": 110, "target": 210},
                 bar=_bar(121, 119, 120))                          # open (draw 260)
    book.monitor(lambda s: None, bar=_bar(122, 108, 109))          # stop-out (draw 260 still unswept)
    assert len(book.trades) == 1
    # the draw drifts 260 -> 255 → a NEW scenario_id, SAME range/direction/entry/stop
    book.observe(SC.build_scenarios(strat, strat, [_draw("high", 255)], price=150),
                 context_key="t1", cur_range_key=rk)
    book.monitor(lambda s: {"state": "triggered", "entry": 120, "stop": 110, "target": 205},
                 bar=_bar(121, 119, 120))
    assert len(book.trades) == 1                                   # drifting target ≠ a new trade


def test_topstep_order_type_depends_on_price_vs_entry():
    from ict_v2 import pipeline as P
    o = lambda sc, ch, objs, pr: P.execution_for_scenario(sc, [ch], price=pr, objectives=objs)["order"]
    scL = _N(direction="long", entry_zone=(100, 150), draw=_N(price=210), tf="1m")
    chL = _chain("long", 100, 140)                            # entry 120 (leg 50%)
    assert o(scL, chL, [_draw_obj(210)], 140) == "BUY LIMIT"   # entry 120 below price 140 → limit
    assert o(scL, chL, [_draw_obj(210)], 110) == "BUY STOP"    # entry 120 above price 110 → stop
    scS = _N(direction="short", entry_zone=(100, 150), draw=_N(price=40), tf="1m")
    chS = _chain("short", 140, 100)                           # leg 100→140, 50% = 120 = entry
    assert o(scS, chS, [_draw_obj(40, side="low")], 110) == "SELL LIMIT"   # entry 120 above price 110 → limit
    assert o(scS, chS, [_draw_obj(40, side="low")], 130) == "SELL STOP"    # entry 120 below price 130 → stop
