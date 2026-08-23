"""Outcome labelling: ordered stop/target, intrabar ambiguity, fill, MFE/MAE, fixed-R, horizon,
and the two required guards (outcome keys never in features; outcome pass can't change the engine)."""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ict_live.engine import outcomes, pipeline, replay
from ict_live.engine.outcomes import OUTCOME_TOP_KEYS, label_setup
from ict_live.market.bar import Bar

ET = ZoneInfo("America/New_York")
T0 = datetime(2026, 6, 2, 9, 0, tzinfo=ET)      # a normal open (Tue session)


def _b(i, o, h, l, c):
    return Bar("15m", T0 + timedelta(minutes=15 * i), T0 + timedelta(minutes=15 * (i + 1)),
               o, h, l, c, 1.0)


def test_short_target_hit_first():
    # short entry 100, stop 102, target 94 (RR 3). fill bar0; bar2 reaches target cleanly.
    bars = [_b(0, 100, 100.5, 99, 99.5), _b(1, 99.5, 100, 97, 98), _b(2, 98, 98, 93.5, 94)]
    r = label_setup(id="S1", symbol="X", tf="15m", direction="short", entry=100, stop=102,
                    target=94, decision_index=0, bars=bars, horizon_bars=10)
    assert r["fill_index"] == 0
    assert r["course_execution"]["result"] == "TARGET"
    assert r["course_execution"]["realized_R"] == 3.0
    assert r["liquidity_target"]["reached"] is True


def test_short_stop_hit_first():
    bars = [_b(0, 100, 100.5, 99, 100), _b(1, 100, 103, 99, 102.5)]   # bar1 high 103 >= stop 102
    r = label_setup(id="S2", symbol="X", tf="15m", direction="short", entry=100, stop=102,
                    target=94, decision_index=0, bars=bars, horizon_bars=10)
    assert r["course_execution"]["result"] == "STOP" and r["course_execution"]["realized_R"] == -1.0


def test_ambiguous_intrabar_not_favorable():
    # one bar touches BOTH stop (102) and target (94) -> AMBIGUOUS, never TARGET
    bars = [_b(0, 100, 100.5, 99, 100), _b(1, 100, 103, 93, 95)]
    r = label_setup(id="S3", symbol="X", tf="15m", direction="short", entry=100, stop=102,
                    target=94, decision_index=0, bars=bars, horizon_bars=10)
    assert r["course_execution"]["result"] == "AMBIGUOUS_INTRABAR"
    assert r["course_execution"]["realized_R"] is None


def test_no_fill_when_entry_never_touched():
    bars = [_b(0, 90, 91, 89, 90), _b(1, 90, 92, 88, 89)]              # never reaches entry 100
    r = label_setup(id="S4", symbol="X", tf="15m", direction="short", entry=100, stop=102,
                    target=94, decision_index=0, bars=bars, horizon_bars=10)
    assert r["fill_index"] is None and r["course_execution"]["result"] == "NO_FILL"


def test_horizon_marks_to_close():
    bars = [_b(0, 100, 100.5, 99.5, 100), _b(1, 100, 100.2, 99, 99.5), _b(2, 99.5, 99.8, 99, 99)]
    r = label_setup(id="S5", symbol="X", tf="15m", direction="short", entry=100, stop=102,
                    target=90, decision_index=0, bars=bars, horizon_bars=2)
    assert r["course_execution"]["result"] == "HORIZON"
    # mark-to-close R = (100 - 99)/2 = 0.5
    assert r["course_execution"]["realized_R"] == 0.5


def test_fixed_r_and_mfe_mae_from_fill():
    # short entry 100 stop 102 (risk 2). price goes to 96 (=2R) then back; horizon before stop.
    bars = [_b(0, 100, 100.5, 99, 99.5), _b(1, 99.5, 100, 95.9, 96.5), _b(2, 96.5, 99, 96, 98)]
    r = label_setup(id="S6", symbol="X", tf="15m", direction="short", entry=100, stop=102,
                    target=90, decision_index=0, bars=bars, horizon_bars=3)
    assert r["fixed_r"]["r1_hit"] is True and r["fixed_r"]["r2_hit"] is True
    assert r["fixed_r"]["r3_hit"] is False
    assert r["excursion"]["mfe_R"] >= 2.0 and r["excursion"]["mae_R"] >= 0.0


# ---- the two REQUIRED guards ----
def _series(n, seed=11):
    bars, px, x = [], 20000.0, seed
    for i in range(n):
        x = (1103515245 * x + 12345) % (2 ** 31)
        step = ((x % 21) - 10) * 1.5
        o, c = px, px + step
        ot = T0 + timedelta(minutes=15 * i)
        bars.append(Bar("15m", ot, ot + timedelta(minutes=15), o, max(o, c) + (x % 7),
                        min(o, c) - (x % 5), c, 100.0))
        px = c
    return bars


def test_outcome_keys_never_appear_in_feature_records(tmp_path):
    bars = _series(80)
    out = tmp_path / "ds.jsonl"
    replay.generate(bars, "15m", {"symbol": "X", "contract": "Xc"}, warmup=20, out_path=str(out))
    for line in out.read_text().splitlines():
        rec = json.loads(line)
        assert OUTCOME_TOP_KEYS.isdisjoint(rec.keys()), \
            f"outcome key leaked into {rec['type']}: {set(rec) & OUTCOME_TOP_KEYS}"


def test_outcome_pass_cannot_change_recommendations():
    bars = _series(60)
    before = pipeline.analyze(bars[:50], "15m").recommendation.decision
    # run the outcome pass (touches future bars) on a fabricated setup...
    label_setup(id="Z", symbol="X", tf="15m", direction="short", entry=bars[49].close,
                stop=bars[49].close + 10, target=bars[49].close - 40, decision_index=49,
                bars=bars, horizon_bars=10)
    after = pipeline.analyze(bars[:50], "15m").recommendation.decision
    assert before == after                       # labelling is pure/offline; engine unaffected
