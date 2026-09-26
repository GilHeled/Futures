"""Data-update sanitizer: the settlement-artifact repair on the final bar (deterministic)."""
from ict_v2.market_data import sanitize_final_bar


def _rec(o, h, l, c):
    return {"symbol": "X", "open_ms": 1, "close_ms": 2, "o": o, "h": h, "l": l, "c": c, "v": 0.0}


def test_down_settle_repaired_to_high():
    # yfinance stamped close to the low (settle); real last trade = high
    recs = [_rec(10, 12, 9, 11), _rec(30919.25, 30922.5, 30889.25, 30889.25)]
    rep = sanitize_final_bar(recs)
    assert rep and rep["direction"] == "down-settle" and rep["new_close"] == 30922.5
    assert recs[-1]["c"] == 30922.5 and recs[-1]["l"] == 30919.25   # low := min(open, high)


def test_up_settle_repaired_to_low():
    recs = [_rec(100, 110, 95, 110)]         # close==high, open<close -> up-settle
    rep = sanitize_final_bar(recs)
    assert rep and rep["direction"] == "up-settle" and rep["new_close"] == 95
    assert recs[-1]["c"] == 95 and recs[-1]["h"] == 100            # high := max(open, low)


def test_clean_final_bar_untouched():
    recs = [_rec(100, 110, 95, 104)]         # ordinary bar: close is neither extreme
    before = dict(recs[-1])
    assert sanitize_final_bar(recs) is None
    assert recs[-1] == before


def test_down_bar_closing_at_low_but_open_equals_close_untouched():
    # o == c means no settle drag; must not be treated as an artifact
    recs = [_rec(100, 105, 100, 100)]        # c==l but o==c -> not (o>c) -> untouched
    assert sanitize_final_bar(recs) is None


def test_empty_records():
    assert sanitize_final_bar([]) is None
