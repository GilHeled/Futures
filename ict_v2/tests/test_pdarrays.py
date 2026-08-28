"""Course PD-array context detectors: NWOG (Lesson 13), ORG (Lesson 14)."""
from datetime import datetime, timedelta, timezone

from ict_live.market.bar import Bar
from ict_v2 import pdarrays

_UTC = timezone.utc


def _bar(t, o, h, l, c, dur_min=240):
    return Bar("4H", t, t + timedelta(minutes=dur_min), o, h, l, c, 100.0)


def _week(start, closes, step_h=4):
    """A run of contiguous 4H bars at `closes` prices, 4h apart, starting at `start`."""
    bars, t = [], start
    prevc = closes[0]
    for c in closes:
        o = prevc
        bars.append(_bar(t, o, max(o, c) + 1, min(o, c) - 1, c))
        prevc = c
        t = t + timedelta(hours=step_h)
    return bars, t


def test_nwog_detects_weekend_gap_and_midpoint():
    """Lesson 13: NWOG = gap between the last price before the weekend and the new-week open;
    boundaries + 50% midpoint; S/R + magnet (not liquidity). A >24h bar gap marks the weekly reopen."""
    t0 = datetime(2026, 6, 1, 14, 0, tzinfo=_UTC)              # week 1
    w1, tend = _week(t0, [100, 101, 100, 100])                # Friday close = 100
    # weekend: next bar opens 48h later at 105 (gap UP) → NWOG top=105 bottom=100 mid=102.5
    t_open = w1[-1].close_time + timedelta(hours=48)
    w2, _ = _week(t_open, [105, 106, 107, 106])               # stays above 100 → NOT closed
    got = pdarrays.nwogs(w1 + w2)
    assert len(got) == 1
    g = got[0]
    assert g["top"] == 105.0 and g["bottom"] == 100.0 and g["mid"] == 102.5
    assert g["closed"] is False                               # price never closed back through 100


def test_nwog_closed_when_rebalanced():
    """'Closed' = a later bar's body closes back through the pre-weekend price (Lesson 12 convention)."""
    t0 = datetime(2026, 6, 1, 14, 0, tzinfo=_UTC)
    w1, _ = _week(t0, [100, 101, 100, 100])
    t_open = w1[-1].close_time + timedelta(hours=48)
    w2, _ = _week(t_open, [105, 104, 102, 99])                # last bar closes 99 ≤ 100 → rebalanced
    got = pdarrays.nwogs(w1 + w2)
    assert len(got) == 1 and got[0]["closed"] is True


def test_nwog_keeps_three_plus_old_unclosed():
    """The course marks THREE; an UNCLOSED gap older than three weeks is additionally retained.
    A `week` here = two contiguous 4H bars starting on `day`; weeks are placed weeks apart so the
    inter-week jump is the (>24h) weekly-open gap, with realistic ages."""
    base_day = datetime(2026, 4, 1, 14, 0, tzinfo=_UTC)

    def week(day_offset, open_px, close_px):
        t = base_day + timedelta(days=day_offset)
        b1 = _bar(t, open_px, max(open_px, close_px) + 1, min(open_px, close_px) - 1, close_px)
        b2 = _bar(t + timedelta(hours=4), close_px, close_px + 1, close_px - 1, close_px)
        return [b1, b2]

    # gap A (oldest, day 0→7): 100→200 gap-up, and price never returns ≤100 → stays UNCLOSED, gets old
    bars = week(0, 100, 100) + week(7, 200, 200) + week(21, 210, 210) \
        + week(28, 220, 220) + week(35, 230, 230)              # gaps at days 7,21,28,35; gap A (day7) → 4wk old
    got = pdarrays.nwogs(bars, keep=3)
    ages = [g["age_weeks"] for g in got]
    assert len(got) == 4                                       # 3 most-recent + 1 retained old-unclosed
    assert got[0]["bottom"] == 100.0 and got[0]["closed"] is False and got[0]["age_weeks"] > 3
    assert ages == sorted(ages, reverse=True)                  # oldest first → largest age first


def test_nwog_empty_without_weekend_gap():
    t0 = datetime(2026, 6, 1, 14, 0, tzinfo=_UTC)
    w, _ = _week(t0, [100, 101, 102, 103, 104])               # contiguous, no >24h gap
    assert pdarrays.nwogs(w) == []


# ---- ORG (Lesson 14) --------------------------------------------------------------------------
_ET = timezone(timedelta(hours=-4))            # EDT (2026-06 is DST) — build bars at known ET times


def _bar15(et_dt, o, c):
    t = et_dt.astimezone(_UTC)
    return Bar("15m", t, t + timedelta(minutes=15), o, max(o, c) + 1, min(o, c) - 1, c, 100.0)


def test_org_prior_close_to_today_open_and_midpoint():
    """Lesson 14: ORG = prior day's 16:15 ET close → today's 09:30 ET open; key level = 50% midpoint;
    current day only. Example from the lesson: close 100, open 200 → ORG 50% = 150."""
    d1 = datetime(2026, 6, 1, tzinfo=_ET)                     # Monday
    d2 = datetime(2026, 6, 2, tzinfo=_ET)                     # Tuesday (today)
    bars = [
        _bar15(d1.replace(hour=16, minute=0), 100, 100),      # prior day ≤16:15 → close 100
        _bar15(d2.replace(hour=9, minute=30), 200, 205),      # today 09:30 open = 200 (gap up)
        _bar15(d2.replace(hour=9, minute=45), 205, 206),
    ]
    g = pdarrays.org(bars)
    assert g is not None
    assert g["bottom"] == 100.0 and g["top"] == 200.0 and g["mid"] == 150.0
    assert g["closed"] is False                               # price never closed back to 100


def test_org_closed_when_price_returns_to_prior_close():
    d1 = datetime(2026, 6, 1, tzinfo=_ET)
    d2 = datetime(2026, 6, 2, tzinfo=_ET)
    bars = [
        _bar15(d1.replace(hour=16, minute=0), 100, 100),
        _bar15(d2.replace(hour=9, minute=30), 200, 190),
        _bar15(d2.replace(hour=10, minute=0), 190, 99),       # closes 99 ≤ 100 → rebalanced
    ]
    assert pdarrays.org(bars)["closed"] is True


def test_org_none_before_open_or_without_prior_day():
    d2 = datetime(2026, 6, 2, tzinfo=_ET)
    # only today's bars, no prior day → cannot form
    assert pdarrays.org([_bar15(d2.replace(hour=9, minute=30), 200, 205)]) is None
    # prior day exists but no 09:30 bar today yet → None
    d1 = datetime(2026, 6, 1, tzinfo=_ET)
    pre = [_bar15(d1.replace(hour=16, minute=0), 100, 100),
           _bar15(d2.replace(hour=8, minute=0), 150, 150)]    # 08:00, before 09:30
    assert pdarrays.org(pre) is None


# ---- PDArray object + contextual role (Lessons 10-12): LIFECYCLE ≠ ROLE ------------------------
from types import SimpleNamespace


def _fvg(direction="bullish", top=105.0, bottom=100.0, status="unfilled", tf="1m"):
    return SimpleNamespace(id="F1", direction=direction, top=top, bottom=bottom,
                           ce=(top + bottom) / 2.0, status=status, tf=tf)


def test_tf_class_lesson12_scoping():
    assert pdarrays.tf_class("1m") == "LTF" and pdarrays.tf_class("5m") == "LTF"
    assert pdarrays.tf_class("15m") == "MTF" and pdarrays.tf_class("1H") == "MTF"
    assert pdarrays.tf_class("4H") == "HTF" and pdarrays.tf_class("D") == "HTF"


def test_from_fvg_adapter_preserves_lifecycle_and_geometry():
    a = pdarrays.from_fvg(_fvg(top=110, bottom=100, status="touched", tf="15m"))
    assert (a.kind, a.tf, a.polarity, a.status) == ("fvg", "15m", "bullish", "touched")
    assert a.top == 110 and a.bottom == 100 and a.ce == 105 and a.role == ""   # role not yet assigned


def test_from_nwog_org_are_nondirectional_with_closed_as_mitigated():
    n = pdarrays.from_nwog({"top": 10, "bottom": 8, "mid": 9, "closed": True})
    o = pdarrays.from_org({"top": 5, "bottom": 4, "mid": 4.5, "closed": False})
    assert (n.kind, n.polarity, n.status) == ("nwog", "", "mitigated")
    assert (o.kind, o.polarity, o.status) == ("org", "", "unfilled")


def test_role_ltf_discount_is_entry_even_when_unfilled_long():
    # user correction #1: an UNFILLED 1m FVG in the retracement (discount) area is already the
    # planned ENTRY — role must NOT be read off status.
    a = pdarrays.role_of(pdarrays.from_fvg(_fvg(status="unfilled", tf="1m")),
                         direction="long", zone="discount")
    assert a.role == "entry" and a.role_basis["lifecycle"] == "unfilled"


def test_role_htf_draw_side_is_draw_long():
    a = pdarrays.role_of(pdarrays.from_fvg(_fvg(tf="4H")), direction="long", zone="premium")
    assert a.role == "draw" and a.role_basis["side"] == "draw"


def test_role_mtf_retrace_is_reaction_not_entry():
    # Lesson 12 limits ENTRY to 5m/1m: a 15m FVG in the retracement zone is reaction (quality), not entry
    a = pdarrays.role_of(pdarrays.from_fvg(_fvg(tf="15m")), direction="long", zone="discount")
    assert a.role == "reaction"


def test_role_short_mirrors_long():
    entry = pdarrays.role_of(pdarrays.from_fvg(_fvg(direction="bearish", tf="1m")),
                             direction="short", zone="premium")
    draw = pdarrays.role_of(pdarrays.from_fvg(_fvg(direction="bearish", tf="4H")),
                            direction="short", zone="discount")
    assert entry.role == "entry" and draw.role == "draw"


def test_mitigated_fvg_inactive_but_closed_nwog_stays_reaction():
    fvg = pdarrays.role_of(pdarrays.from_fvg(_fvg(status="mitigated", tf="1m")),
                           direction="long", zone="discount")
    nwog = pdarrays.role_of(pdarrays.from_nwog({"top": 10, "bottom": 8, "mid": 9, "closed": True}),
                            direction="long", zone="premium")
    assert fvg.role == "inactive"                 # spent FVG drops out
    assert nwog.role == "reaction"                # Lesson 13: keep the marking after close


def test_role_equilibrium_or_no_range_is_reaction():
    a = pdarrays.role_of(pdarrays.from_fvg(_fvg(tf="4H")), direction="long", zone=None)
    assert a.role == "reaction" and a.role_basis["side"] == "neutral"


def test_role_basis_is_a_full_auditable_trace():
    # every role decision must expose all dimensions + a human rule (no hidden methodology)
    a = pdarrays.role_of(pdarrays.from_fvg(_fvg(status="touched", tf="1m")),
                         direction="long", zone="discount", erl_irl="IRL")
    b = a.role_basis
    for k in ("tf_class", "dealing_range_position", "liquidity_class", "seeking_vs_reacting",
              "side", "polarity", "lifecycle", "rule", "role"):
        assert k in b, f"missing trace key {k}"
    assert b["tf_class"] == "LTF" and b["dealing_range_position"] == "discount"
    assert b["liquidity_class"] == "IRL" and b["seeking_vs_reacting"] == "reacting"   # touched → reacting
    assert b["role"] == "entry" and a.role == "entry"
    assert "Lesson 12" in b["rule"]                        # the rule cites the course


def test_seeking_vs_reacting_tracks_lifecycle_not_role():
    seek = pdarrays.role_of(pdarrays.from_fvg(_fvg(status="unfilled", tf="1m")),
                            direction="long", zone="discount")
    react = pdarrays.role_of(pdarrays.from_fvg(_fvg(status="touched", tf="1m")),
                             direction="long", zone="discount")
    # SAME role (entry) but DIFFERENT seeking/reacting — the two dimensions are independent
    assert seek.role == react.role == "entry"
    assert seek.role_basis["seeking_vs_reacting"] == "seeking"
    assert react.role_basis["seeking_vs_reacting"] == "reacting"
