"""MTFEngine — responsibility cascade: 4H strategic context · 1H intraday context · scenario layer ·
M15/M1 execution. Context stages recompute only on their own close; the scenario set is stable."""
from ict_v2 import pipeline as P
from ict_v2.engine import MTFEngine


def _data(seed=7):
    base = P._base_1m(20000, seed)
    return base, P.resample(base, 240, "4H"), P.resample(base, 60, "1H"), P.resample(base, 15, "15m")


def test_context_stages_fixed_across_execution_closes():
    base, h4, h1, m15 = _data()
    eng = MTFEngine("4H", "1H", "15m", "1m")
    eng.on_trigger_close(base[-400:])                      # prime price
    eng.on_context_close(h4); eng.on_setup_close(h1); eng.on_confirm_close(m15)
    strat, intra = eng.strategic, eng.intraday
    eng.on_trigger_close(base[-400:])                      # a 1m trigger MONITORS scenarios only
    assert eng.strategic is strat and eng.intraday is intra   # context objects untouched
    assert eng.strategic is not None and eng.intraday is not None


def test_context_recomputes_only_on_its_own_close():
    base, h4, h1, m15 = _data()
    eng = MTFEngine("4H", "1H", "15m", "1m")
    eng.on_context_close(h4); s1 = eng.strategic
    eng.on_context_close(P.resample(base[:-1200], 240, "4H"))
    assert eng.strategic is not s1                         # a 4H close rebuilds strategic context
    eng.on_setup_close(h1); i1 = eng.intraday
    eng.on_confirm_close(m15)                              # a 15m close does NOT touch the 1H context
    assert eng.intraday is i1


def test_context_required_before_scenarios():
    base, h4, h1, m15 = _data()
    eng = MTFEngine("4H", "1H", "15m", "1m")
    assert eng.on_setup_close(h1) is None                  # intraday context needs strategic first
    eng.on_trigger_close(base[-400:])                      # no context → no scenarios
    assert eng.book.active == []


def test_scenario_set_is_small_and_stable():
    base, h4, h1, m15 = _data()
    eng = MTFEngine("4H", "1H", "15m", "1m")
    eng.on_trigger_close(base[-400:])
    eng.on_context_close(h4); eng.on_setup_close(h1)
    assert 0 <= len(eng.book.active) <= 3                  # target 2, max 3 — never a scanner
    ids = [s.scenario_id for s in eng.book.active]
    eng.on_setup_close(h1)                                 # same context again → identical set (no churn)
    assert [s.scenario_id for s in eng.book.active] == ids


def test_describe_runs():
    base, h4, h1, m15 = _data()
    eng = MTFEngine("4H", "1H", "15m", "1m")
    eng.on_trigger_close(base[-400:]); eng.on_context_close(h4); eng.on_setup_close(h1)
    assert "scenario" in eng.describe()
