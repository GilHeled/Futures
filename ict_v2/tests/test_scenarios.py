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
    objs = [_draw("high", 260), _draw("high", 240, strength=0.6)]
    book.observe(SC.build_scenarios(strat, strat, objs, price=190), context_key="t0", cur_range_key=rk)
    assert len(book.active) == 2
    # the top draw (260) gets taken → it drops from the active pool set → its scenario leaves the book
    objs2 = [_draw("high", 240, strength=0.6)]                        # 260 no longer an active draw
    book.observe(SC.build_scenarios(strat, strat, objs2, price=261), context_key="t1", cur_range_key=rk)
    ids = [s.scenario_id for s in book.active]
    assert not any(":260" in i for i in ids)                         # the taken draw's scenario is gone
    assert any(":260" in s.scenario_id for s in book.retired)        # retired for audit


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
