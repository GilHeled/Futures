"""Contract-roll detection + segmentation for the raw continuous front-month series.

The cached Databento continuous (`.c.0`) series carries RAW, non-back-adjusted prices with a
discontinuity at each quarterly roll. Per the methodology owner: do not back-adjust or silently
splice — treat rolls as explicit STRUCTURAL BOUNDARIES, split into independent segments, reset all
engine state per segment, and never let a candidate/feature/outcome window cross a roll.

The continuous parquet has no per-bar contract id, so rolls are located deterministically by the
CME equity-index quarterly cycle (expiry = 3rd Friday of Mar/Jun/Sep/Dec) and confirmed by the
largest overnight price jump in a window around the nominal roll date. Contract codes (e.g.
MNQU26) are therefore INFERRED from the calendar, not read from the data — documented as such.
Future-aware metadata (bars_until_roll) is computed for analysis but must NOT be fed to ML.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ict_live.market.bar import Bar

ET = ZoneInfo("America/New_York")
_MONTH_CODE = {3: "H", 6: "M", 9: "U", 12: "Z"}     # CME quarterly futures month codes


@dataclass(frozen=True)
class RollBoundary:
    index: int                # bar index of the first bar of the NEW contract (the discontinuity)
    time: datetime
    gap: float                # |open - prev_close| at the boundary (the roll jump)
    from_contract: str        # inferred (calendar), e.g. "MNQM26"
    to_contract: str
    expiry: date


@dataclass(frozen=True)
class Segment:
    contract: str             # inferred active contract during this segment
    start_index: int
    end_index: int            # inclusive
    bars: list[Bar]


def third_friday(year: int, month: int) -> date:
    d = date(year, month, 1)
    # weekday(): Mon=0..Sun=6; Friday=4
    first_fri = d + timedelta(days=(4 - d.weekday()) % 7)
    return first_fri + timedelta(days=14)


def _code(root: str, expiry: date) -> str:
    return f"{root}{_MONTH_CODE[expiry.month]}{expiry.year % 100:02d}"


def _next_quarter(expiry: date) -> date:
    m, y = expiry.month, expiry.year
    nm, ny = (m + 3, y) if m < 12 else (3, y + 1)
    return third_friday(ny, nm)


def detect_rolls(bars: list[Bar], root: str = "MNQ", *, roll_days_before: int = 8,
                 search_window_days: int = 5) -> list[RollBoundary]:
    """Locate each quarterly roll as the largest overnight gap near (expiry - roll_days_before)."""
    if not bars:
        return []
    start, end = bars[0].open_time.date(), bars[-1].open_time.date()
    # candidate expiries covering the data span (+ one quarter of margin each side)
    expiries: list[date] = []
    for y in range(start.year - 1, end.year + 2):
        for m in (3, 6, 9, 12):
            expiries.append(third_friday(y, m))

    times = [b.open_time for b in bars]
    boundaries: list[RollBoundary] = []
    for exp in expiries:
        nominal = exp - timedelta(days=roll_days_before)
        lo = datetime.combine(nominal - timedelta(days=search_window_days), datetime.min.time(), ET)
        hi = datetime.combine(nominal + timedelta(days=search_window_days), datetime.min.time(), ET)
        # bars whose open_time falls in the search window
        idxs = [i for i, t in enumerate(times) if lo <= t <= hi and i > 0]
        if not idxs:
            continue
        best_i = max(idxs, key=lambda i: abs(bars[i].open - bars[i - 1].close))
        gap = abs(bars[best_i].open - bars[best_i - 1].close)
        boundaries.append(RollBoundary(
            index=best_i, time=bars[best_i].open_time, gap=round(gap, 4),
            from_contract=_code(root, exp), to_contract=_code(root, _next_quarter(exp)), expiry=exp))
    # dedupe by index, keep chronological
    seen, out = set(), []
    for b in sorted(boundaries, key=lambda r: r.index):
        if b.index not in seen:
            seen.add(b.index)
            out.append(b)
    return out


def segment(bars: list[Bar], boundaries: list[RollBoundary]) -> list[Segment]:
    """Split bars into independent per-contract segments at the roll boundaries (state resets each
    segment; nothing survives across a roll)."""
    cuts = [b.index for b in boundaries]
    starts = [0] + cuts
    ends = [c - 1 for c in cuts] + [len(bars) - 1]
    segs: list[Segment] = []
    for k, (s, e) in enumerate(zip(starts, ends)):
        if e < s:
            continue
        # contract active in this segment: the to_contract of the boundary that STARTED it,
        # or (first segment) the from_contract of the first boundary.
        if k == 0:
            contract = boundaries[0].from_contract if boundaries else "unknown"
        else:
            contract = boundaries[k - 1].to_contract
        segs.append(Segment(contract=contract, start_index=s, end_index=e, bars=bars[s:e + 1]))
    return segs


def roll_metadata(index: int, seg: Segment) -> dict:
    """Per-bar roll metadata. `bars_until_roll` is FUTURE-AWARE — for analysis only, never a
    feature (a bar cannot know when the next roll is)."""
    return {"contract": seg.contract,
            "bars_since_roll": index - seg.start_index,          # causal
            "bars_until_roll": seg.end_index - index}            # NON-causal: do not feed ML
