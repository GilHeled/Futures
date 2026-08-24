"""User trade-control overlay: records a decision per ticket, validates status, persists to disk.
It never touches the tracker (that's asserted at the endpoint level)."""
import pytest

from ict_live.live.control import TradeControl


def test_set_and_all(tmp_path):
    c = TradeControl(tmp_path / "tc.json")
    c.set("t1", "placed")
    c.set("t2", "skipped")
    a = c.all()
    assert a["t1"]["status"] == "placed" and a["t2"]["status"] == "skipped"
    assert isinstance(a["t1"]["ts"], int)


def test_rejects_unknown_status_and_empty_id():
    c = TradeControl()
    with pytest.raises(ValueError):
        c.set("t", "frobnicate")
    with pytest.raises(ValueError):
        c.set("", "placed")


def test_persists_across_restart(tmp_path):
    p = tmp_path / "tc.json"
    TradeControl(p).set("t1", "closed")
    assert TradeControl(p).all()["t1"]["status"] == "closed"     # a fresh instance reloads it
