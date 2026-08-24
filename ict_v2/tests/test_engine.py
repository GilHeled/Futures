"""MTFEngine cadence (4H context -> 1H setup -> 15m/1m execution): each layer recomputes only on its
own close; context stays fixed across setup/exec closes; execution steps down 15m -> 1m."""
from ict_v2 import pipeline as P
from ict_v2.engine import MTFEngine


def _data(seed=7):
    base = P._base_1m(20000, seed)
    return base, P.resample(base, 240, "4H"), P.resample(base, 60, "1H"), P.resample(base, 15, "15m")


def test_execution_reacts_per_exec_close_context_fixed():
    base, h4, h1, m15 = _data()
    eng = MTFEngine("4H", "1H", ("15m", "1m"))
    eng.on_context_close(h4)
    eng.on_setup_close(h1)
    ctx, setup = eng.context, eng.setup
    e15 = eng.on_exec_close("15m", m15)
    e1 = eng.on_exec_close("1m", base[:300][-400:])
    e1b = eng.on_exec_close("1m", base[:360][-400:])
    assert eng.context is ctx                        # 4H context fixed across exec closes
    assert eng.setup is setup                        # 1H setup fixed across exec closes
    assert all(isinstance(x, P.LTFExecution) for x in (e15, e1, e1b))
    assert eng.executions["1m"] is e1b and eng.executions["15m"] is e15


def test_context_and_setup_recompute_only_on_their_close():
    base, h4, h1, m15 = _data()
    eng = MTFEngine("4H", "1H", ("15m", "1m"))
    eng.on_context_close(h4)
    c1 = eng.context
    eng.on_context_close(P.resample(base[:-1200], 240, "4H"))
    assert eng.context is not c1                      # a 4H close recomputes context
    eng.on_setup_close(h1)
    s1 = eng.setup
    eng.on_setup_close(P.resample(base[:-600], 60, "1H"))
    assert eng.setup is not s1                         # a 1H close recomputes setup


def test_no_setup_before_context_and_no_exec_before_setup():
    base, h4, h1, m15 = _data()
    eng = MTFEngine("4H", "1H", ("15m", "1m"))
    assert eng.on_setup_close(h1) is None              # setup needs a context first
    e = eng.on_exec_close("1m", base[-400:])           # execution needs a setup first
    assert e.decision.startswith("NO-TRADE") and e.executables == []


def test_current_execution_prefers_finest_with_a_trade():
    # 1m execution (if it has an executable) supersedes 15m in the step-down
    eng = MTFEngine("4H", "1H", ("15m", "1m"))
    eng.executions["15m"] = P.LTFExecution(tf="15m", executables=["x"], decision="LONG")
    eng.executions["1m"] = P.LTFExecution(tf="1m", executables=["y"], decision="SHORT")
    assert eng.current_execution().tf == "1m"
    eng.executions["1m"] = P.LTFExecution(tf="1m", executables=[], decision="NO-TRADE")
    assert eng.current_execution().tf == "15m"         # falls back to the coarser TF that has one
