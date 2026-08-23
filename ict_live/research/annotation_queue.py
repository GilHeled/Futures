"""Active-learning annotation queue — maximize learning per minute of human annotation.

Scores each candidate scene by expected INFORMATION VALUE (from the deterministic engine only) and
ranks the queue so the highest-value scenes are annotated first. High value = many competing
candidates, close/tied rankings, a marginal decision (NO-TRADE competing closely with LONG/SHORT),
disagreement with a prior human label, or a recommendation that changed across engine versions.
Trivial scenes (one obvious candidate, clear decision) score low and sink.

The score is a TRANSPARENT weighted sum of named components (weights exposed, tunable) — it only
orders the annotation queue; it never ranks trade candidates (that stays lexicographic per B3) and
never invents labels. Pandas-free scorer (engine only); the queue generator uses research.data.
"""
from __future__ import annotations

from ict_live.engine.pipeline import MarketState

# transparent, tunable weights (exposed). AMBIGUITY-centric, not volume-centric: a scene with
# hundreds of raw objects is not informative just for being busy — what teaches is a HARD DECISION
# (both sides actionable, marginal calls, near-misses, close rankings, disagreement).
WEIGHTS = {"setups_competing": 0.4, "direction_conflict": 3.0, "close_ranking": 1.5,
           "decision_marginal": 2.0, "near_miss_setups": 0.4, "disagree_prior": 3.0,
           "version_changed": 3.0}


def information_value(ms: MarketState, *, prior_decision=None, version_changed: bool = False) -> dict:
    rs, rk = ms.ranked_sweeps, ms.ranked_setups
    dec = ms.recommendation.decision
    # lifecycle-aware: score ambiguity over CURRENT COMPETITORS only (not resolved/historical theses).
    # An EMPTY current set is a valid state (0 current) — only fall back to actionable when the engine
    # produced NO lifecycle at all.
    life = ms.lifecycle or {}
    if life:
        cur = [r for r in rk if r.item.id in life.get("current_setup_ids", set())]
        active_ids = life.get("active_setup_ids", set())
    else:
        cur = [r for r in rk if r.item.actionable]
        active_ids = set()

    setups_competing = min(len(cur), 8)                  # current competing TRADE candidates
    long_act = any(r.item.direction == "long" for r in cur)
    short_act = any(r.item.direction == "short" for r in cur)
    direction_conflict = 1 if (long_act and short_act) else 0     # both sides live = ambiguous

    def close_top(L):                       # top-2 share their leading factor value -> a close call
        return 1 if len(L) >= 2 and L[0].key[:1] == L[1].key[:1] else 0
    close_ranking = close_top(cur) + close_top(rs)       # current-decision + manipulation layers

    n_act = len(cur)
    # near-miss = live (active) theses rejected only by RR — a small nudge could flip the decision
    near_miss = min(sum(1 for r in rk if r.item.id in active_ids and "RR" in (r.item.reject_reason or "")), 5) \
        if active_ids else min(sum(1 for r in rk if not r.item.actionable and "RR" in (r.item.reject_reason or "")), 5)
    decision_marginal = (1 if 0 < n_act <= 2 else 0) + (1 if dec == "NO-TRADE" and near_miss else 0)
    disagree_prior = 1 if (prior_decision is not None and prior_decision != dec) else 0

    factors = {"setups_competing": setups_competing, "direction_conflict": direction_conflict,
               "close_ranking": close_ranking, "decision_marginal": decision_marginal,
               "near_miss_setups": near_miss, "disagree_prior": disagree_prior,
               "version_changed": 1 if version_changed else 0}
    score = round(sum(WEIGHTS[k] * factors[k] for k in WEIGHTS), 3)
    return {"score": score, "factors": factors, "weights": WEIGHTS, "engine_decision": dec,
            "n_actionable": n_act}


def is_trivial(iv: dict) -> bool:
    """A scene with at most one trade candidate and a clear, unambiguous decision teaches little."""
    f = iv["factors"]
    return (f["setups_competing"] <= 1 and f["direction_conflict"] == 0 and f["close_ranking"] == 0
            and f["decision_marginal"] == 0 and f["disagree_prior"] == 0 and f["version_changed"] == 0)


def generate_queue(symbol: str, *, signal_tf: str = "1H", entry_tf: str = "15m",
                   dev_start: str = "2019-05-01", dev_end: str = "2025-01-01",
                   stride: int = 6, window: int = 240, top_n: int = 200,
                   prior_decisions: dict | None = None, changed_scenes: set | None = None,
                   min_stop: float | None = None, out_path: str | None = None) -> dict:
    """Scan dev scenes causally (every `stride` signal bars), score by information value, dedupe
    persistence, and return the top-N queue ranked by score. LOCKED OOS (>=2025) is never scanned."""
    import json
    from ict_live.engine import pipeline
    from ict_live.research import data as data_mod
    from ict_live.research import rolls as rolls_mod

    assert dev_end <= "2025-01-01", "dev_end must not enter the locked OOS"
    prior_decisions = prior_decisions or {}
    changed_scenes = changed_scenes or set()
    bars5 = data_mod.load_5m(symbol, start=dev_start, end=dev_end)
    segments = rolls_mod.segment(bars5, rolls_mod.detect_rolls(bars5, symbol))

    scored, seen_sig = [], set()
    for si, seg in enumerate(segments):
        if si == 0:
            continue                        # first segment is load-truncated (no prior roll in range)
            #                                 → not reproducible by build_state; skip for consistency
        sig = data_mod.resample(seg.bars, signal_tf)
        ref_all = data_mod.resample(seg.bars, entry_tf)
        for k in range(window, len(sig), stride):
            cc = sig[k].close_time
            refine = [b for b in ref_all if b.close_time <= cc]
            ms = pipeline.analyze(sig[max(0, k - window + 1):k + 1], signal_tf,
                                  refine_bars=refine, min_stop=min_stop)
            scene_id = f"{symbol}:{seg.contract}:{signal_tf}:{sig[k].open_time.isoformat()}"
            iv = information_value(ms, prior_decision=prior_decisions.get(scene_id),
                                   version_changed=scene_id in changed_scenes)
            if is_trivial(iv):
                continue
            # dedupe: at most one scene per contract-day-decision (don't flood on one busy day)
            sigkey = (seg.contract, sig[k].open_time.date().isoformat(), iv["engine_decision"])
            if sigkey in seen_sig:
                continue
            seen_sig.add(sigkey)
            scored.append({"scene_id": scene_id, "symbol": symbol, "contract": seg.contract,
                           "signal_tf": signal_tf, "entry_tf": entry_tf,
                           "date": sig[k].open_time.date().isoformat(),
                           "time": sig[k].open_time.isoformat(), **iv})
    scored.sort(key=lambda r: r["score"], reverse=True)
    queue = scored[:top_n]
    if out_path:
        from pathlib import Path
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text("\n".join(json.dumps(r, default=str) for r in queue))
    return {"symbol": symbol, "scanned": len(scored), "queued": len(queue),
            "top_score": queue[0]["score"] if queue else 0, "out_path": out_path, "queue": queue}


def execution_selectivity(ms) -> dict:
    """Batch-2 (adversarial): score scenes with a CURRENT WINNING actionable setup by how much they
    stress EXECUTION QUALITY / LOCATION — where a structurally-valid setup may still be a poor trade.
    High value = P/D conflict (long above CE / short below CE), very high RR (wide target masking a
    mediocre entry), and entry far from CE. Engine logic is untouched; this only prioritizes scenes."""
    win = ms.recommendation.setup
    if win is None or not ms.ranges:
        return {}
    dr = ms.ranges[0]
    span = (dr.high - dr.low) or 1e-9
    ce = dr.ce
    premium = win.entry > ce
    pd_conflict = (win.direction == "long" and premium) or (win.direction == "short" and not premium)
    loc_norm = round((win.entry - dr.low) / span, 4)               # 0=range low, 1=range high
    entry_ce_dist = round(abs(win.entry - ce) / span, 4)           # 0=at CE, .5=at an extreme
    rr = win.rr
    rr_bin = ">=25" if rr >= 25 else ("20-25" if rr >= 20 else ("10-20" if rr >= 10 else "<10"))
    score = round(3.0 * pd_conflict + (3 if rr >= 25 else 2 if rr >= 20 else 1 if rr >= 10 else 0)
                  + 2.0 * entry_ce_dist, 3)
    return {"score": score, "engine_decision": ms.recommendation.decision,
            "factors": {"pd_conflict": int(pd_conflict), "rr": round(rr, 2), "rr_bin": rr_bin,
                        "entry_ce_dist": entry_ce_dist, "loc_norm": loc_norm,
                        "dr_zone": dr.zone_of(win.entry), "direction": win.direction}}


def generate_execution_batch(symbol: str, *, signal_tf: str = "1H", entry_tf: str = "15m",
                             dev_start: str = "2019-05-01", dev_end: str = "2025-01-01",
                             stride: int = 6, window: int = 240, top_n: int = 60,
                             min_stop: float | None = None, out_path: str | None = None) -> dict:
    """Scan dev for scenes with a current winning setup, score by execution_selectivity, dedupe per
    contract-day, and return the top-N (LOCKED OOS >=2025 never scanned)."""
    import json
    from ict_live.engine import pipeline
    from ict_live.research import data as data_mod
    from ict_live.research import rolls as rolls_mod

    assert dev_end <= "2025-01-01", "dev_end must not enter the locked OOS"
    bars5 = data_mod.load_5m(symbol, start=dev_start, end=dev_end)
    segments = rolls_mod.segment(bars5, rolls_mod.detect_rolls(bars5, symbol))
    scored, seen = [], set()
    for si, seg in enumerate(segments):
        if si == 0:
            continue
        sig = data_mod.resample(seg.bars, signal_tf)
        ref_all = data_mod.resample(seg.bars, entry_tf)
        for k in range(window, len(sig), stride):
            cc = sig[k].close_time
            ms = pipeline.analyze(sig[max(0, k - window + 1):k + 1], signal_tf,
                                  refine_bars=[b for b in ref_all if b.close_time <= cc], min_stop=min_stop)
            if ms.recommendation.decision not in ("LONG", "SHORT"):
                continue                                            # need a live trade to judge
            iv = execution_selectivity(ms)
            if not iv:
                continue
            key = (seg.contract, sig[k].open_time.date().isoformat())
            if key in seen:
                continue
            seen.add(key)
            scored.append({"scene_id": f"{symbol}:{seg.contract}:{signal_tf}:{sig[k].open_time.isoformat()}",
                           "symbol": symbol, "contract": seg.contract, "signal_tf": signal_tf,
                           "entry_tf": entry_tf, "date": sig[k].open_time.date().isoformat(),
                           "time": sig[k].open_time.isoformat(), **iv})
    scored.sort(key=lambda r: r["score"], reverse=True)
    queue = scored[:top_n]
    if out_path:
        from pathlib import Path
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text("\n".join(json.dumps(r, default=str) for r in queue))
    return {"symbol": symbol, "scanned": len(scored), "queued": len(queue), "out_path": out_path,
            "queue": queue}
