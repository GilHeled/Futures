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


# ---- execution monitor geometry (M15/M1): only VALID, COHERENT entries become actionable ------
def test_execution_for_scenario_only_surfaces_valid_coherent_entries():
    from types import SimpleNamespace
    from ict_v2 import pipeline as P
    sc = SimpleNamespace(direction="long", entry_zone=(100, 150), draw=SimpleNamespace(price=210))

    def cand(entry, stop, structure="valid", role="entry", dirn="long", live=False):
        return SimpleNamespace(direction=dirn, entry=entry, stop=stop, structure=structure,
                               entry_role=role, tf="1m",
                               entry_obj=SimpleNamespace(state=("valid" if live else "waiting")))

    # coherent long (stop<entry<draw), valid, in zone → armed; live → triggered
    assert P.execution_for_scenario(sc, [cand(120, 110)], price=120)["state"] == "armed"
    assert P.execution_for_scenario(sc, [cand(120, 110, live=True)], price=120)["state"] == "triggered"
    # wrong-side stop (stop>entry for a long) → not actionable; price in zone → retracing
    assert P.execution_for_scenario(sc, [cand(120, 130)], price=120)["state"] == "retracing"
    # structurally invalid → not actionable
    assert P.execution_for_scenario(sc, [cand(120, 110, structure="invalid")], price=120)["state"] == "retracing"
    # nothing in zone and price outside the zone → watching (None)
    assert P.execution_for_scenario(sc, [cand(160, 150)], price=90) is None
    # geometry: target = the SCENARIO draw (210), stop = the candidate's manipulation extreme (110)
    ex = P.execution_for_scenario(sc, [cand(120, 110)], price=120)
    assert ex["target"] == 210 and ex["stop"] == 110 and ex["entry"] == 120


def test_execution_marks_a_late_entry_stale_not_armed():
    """TIMELINESS: once price has run past the midpoint of the entry→target move (the draw is nearly
    reached), a resting retrace-entry is STALE — surfaced as 'stale', never 'armed' (fixes armed-too-late)."""
    from types import SimpleNamespace as N
    from ict_v2 import pipeline as P
    sc = N(direction="long", entry_zone=(100, 150), draw=N(price=210))
    def cand(entry, stop, live=False):
        return N(direction="long", entry=entry, stop=stop, structure="valid", entry_role="entry",
                 tf="1m", entry_obj=N(state=("valid" if live else "waiting")))
    objs = [N(price=210, status="unswept")]
    # price 180 = 67% of the way from entry 120 to target 210 → move already ran → STALE, not armed
    r = P.execution_for_scenario(sc, [cand(120, 110)], price=180, objectives=objs)
    assert r["state"] == "stale" and "missed" in r["why"]
    # price 130 = ~11% of the way → still ahead of the move → ARMED
    assert P.execution_for_scenario(sc, [cand(120, 110)], price=130, objectives=objs)["state"] == "armed"
    # a LIVE entry (price retraced into it) is never stale even if computed progress is high → triggered
    assert P.execution_for_scenario(sc, [cand(120, 110, live=True)], price=180, objectives=objs)["state"] == "triggered"


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
    from types import SimpleNamespace
    from ict_v2 import pipeline as P
    def cand(entry, stop):
        return SimpleNamespace(direction="long", entry=entry, stop=stop, structure="valid",
                               entry_role="entry", tf="1m", entry_obj=SimpleNamespace(state="waiting"))
    # draw too NEAR (< 2R): entry 120 / stop 110 (risk 10), draw 135 → dist 15 < 20 → NOT tradeable
    sc = SimpleNamespace(direction="long", entry_zone=(100, 150), draw=SimpleNamespace(price=135))
    r = P.execution_for_scenario(sc, [cand(120, 110)], price=120)
    assert r["state"] == "retracing" and "2R" in r["why"]
    # with liquidity: a 145 (2.5R) and a far 300 → pick the NEAREST that clears 2R = 145
    objs = [SimpleNamespace(price=145, status="unswept"), SimpleNamespace(price=300, status="unswept")]
    r2 = P.execution_for_scenario(sc, [cand(120, 110)], price=120, objectives=objs)
    assert r2["state"] == "armed" and r2["target"] == 145 and r2["rr"] == 2.5


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
    from types import SimpleNamespace as N
    from ict_v2 import pipeline as P
    objs = [N(price=210, status="unswept"), N(price=40, status="unswept")]
    def cand(entry, stop, dirn):
        return N(direction=dirn, entry=entry, stop=stop, structure="valid", entry_role="entry",
                 tf="1m", entry_obj=N(state="waiting"))
    o = lambda sc, c, pr: P.execution_for_scenario(sc, [c], price=pr, objectives=objs)["order"]
    scL = N(direction="long", entry_zone=(100, 150), draw=N(price=210))
    scS = N(direction="short", entry_zone=(100, 150), draw=N(price=40))
    assert o(scL, cand(120, 110, "long"), 140) == "BUY LIMIT"    # entry below price → limit
    assert o(scL, cand(140, 130, "long"), 120) == "BUY STOP"     # entry above price → stop
    assert o(scS, cand(130, 140, "short"), 120) == "SELL LIMIT"  # entry above price → limit
    assert o(scS, cand(120, 130, "short"), 140) == "SELL STOP"   # entry below price → stop (price higher)
