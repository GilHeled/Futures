"""Historical setup enumerator (validation aid). Skips when the cached Databento data is absent."""
import pytest

from ict_v2 import validate as V
from ict_live.research import data as D

_HAS_DATA = (D.CACHE / "databento_MES_5m.parquet").exists()


@pytest.mark.skipif(not _HAS_DATA, reason="cached Databento 5m data not present")
def test_enumerate_setups_dedups_and_reports_fields():
    rows = V.enumerate_setups("MES", "2025-06-02", "2025-06-13", stage="setup", want=("TAKE",))
    # de-dup across sliding cursors: the same (dir, entry, stop) setup appears once
    keys = [(r["direction"], r["entry"], r["stop"]) for r in rows]
    assert len(keys) == len(set(keys))
    for r in rows:                                   # each row is chart-reviewable
        assert r["recommendation"] == "TAKE"
        for k in ("time", "direction", "entry", "stop", "target", "rr", "context_label", "leg"):
            assert k in r


@pytest.mark.skipif(not _HAS_DATA, reason="cached Databento 5m data not present")
def test_relaxed_yields_at_least_as_many_as_faithful():
    faithful = V.enumerate_setups("MES", "2025-06-02", "2025-06-13", want=("TAKE",))
    relaxed = V.enumerate_setups("MES", "2025-06-02", "2025-06-13", want=("TAKE",), relaxed=True)
    assert len(relaxed) >= len(faithful)             # relaxing filters can only surface ≥ as many
    # configure() must have restored faithful defaults after the relaxed run
    from ict_v2 import recommend as REC
    assert REC.REQUIRE_RETRACE is True and REC.COURSE_FILTERS["min_rr"] == 2.0
