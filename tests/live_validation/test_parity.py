"""
Signal-parity: the live streaming path must reproduce the batch
frozen-model computation exactly (prefix-stability / causality). This is
the make-or-break gate before any live number is trusted -- if the
streaming engine diverges from the batch reference, the shadow log is
meaningless. Also exercises the shadow book end-to-end on replayed bars.

Uses cached Databento bars; skipped if the cache is absent.
"""
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from live_validation.bundle import build_bundle
from live_validation.inference import compute_frame
from live_validation.signal_engine import StreamingSignalEngine
from live_validation.shadow_book import ShadowBook
from mnq_system.cli import _resolve_contract_spec
from mnq_system.config import DEFAULT_ACCOUNT_CONFIG
from mnq_system.data.providers import build_provider

CACHE = Path(__file__).resolve().parents[2] / "cache" / "bars" / "databento_MYM_5m.parquet"
pytestmark = pytest.mark.skipif(not CACHE.exists(), reason="cached MYM bars required")


@pytest.fixture(scope="module")
def setup():
    provider = build_provider("databento", cache=True)
    bars = provider.get_historical_bars(
        "MYM", pd.Timestamp("2022-01-01", tz="UTC").to_pydatetime(),
        pd.Timestamp("2023-07-01", tz="UTC").to_pydatetime(), "5m")
    account = replace(DEFAULT_ACCOUNT_CONFIG, contract=_resolve_contract_spec("MYM"))
    tick, pv = account.contract.tick_size, account.contract.point_value
    train_end = pd.Timestamp("2023-03-01", tz="UTC")
    bundle = build_bundle(bars, "MYM", tick, pv, account, train_end)
    return bars, account, bundle


def test_streaming_equals_batch(setup):
    """prefix compute == batch at each of the last K positions."""
    bars, account, bundle = setup
    window = bars.iloc[-2500:]
    batch = compute_frame(window, bundle, account)
    K = 30
    for t in range(len(window) - K, len(window)):
        last = compute_frame(window.iloc[: t + 1], bundle, account).iloc[-1]
        b = batch.iloc[t]
        for col in ["ev", "atr", "cost_hurdle", "direction", "off_hours"]:
            a, bb = last[col], b[col]
            if pd.isna(a) and pd.isna(bb):
                continue
            assert np.isclose(float(a), float(bb), rtol=1e-9, atol=1e-9), f"{col} mismatch at t={t}"


def test_engine_matches_batch_direction(setup):
    bars, account, bundle = setup
    eng = StreamingSignalEngine(bundle, account, buffer_bars=3000)
    tail = bars.iloc[-150:]
    batch_dir = compute_frame(bars.iloc[-3150:], bundle, account)["direction"].iloc[-150:].tolist()
    for (ts, r), bd in zip(tail.iterrows(), batch_dir):
        sig = eng.push(ts, {"open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"], "volume": r.get("volume", 0)})
        assert sig["direction"] == (0 if pd.isna(bd) else int(bd))


def test_shadow_book_records_dual_fills(setup):
    """A stopped/exited shadow trade records both expected and crossing
    fills and finite R (quoteless replay => crossing==expected)."""
    bars, account, bundle = setup
    frame = compute_frame(bars, bundle, account)
    book = ShadowBook("MYM", bundle)
    trades = []
    for ts in frame.index:
        r = bars.loc[ts]
        f = frame.loc[ts]
        ohlc = {"open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"],
                "atr": (float(f["atr"]) if pd.notna(f["atr"]) else None)}
        ev = float(f["ev"]) if pd.notna(f["ev"]) else None
        d = int(f["direction"]) if pd.notna(f["direction"]) else 0
        tr = book.on_bar(ts, ohlc, ev, d, bool(f["off_hours"]), bool(f["session_ending"]))
        if tr:
            trades.append(tr)
    assert len(trades) > 0
    for tr in trades:
        assert tr["r_expected"] is not None and np.isfinite(tr["r_expected"])
        assert tr["exit_reason"] in {"stop", "ev_reversal", "max_hold"}
        # quoteless replay: crossing fill falls back to expected convention
        assert np.isclose(tr["entry_crossing"], tr["entry_expected"])
        assert tr["bundle_version"] and tr["model_version"]


def test_contract_mismatch_excluded(setup):
    """A fill whose quote is from a different contract (quote_valid=False)
    must be recorded as an INVALID execution observation -- crossing fill and
    slippage None, execution_valid False -- never fabricated from expected."""
    _, _, bundle = setup
    book = ShadowBook("MYM", bundle)
    # open a long off-hours (valid quote), atr=1 -> stop at 98.5
    o1 = {"open": 100.0, "high": 100.5, "low": 99.8, "close": 100.0, "atr": 1.0}
    assert book.on_bar("t0", o1, ev=0.5, direction_signal=1, off_hours=True, session_ending=False,
                       quote={"bid": 99.99, "ask": 100.01, "instrument_id": 1}, quote_valid=True) is None
    # next bar gaps through the stop, but the quote is from a DIFFERENT contract
    o2 = {"open": 99.0, "high": 99.0, "low": 98.0, "close": 98.2, "atr": 1.0}
    tr = book.on_bar("t1", o2, ev=0.5, direction_signal=0, off_hours=True, session_ending=False,
                     quote=None, quote_valid=False)
    assert tr is not None and tr["exit_reason"] == "stop"
    assert tr["execution_valid"] is False
    assert tr["exit_crossing"] is None and tr["r_crossing"] is None
    assert tr["exit_slippage_ticks"] is None
    # expected-fill R is still computable (does not depend on a quote)
    assert tr["r_expected"] is not None and np.isfinite(tr["r_expected"])
