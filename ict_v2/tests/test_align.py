"""Cross-TF confluence: the liquidity DRAW (target). HTF bias & premium/discount are QUALITY, not a
veto (that lives in the semantic layer, ict_v2/recommend.py + pipeline) — see test_pipeline."""
from types import SimpleNamespace

from ict_v2 import align
from ict_v2.pipeline import HTFContext


def _dr(ce):
    return SimpleNamespace(ce=ce, zone_of=lambda p: "discount" if p < ce else ("premium" if p > ce else "equilibrium"))


def _pool(kind, price):
    return SimpleNamespace(kind=kind, price=price)


def _ctx(bias, ce, pools):
    return HTFContext(tf="4H", bias=bias, dealing_range=_dr(ce), liquidity=pools)


def test_liquidity_objective_targets_buyside_for_long():
    ctx = _ctx("long", 150, [_pool("high", 210), _pool("low", 90)])
    obj = align.liquidity_objective(ctx, "long")
    assert obj.kind == "high" and obj.price == 210          # long → nearest buy-side draw above EQ


def test_liquidity_objective_targets_sellside_for_short():
    ctx = _ctx("short", 150, [_pool("low", 90), _pool("high", 210)])
    obj = align.liquidity_objective(ctx, "short")
    assert obj.kind == "low" and obj.price == 90            # short → nearest sell-side draw below EQ


def test_liquidity_objective_none_when_no_pool_in_direction():
    ctx = _ctx("long", 150, [_pool("low", 90)])             # only sell-side pools → no long draw
    assert align.liquidity_objective(ctx, "long") is None


def test_gate_setup_removed():
    """The old HTF veto is gone — HTF bias/premium-discount are context/quality now, never a gate."""
    assert not hasattr(align, "gate_setup")


# ---- ERL/IRL class-aware draw selection (Lesson 10, user decision 2026-08-28) -----------------
def _arr(ce, status="unfilled", kind="fvg"):
    return SimpleNamespace(ce=ce, status=status, kind=kind)


def test_next_draw_prefers_erl_when_opposing_pool_unswept():
    ctx = _ctx("long", 150, [_pool("high", 210), _pool("low", 90)])
    d = align.next_draw(ctx, "long", internal_arrays=[_arr(180)])
    assert d.klass == "ERL" and d.kind == "high" and d.price == 210      # ERL wins the class decision
    assert d.array_kind == "swing"


def test_next_draw_falls_back_to_irl_when_no_opposing_erl():
    # only sell-side external pools → no ERL draw for a long → seek internal rebalance (Lesson 10)
    ctx = _ctx("long", 150, [_pool("low", 90)])
    d = align.next_draw(ctx, "long", internal_arrays=[_arr(180), _arr(200)])
    assert d.klass == "IRL" and d.array_kind == "fvg"
    assert d.price == 180 and d.tiebreak_n == 2                          # nearest EQ on the draw side; [RES] surfaced


def test_next_draw_irl_ignores_mitigated_and_wrong_side():
    ctx = _ctx("long", 150, [_pool("low", 90)])
    d = align.next_draw(ctx, "long", internal_arrays=[_arr(200, status="mitigated"), _arr(120)])
    # 200 is mitigated (spent), 120 is on the discount (retrace) side → neither is a long draw
    assert d is None


def test_next_draw_none_when_no_class_yields_a_draw():
    ctx = _ctx("long", 150, [_pool("low", 90)])
    assert align.next_draw(ctx, "long", internal_arrays=None) is None
