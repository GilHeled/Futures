"""Liquidity objectives: the ONE general abstraction (swing/fvg/nwog/org/fib), typed + roled + scored."""
from types import SimpleNamespace

from ict_v2 import liquidity as LQ
from ict_v2 import pdarrays


def _dr(low, high):
    ce = (low + high) / 2.0
    return SimpleNamespace(low=low, high=high, ce=ce, direction="up",
                           zone_of=lambda p: "discount" if p < ce else ("premium" if p > ce else "equilibrium"))


def _ctx(bias="long", low=100, high=200, pools=(), draws=()):
    dr = _dr(low, high)
    return SimpleNamespace(
        tf="4H", bias=bias, dealing_range=dr, liquidity=list(pools), draws=list(draws),
        erl_irl=lambda p: "ERL" if (p > dr.high or p < dr.low) else "IRL",
        fib_levels=lambda: [{"level": 0.5, "price": dr.ce, "zone": "equilibrium"},
                            {"level": 0.62, "price": dr.low + 0.38 * (dr.high - dr.low), "zone": "discount"},
                            {"level": 0.79, "price": dr.low + 0.21 * (dr.high - dr.low), "zone": "discount"}])


def _pool(kind, price):
    return SimpleNamespace(kind=kind, price=price)


def test_collect_unifies_all_kinds():
    fvg = pdarrays.role_of(pdarrays.from_fvg(SimpleNamespace(direction="bullish", top=260, bottom=250,
                           ce=255, status="unfilled", tf="4H"), "4H"), direction="long", zone="premium")
    ctx = _ctx(pools=[_pool("high", 260), _pool("low", 40)], draws=[fvg])
    objs = LQ.collect_objectives(ctx, direction="long",
                                 gaps=[{"_kind": "nwog", "tf": "W", "top": 210, "bottom": 205, "mid": 207.5,
                                        "closed": False}])
    kinds = {o.kind for o in objs}
    assert {"swing", "fvg", "nwog", "fib"} <= kinds                 # every kind unified into one list
    for o in objs:                                                   # every objective is typed + scored + roled
        assert o.kind in LQ._OBJECTIVE_KINDS and o.strength > 0 and o.role in pdarrays.PD_ROLES


def test_higher_tf_objective_is_stronger():
    o4 = LQ.LiquidityObjective(kind="swing", tf="4H", side="high", price=260, status="unswept")
    o1 = LQ.LiquidityObjective(kind="swing", tf="1H", side="high", price=260, status="unswept")
    assert LQ._strength("swing", "4H", "unswept") > LQ._strength("swing", "1H", "unswept")


def test_swept_objective_is_spent_and_low_strength():
    ctx = _ctx(pools=[_pool("high", 260)])
    objs = LQ.collect_objectives(ctx, direction="long")
    # a swing pool is unswept here → active draw; a swept one would score 0 and role inactive
    assert any(o.kind == "swing" and o.role in ("draw", "reaction") for o in objs)
    assert LQ._strength("swing", "4H", "swept") == 0.0


def test_draws_helper_returns_draw_role_strongest_first():
    a = LQ.LiquidityObjective(kind="swing", tf="4H", side="high", price=260, strength=0.8, role="draw")
    b = LQ.LiquidityObjective(kind="fvg", tf="1H", side="high", price=255, strength=0.48, role="draw")
    c = LQ.LiquidityObjective(kind="fib", tf="4H", side="low", price=120, strength=0.4, role="reaction")
    ds = LQ.draws([b, a, c])
    assert ds == [a, b] and c not in ds                              # only draws, strongest first
