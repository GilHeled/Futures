"""TradeTracker lifecycle: open from a TAKE ticket, resolve to +2R / −1R / horizon / no-fill against
closed bars, and record expected-vs-actual. Mirrors the frozen fixed-2R exit."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ict_live.live import signal as SIG
from ict_live.live.tracker import TradeTracker
from ict_live.market.bar import Bar

ET = ZoneInfo("America/New_York")


def _b(i, o, h, l, c):
    t = datetime(2026, 6, 2, 9, tzinfo=ET) + timedelta(hours=i)
    return Bar("1H", t, t + timedelta(hours=1), o, h, l, c, 100.0)


def _ticket(action="TAKE", direction="LONG", entry=100.0, stop=99.0):
    # a minimal TradeTicket with the fields the tracker reads
    exit_t = entry + 2 * abs(entry - stop) if direction == "LONG" else entry - 2 * abs(entry - stop)
    return SIG.TradeTicket(
        ticket_id="MNQ:1H:t0", time="2026-06-02T09:00:00-04:00", symbol="MNQ", signal_tf="1H",
        action=action, structural=direction, execution=("TRADE" if action == "TAKE" else "PASS"),
        confidence=0.7, entry=entry, stop=stop, risk=abs(entry - stop), exit_target=exit_t,
        structural_target=(entry + 10 if direction == "LONG" else entry - 10), structural_rr=10.0,
        reasoning={"weakest_factor": "pd_location"})


def test_target_hit_records_plus_2R():
    tr = TradeTracker()
    assert tr.open_from_ticket(_ticket()) is not None
    tr.update(_b(1, 100, 100, 100, 100))            # fill (range contains entry 100)
    closed = tr.update(_b(2, 100, 102.5, 100, 102)) # hits +2R (102)
    assert len(closed) == 1
    c = closed[0]
    assert c.result == "TARGET" and c.result_R == 2.0 and c.win is True and c.filled
    assert c.expected_R == 2.0 and c.bars_held == 1
    assert not tr.open


def test_stop_hit_records_minus_1R():
    tr = TradeTracker()
    tr.open_from_ticket(_ticket())
    tr.update(_b(1, 100, 100, 100, 100))            # fill
    closed = tr.update(_b(2, 100, 100.3, 98.9, 99)) # hits stop 99
    assert closed[0].result == "STOP" and closed[0].result_R == -1.0 and closed[0].win is False


def test_ambiguous_resolves_to_stop():
    tr = TradeTracker()
    tr.open_from_ticket(_ticket())
    tr.update(_b(1, 100, 100, 100, 100))
    closed = tr.update(_b(2, 100, 102.5, 98.9, 100))  # spans both stop and +2R
    assert closed[0].result == "AMBIGUOUS" and closed[0].result_R == -1.0


def test_no_fill_closes_after_horizon():
    tr = TradeTracker(horizon=3)
    tr.open_from_ticket(_ticket(entry=100.0, stop=99.0))
    out = []
    for i in range(1, 5):
        out += tr.update(_b(i, 101, 101.5, 100.6, 101))   # entry 100 never touched
    assert any(c.result == "NO_FILL" and c.filled is False for c in out)


def test_skip_ticket_not_opened():
    tr = TradeTracker()
    assert tr.open_from_ticket(_ticket(action="SKIP")) is None
    assert not tr.open


def test_short_side_target():
    tr = TradeTracker()
    tr.open_from_ticket(_ticket(direction="SHORT", entry=100.0, stop=101.0))  # exit_target = 98
    tr.update(_b(1, 100, 100, 100, 100))            # fill
    closed = tr.update(_b(2, 100, 100, 97.5, 98))   # hits 98 (+2R short)
    assert closed[0].result == "TARGET" and closed[0].result_R == 2.0
