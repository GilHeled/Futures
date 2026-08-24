"""MTFEngine cadence: the HTF context stays FIXED across LTF closes and only changes on an HTF
close; the MTF setup only changes on an MTF close; execution re-evaluates on every LTF close."""
from ict_v2 import pipeline as P
from ict_v2.engine import MTFEngine


def _data(seed=7):
    base = P._base_1m(20000, seed)
    return base, P.resample(base, 240, "4H"), P.resample(base, 15, "15m")


def test_execution_reacts_per_ltf_close_context_fixed():
    base, htf, mtf = _data()
    eng = MTFEngine("4H", "15m", "1m")
    eng.on_htf_close(htf)
    eng.on_mtf_close(mtf)
    ctx, setup = eng.context, eng.setup
    # several successive LTF closes — execution refreshes, but context & setup are the SAME objects
    e1 = eng.on_ltf_close(base[:300][-400:])
    e2 = eng.on_ltf_close(base[:360][-400:])
    assert eng.context is ctx                      # HTF context fixed across LTF closes
    assert eng.setup is setup                      # MTF setup fixed across LTF closes
    assert isinstance(e1, P.LTFExecution) and isinstance(e2, P.LTFExecution)
    assert eng.execution is e2                     # latest LTF close is current


def test_htf_close_updates_context_mtf_close_updates_setup():
    base, htf, mtf = _data()
    eng = MTFEngine("4H", "15m", "1m")
    eng.on_htf_close(htf)
    c1 = eng.context
    eng.on_htf_close(P.resample(base[:-1200], 240, "4H"))   # a different HTF window
    assert eng.context is not c1                    # HTF close DOES recompute the context
    eng.on_mtf_close(mtf)
    s1 = eng.setup
    eng.on_mtf_close(P.resample(base[:-900], 15, "15m"))
    assert eng.setup is not s1                       # MTF close DOES recompute the setup


def test_no_setup_before_context_and_no_exec_before_setup():
    base, htf, mtf = _data()
    eng = MTFEngine("4H", "15m", "1m")
    assert eng.on_mtf_close(mtf) is None             # setup needs a context first
    e = eng.on_ltf_close(base[-400:])                # execution needs a setup first
    assert e.decision.startswith("NO-TRADE") and e.executables == []
