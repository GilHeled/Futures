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


def test_stop_resolves_as_loss():
    book, rk = _booked()
    book.monitor(lambda s: {"state": "triggered", "entry": 120, "stop": 110, "target": 210}, bar=_bar(121, 119, 120))
    book.monitor(lambda s: None, bar=_bar(122, 108, 109))           # low 108 ≤ stop 110 → stopped
    assert book.active[0].state == "stop" and book.active[0].position["result_r"] == -1.0


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
