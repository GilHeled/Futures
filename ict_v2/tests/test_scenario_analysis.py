"""Read-only scenario-analysis layer: the FVG lifecycle state machine (with the causal-binding
refinement — an unrelated later MSS must NOT confirm), the absence taxonomy, and an integration
smoke test. None of this touches the restored trading engine."""
from dataclasses import dataclass
from datetime import datetime, timedelta

from ict_v2 import scenario_analysis as SA


# ─── tiny deterministic stand-ins (only the fields the state machine reads) ───
@dataclass
class B:
    close: float
    high: float = 0.0
    low: float = 0.0


@dataclass
class F:
    direction: str
    top: float
    bottom: float
    ce: float
    formed_index: int
    first_touch_index: int | None
    mid_index: int = 0
    status: str = ""


@dataclass
class D:
    id: str
    direction: str
    start_index: int
    end_index: int
    start_price: float


@dataclass
class M:
    id: str
    direction: str
    state: str
    depends_on: tuple


def _bars(closes):
    return [B(close=c, high=c + 1, low=c - 1) for c in closes]


def test_fresh_when_never_touched():
    f = F("bullish", top=100, bottom=90, ce=95, formed_index=1, first_touch_index=None)
    bars = _bars([120, 118, 119, 121, 122])          # never returns to the gap, never closes below 90
    state, audit = SA.fvg_state(f, [], [], bars)
    assert state == "FRESH" and audit["confirmation"] is None


def test_touched_unresolved_without_binding():
    f = F("bullish", top=100, bottom=90, ce=95, formed_index=1, first_touch_index=3)
    bars = _bars([110, 108, 106, 96, 98, 99])        # dips to CE (touch) but no departing MSS
    state, audit = SA.fvg_state(f, [], [], bars)
    assert state == "TOUCHED" and audit["confirmation"] == "unresolved"


def test_respected_binds_reaction_departing_the_fvg():
    f = F("bullish", top=100, bottom=90, ce=95, formed_index=1, first_touch_index=3)
    bars = _bars([110, 108, 106, 96, 104, 112])      # touch at 3, then rallies away (never closes <90)
    d = D("d1", "bullish", start_index=3, start_price=95, end_index=5)   # leg ORIGINATES in the zone, after touch
    m = M("m1", "bullish", "confirmed", depends_on=("d1",))
    state, audit = SA.fvg_state(f, [d], [m], bars)
    assert state == "RESPECTED" and audit["bound_mss"] == "m1"


def test_unrelated_later_mss_does_not_confirm_respected():
    # refinement: a same-direction confirmed MSS whose displacement does NOT originate inside the FVG
    # must NOT upgrade the state — it stays TOUCHED / confirmation-unresolved.
    f = F("bullish", top=100, bottom=90, ce=95, formed_index=1, first_touch_index=3)
    bars = _bars([110, 108, 106, 96, 98, 99, 130])
    d = D("d1", "bullish", start_index=6, start_price=125, end_index=6)  # leg origin 125 is OUTSIDE [90,100]
    m = M("m1", "bullish", "confirmed", depends_on=("d1",))
    state, audit = SA.fvg_state(f, [d], [m], bars)
    assert state == "TOUCHED" and audit["confirmation"] == "unresolved"


def test_failed_binds_the_acceptance_move():
    f = F("bullish", top=100, bottom=90, ce=95, formed_index=1, first_touch_index=3)
    bars = _bars([110, 108, 106, 96, 88, 84])        # closes 88 < bottom(90) at index 4 = acceptance-through
    d = D("d1", "bearish", start_index=2, start_price=106, end_index=5)  # span contains the mitigation bar (4)
    m = M("m1", "bearish", "confirmed", depends_on=("d1",))
    state, audit = SA.fvg_state(f, [d], [m], bars)
    assert state == "FAILED" and audit["mitigation_index"] == 4 and audit["bound_mss"] == "m1"


def test_closed_when_mitigated_without_opposite_confirmation():
    f = F("bullish", top=100, bottom=90, ce=95, formed_index=1, first_touch_index=3)
    bars = _bars([110, 108, 106, 96, 88, 84])
    state, audit = SA.fvg_state(f, [], [], bars)      # mitigated but no opposite MSS bound
    assert state == "CLOSED" and audit["confirmation"] == "mitigated-no-opposite-confirmation"


def test_absent_gaps_flags_raw_geometry_with_reason():
    # a clean bullish gap (bar0.high < bar2.low) that no emitted FVG covers
    bars = [B(close=10, high=10, low=9), B(close=12, high=13, low=11), B(close=15, high=16, low=14)]
    absent = SA.absent_gaps(bars, emitted_fvgs=[])
    assert len(absent) == 1 and absent[0]["direction"] == "bullish"
    assert "MSS-displacement leg" in absent[0]["reason"]


# ─── integration smoke: analyze() over a generated 1m stream (dict shape + read-only) ───
def _1m(n, seed=7):
    from ict_live.market.bar import Bar
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
    bars, px, x, t0 = [], 20000.0, seed, datetime(2026, 6, 1, 18, 0, tzinfo=ET)
    for i in range(n):
        x = (1103515245 * x + 12345) % (2 ** 31)
        o = px
        c = px + ((x % 25) - 12) * 0.6
        ot = t0 + timedelta(minutes=i)
        bars.append(Bar("1m", ot, ot + timedelta(minutes=1), o, max(o, c) + (x % 5) * 0.4,
                        min(o, c) - (x % 4) * 0.4, c, 100.0))
        px = c
    return bars


def test_analyze_shape_and_read_only():
    from ict_v2.live import V2Live
    live = V2Live(min_stop=2.0)
    for b in _1m(1500):
        live.push_1m(b)
    trades_before = len(live.engine.book.trades)
    A = SA.analyze(live)
    assert set(A) >= {"price", "ranges", "fvgs", "absent_fvgs", "liquidity", "scenarios", "notes"}
    assert set(A["ranges"]) == {"parent", "internal"}
    assert isinstance(A["fvgs"], list) and isinstance(A["absent_fvgs"], list)
    assert len(A["scenarios"]) == 3 and {s["id"] for s in A["scenarios"]} == {"A", "B", "C"}
    for f in A["fvgs"]:
        assert f["state"] in {"FRESH", "TOUCHED", "RESPECTED", "FAILED", "CLOSED"}
        assert f["test_count"] is None
    # read-only guarantee: analysis did not create/close any trade
    assert len(live.engine.book.trades) == trades_before
