"""HTF gate (cross-TF confluence): a MTF setup passes only if it agrees with HTF bias, sits in the
right premium/discount zone, and has an HTF liquidity objective in its direction."""
from types import SimpleNamespace

from ict_v2 import align
from ict_v2.pipeline import HTFContext


def _dr(ce):
    return SimpleNamespace(ce=ce, zone_of=lambda p: "discount" if p < ce else ("premium" if p > ce else "equilibrium"))


def _pool(kind, price):
    return SimpleNamespace(kind=kind, price=price)


def _ctx(bias, ce, pools):
    return HTFContext(tf="4H", bias=bias, dealing_range=_dr(ce), liquidity=pools)


def _setup(direction, entry):
    return SimpleNamespace(direction=direction, entry=entry)


def test_passes_when_all_three_axes_agree():
    ctx = _ctx("long", 150, [_pool("high", 210), _pool("low", 90)])
    ok, reasons, obj = align.gate_setup(_setup("long", 120), ctx)      # long, in discount, BSL above
    assert ok and reasons == []
    assert obj.kind == "high" and obj.price == 210                     # targets the buy-side draw


def test_rejects_direction_against_bias():
    ctx = _ctx("long", 150, [_pool("high", 210)])
    ok, reasons, _ = align.gate_setup(_setup("short", 120), ctx)
    assert not ok and any("bias" in r for r in reasons)


def test_rejects_wrong_pd_zone():
    ctx = _ctx("long", 150, [_pool("high", 210)])
    ok, reasons, _ = align.gate_setup(_setup("long", 180), ctx)        # long but entry in premium
    assert not ok and any("premium" in r for r in reasons)


def test_rejects_when_no_liquidity_objective():
    ctx = _ctx("long", 150, [_pool("low", 90)])                        # only sell-side pools
    ok, reasons, obj = align.gate_setup(_setup("long", 120), ctx)
    assert not ok and obj is None and any("objective" in r for r in reasons)


def test_neutral_bias_rejects():
    ctx = _ctx("neutral", 150, [_pool("high", 210)])
    ok, reasons, _ = align.gate_setup(_setup("long", 120), ctx)
    assert not ok and any("neutral" in r for r in reasons)


def test_short_targets_sellside_below():
    ctx = _ctx("short", 150, [_pool("low", 90), _pool("high", 210)])
    ok, reasons, obj = align.gate_setup(_setup("short", 180), ctx)     # short, in premium, SSL below
    assert ok and obj.kind == "low" and obj.price == 90
