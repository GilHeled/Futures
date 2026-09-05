"""ReversalBook — persistent Lesson-15 POTENTIAL lifecycle (V2_GAP fix). Verifies: creation via the existing
_trend_sequence, dedup by frozen identity (not created_at), persistence across closes when the MSS vanishes,
cancellation by a structural swing beyond frozen S[k-1], confirmation by a displacement body-close beyond the
frozen S[k], terminal-once (no resurrection), and separate-vs-same multiple potentials."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as N

from ict_v2.reversals import ReversalBook

T0 = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def _sw(kind, price, index, cidx):
    return N(kind=kind, price=price, index=index, confirm_index=cidx, time=T0 + timedelta(minutes=5 * index))


def _bars(n, last_close, break_body=8.0, base_body=0.5):
    """n synthetic 5m bars. Baseline bars have a tiny body (base_body); the LAST bar (the break bar) gets a
    body of `break_body` so the confirming displacement can show candle expansion vs the preceding candles.
    Set break_body <= base_body to model a break with NO relative expansion."""
    b = []
    for i in range(n):
        o, c = (last_close + break_body, last_close) if i == n - 1 else (1000.0, 1000.0 + base_body)
        b.append(N(open_time=T0 + timedelta(minutes=5 * i), close_time=T0 + timedelta(minutes=5 * i + 5),
                   open=o, high=max(o, c) + 1, low=min(o, c) - 1, close=c, forming=False))
    return b


def _R(x):
    return N(item=x)


def _ms(structural, mss=(), disps=(), sweeps=(), fvgs=()):
    return N(structural=list(structural), ranked_mss=[_R(m) for m in mss], ranked_displacements=[_R(d) for d in disps],
             ranked_sweeps=[_R(s) for s in sweeps], ranked_fvgs=[_R(f) for f in fvgs])


# a SHORT potential skeleton: uptrend H1 70, L1 50, H2 105, L2(HL,broken) 60, H3(LH) 100
def _short_skel():
    return [_sw("high", 70, 0, 2), _sw("low", 50, 1, 3), _sw("high", 105, 2, 4),
            _sw("low", 60, 3, 5), _sw("high", 100, 4, 6)]


def _short_chain(state="candidate", broken=60, confirm_index=9):
    # the confirming displacement SPANS S[k]=60 (start 100 >= 60 > end 55) and is LOCAL to the break bar
    # (_bars(9) → break index 8, which is within the displacement span 4..8)
    sw = N(id="SW", extreme=100, pool_price=100, depends_on=None)
    d = N(id="D", direction="bearish", start_price=100, end_price=55, start_index=4, end_index=8, depends_on=("SW",))
    m = N(id="MSS", direction="bearish", state=state, broken_price=broken, broken_index=3,
          confirm_index=confirm_index, depends_on=("D",))
    return m, d, sw


def _disp(id_, direction, start_price, end_price, start_index, end_index):
    return N(id=id_, direction=direction, start_price=start_price, end_price=end_price,
             start_index=start_index, end_index=end_index, depends_on=("SW",))


def test_creation_from_valid_sequence():
    b = ReversalBook()
    m, d, sw = _short_chain()
    b.update(_ms(_short_skel(), mss=[m], disps=[d], sweeps=[sw]), _bars(9, 80.0), "t0")
    assert b.n_created == 1 and len(b.active) == 1
    p = b.active[0]
    assert p.direction == "short" and p.s_k.price == 60 and p.s_k_minus_1.price == 105 and p.fcp.price == 100
    assert p.state == "potential"


def test_dedup_same_identity_rediscovered_is_absorbed_not_duplicated():
    b = ReversalBook()
    m, d, sw = _short_chain()
    ms = _ms(_short_skel(), mss=[m], disps=[d], sweeps=[sw])
    b.update(ms, _bars(9, 80.0), "t0")
    b.update(ms, _bars(9, 80.0), "t1")          # same identity re-detected on a later close
    b.update(ms, _bars(9, 80.0), "t2")
    assert b.n_created == 1 and len(b.active) == 1          # NOT duplicated
    assert b.n_rediscovered == 2 and b.active[0].rediscoveries == 2


def test_persists_when_mss_vanishes():
    b = ReversalBook()
    m, d, sw = _short_chain()
    b.update(_ms(_short_skel(), mss=[m], disps=[d], sweeps=[sw]), _bars(9, 80.0), "t0")
    # next close: MSS gone from the re-derived read, price hasn't broken 60 or exceeded 105 → still POTENTIAL
    b.update(_ms(_short_skel(), mss=[], disps=[], sweeps=[]), _bars(9, 80.0), "t1")
    assert len(b.active) == 1 and b.active[0].state == "potential" and b.n_confirmed == 0 and b.n_cancelled == 0


def test_cancel_by_structural_swing_beyond_frozen_s_k_minus_1():
    b = ReversalBook()
    m, d, sw = _short_chain()
    b.update(_ms(_short_skel(), mss=[m], disps=[d], sweeps=[sw]), _bars(9, 80.0), "t0")
    # a NEW structural high 110 (> frozen S[k-1]=105), pivot AFTER the failed-continuation (index 6 > 4)
    resumed_skel = _short_skel() + [_sw("low", 90, 5, 7), _sw("high", 110, 6, 8)]
    b.update(_ms(resumed_skel, mss=[], disps=[], sweeps=[]), _bars(9, 108.0), "t1")
    assert b.n_cancelled == 1 and len(b.active) == 0
    assert b.terminal[-1].state == "cancelled" and b.terminal[-1].resumed_extreme == 110


def test_confirm_by_displacement_body_close_beyond_frozen_s_k():
    b = ReversalBook()
    m, d, sw = _short_chain(state="candidate")
    b.update(_ms(_short_skel(), mss=[m], disps=[d], sweeps=[sw]), _bars(9, 80.0), "t0")   # potential
    # later close: a bearish displacement through 60 and the bar body-closes below frozen S[k]=60
    m2, d2, sw2 = _short_chain(state="candidate", broken=60)
    b.update(_ms(_short_skel(), mss=[], disps=[d2], sweeps=[sw2]), _bars(9, 58.0), "t1")
    assert b.n_confirmed == 1 and len(b.confirmed) == 1
    p = b.confirmed[0]
    assert p.state == "confirmed" and p.confirm_chain.get("manip") == 100


def test_no_resurrection_after_terminal():
    b = ReversalBook()
    m, d, sw = _short_chain()
    b.update(_ms(_short_skel(), mss=[m], disps=[d], sweeps=[sw]), _bars(9, 80.0), "t0")
    # confirm it
    b.update(_ms(_short_skel(), mss=[], disps=[_short_chain()[1]], sweeps=[_short_chain()[2]]), _bars(9, 58.0), "t1")
    assert b.n_confirmed == 1
    # the SAME identity re-detected later must NOT create a new active potential
    m3, d3, sw3 = _short_chain()
    b.update(_ms(_short_skel(), mss=[m3], disps=[d3], sweeps=[sw3]), _bars(9, 80.0), "t2")
    assert b.n_created == 1 and len(b.active) == 0 and b.n_rediscovered == 1


def test_separate_potential_when_s_k_differs():
    b = ReversalBook()
    m, d, sw = _short_chain(broken=60)
    b.update(_ms(_short_skel(), mss=[m], disps=[d], sweeps=[sw]), _bars(9, 80.0), "t0")
    # a DIFFERENT structural target (S[k]=62 via broken_index pointing at a different low) → separate potential
    skel2 = [_sw("high", 72, 0, 2), _sw("low", 52, 1, 3), _sw("high", 106, 2, 4),
             _sw("low", 62, 3, 5), _sw("high", 101, 4, 6)]
    m2 = N(id="MSS2", direction="bearish", state="candidate", broken_price=62, broken_index=3,
           confirm_index=9, depends_on=("D2",))
    d2 = N(id="D2", direction="bearish", start_price=101, end_price=62, start_index=4, end_index=7, depends_on=("SW2",))
    sw2 = N(id="SW2", extreme=101, pool_price=101, depends_on=None)
    b.update(_ms(skel2, mss=[m2], disps=[d2], sweeps=[sw2]), _bars(9, 80.0), "t1")
    assert b.n_created == 2 and len(b.active) == 2          # genuinely separate; no one-per-direction cap
    assert b.census()["peak_simultaneous_active"]["short"] == 2


# ---- LOCALITY correction: the confirming displacement must be the leg that actually breaks frozen S[k] ----
def _make_potential():
    b = ReversalBook()
    m, d, sw = _short_chain()
    b.update(_ms(_short_skel(), mss=[m], disps=[d], sweeps=[sw]), _bars(9, 80.0), "t0")   # active POTENTIAL, S[k]=60
    return b


def test_locality_1_local_displacement_breaks_s_k_confirms():
    b = _make_potential()
    sw = N(id="SW", extreme=100, pool_price=100, depends_on=None)
    d = _disp("D", "bearish", 100, 55, 4, 8)                 # spans 60 and end_index 8 == break index → local
    b.update(_ms(_short_skel(), mss=[], disps=[d], sweeps=[sw]), _bars(9, 58.0), "t1")
    assert b.n_confirmed == 1
    loc = b.confirmed[0].confirm_chain["locality"]
    assert loc["spans_s_k"] is True and loc["confirm_bar_belongs"] is True and loc["s_k"] == 60


def test_locality_2_stale_earlier_displacement_plus_marginal_break_rejects():
    b = _make_potential()
    sw = N(id="SW", extreme=100, pool_price=100, depends_on=None)
    d_stale = _disp("Dstale", "bearish", 100, 55, 1, 3)      # SPANS 60 but ended at idx 3 (break bar is 8) → stale
    b.update(_ms(_short_skel(), mss=[], disps=[d_stale], sweeps=[sw]), _bars(9, 59.75), "t1")
    assert b.n_confirmed == 0 and len(b.active) == 1
    rej = b.active[0].locality_reject
    assert rej is not None and "stale" in rej["reason"].lower()


def test_locality_3_displacement_ending_before_s_k_rejects():
    b = _make_potential()
    sw = N(id="SW", extreme=100, pool_price=100, depends_on=None)
    d_short = _disp("Dshort", "bearish", 100, 65, 4, 8)      # bearish + local, but ends at 65 (never reaches 60)
    b.update(_ms(_short_skel(), mss=[], disps=[d_short], sweeps=[sw]), _bars(9, 58.0), "t1")
    assert b.n_confirmed == 0 and len(b.active) == 1
    rej = b.active[0].locality_reject
    assert rej is not None and "through" in rej["reason"].lower()


def test_locality_4_unrelated_displacement_other_regime_rejects():
    b = _make_potential()
    sw = N(id="SW", extreme=100, pool_price=100, depends_on=None)
    d_far = _disp("Dfar", "bearish", 200, 190, 1, 8)         # bearish but at a totally different price band
    b.update(_ms(_short_skel(), mss=[], disps=[d_far], sweeps=[sw]), _bars(9, 58.0), "t1")
    assert b.n_confirmed == 0 and len(b.active) == 1 and b.active[0].locality_reject is not None


# ---- DISPLACEMENT QUALITY (Lesson 12): candle body expansion vs the immediately-preceding minor leg -------
def test_quality_expansion_present_confirms_and_audits():
    b = _make_potential()
    sw = N(id="SW", extreme=100, pool_price=100, depends_on=None)
    d = _disp("D", "bearish", 100, 55, 4, 8)                 # local + spans S[k]=60
    b.update(_ms(_short_skel(), mss=[], disps=[d], sweeps=[sw]), _bars(9, 58.0, break_body=12.0, base_body=0.5), "t1")
    assert b.n_confirmed == 1
    q = b.confirmed[0].confirm_chain["quality"]
    assert q["expands"] is True and q["disp_max_body"] > q["preceding_leg_max_body"]
    assert q["quality_basis"] == "body max vs preceding minor-leg body max [RES]"


def test_quality_no_expansion_rejects_even_when_local():
    b = _make_potential()
    sw = N(id="SW", extreme=100, pool_price=100, depends_on=None)
    d = _disp("D", "bearish", 100, 55, 4, 8)                 # LOCAL + spans S[k], but break-bar body not bigger
    b.update(_ms(_short_skel(), mss=[], disps=[d], sweeps=[sw]), _bars(9, 58.0, break_body=0.5, base_body=0.5), "t1")
    assert b.n_confirmed == 0 and len(b.active) == 1
    rej = b.active[0].quality_reject
    assert rej is not None and "expansion" in rej["reason"].lower()
    assert b.active[0].locality_reject is None               # locality PASSED; expansion is the blocker
