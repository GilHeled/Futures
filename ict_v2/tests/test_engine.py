"""MTFEngine cascade (4H context -> 1H setup -> 15m confirmation -> 1m execution): each layer
recomputes only on its own close; higher layers stay fixed across lower closes."""
from ict_v2 import pipeline as P
from ict_v2.engine import MTFEngine


def _data(seed=7):
    base = P._base_1m(20000, seed)
    return base, P.resample(base, 240, "4H"), P.resample(base, 60, "1H"), P.resample(base, 15, "15m")


def test_all_four_layers_present_and_context_fixed_on_trigger():
    base, h4, h1, m15 = _data()
    eng = MTFEngine("4H", "1H", "15m", "1m")
    eng.on_context_close(h4)
    eng.on_setup_close(h1)
    eng.on_confirm_close(m15)
    ctx, setup, conf = eng.context, eng.setup, eng.confirmation
    e = eng.on_trigger_close(base[-400:])
    # a 1m trigger recomputes ONLY execution — context/setup/confirmation are the same objects
    assert eng.context is ctx and eng.setup is setup and eng.confirmation is conf
    assert isinstance(e, P.LTFExecution)
    st = eng.state()
    assert st.context and st.setup and st.confirmation and st.execution


def test_layers_recompute_only_on_their_own_close():
    base, h4, h1, m15 = _data()
    eng = MTFEngine("4H", "1H", "15m", "1m")
    eng.on_context_close(h4)
    c1 = eng.context
    eng.on_context_close(P.resample(base[:-1200], 240, "4H"))
    assert eng.context is not c1                            # a 4H close recomputes context
    eng.on_setup_close(h1)
    s1 = eng.setup
    eng.on_confirm_close(m15)                               # a 15m close does NOT touch the 1H setup
    assert eng.setup is s1


def test_cascade_is_top_down():
    base, h4, h1, m15 = _data()
    eng = MTFEngine("4H", "1H", "15m", "1m")
    assert eng.on_setup_close(h1) is None                  # setup needs context
    assert eng.on_confirm_close(m15) is None               # confirmation needs context
    e = eng.on_trigger_close(base[-400:])                  # no context -> no trade
    assert e.decision.startswith("NO-TRADE")
