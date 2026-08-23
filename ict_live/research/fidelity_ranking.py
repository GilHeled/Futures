"""Fidelity selection — the learned SHADOW ranker + NO-TRADE abstention.

Problem (per the methodology owner): given ALL currently valid MTF setup candidates, which one — if
any — would the discretionary methodology actually select? Output is a probability per candidate
PLUS a real NO_TRADE class. Features come ONLY from the deterministic engine (never P&L, never the
market outcome). Runs SHADOW ONLY — it never changes the deterministic recommendation.

Hard deterministic constraints (causality, valid FVG/geometry, min RR, valid stop, session/data)
stay in the engine; this layer only RANKS/abstains among the survivors.

With very few labels (we currently have 6 recovered scenes) the ranker must NOT pretend to be
trained — `fit` reports `insufficient_training_data` and `predict` abstains. This file is the
schema + interface so we are moving correctly; it will train once enough annotations accrue.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

# thresholds below which we refuse to train (avoid overfitting a handful of scenes)
MIN_LABELED_SCENES = 40
MIN_CHOSEN_CANDIDATES = 15

# engine-only candidate features the ranker is allowed to use (all knowable at decision time)
FEATURE_KEYS = ["rr", "rank", "n_competing_setups", "dr_location_norm", "sweep_rank",
                "sweep_rejection", "mss_rank", "mss_acceptance", "fvg_rank", "displacement_net",
                "displacement_speed", "n_active_erl", "n_structural", "risk"]
CAT_KEYS = ["direction", "structure_tf", "entry_tf", "dr_zone", "session", "fvg_status", "mss_state"]


@dataclass
class SelectionExample:
    scene_id: str
    symbol: str
    date: str
    tf: str
    human_decision: str                      # LONG / SHORT / NO_TRADE
    candidates: list                         # [{candidate_id, features:{...}}]
    human_chosen_candidate_id: Optional[str] = None   # candidate-level label if available
    provenance: str = "recovered"
    blinded: bool = False


def candidate_features(ms, r_setup, bar) -> dict:
    """Per-candidate engine-only feature record for the ranker (reuses the leakage-safe builder)."""
    from ict_live.engine import features
    f = features.setup_feature_record(ms, r_setup, bar, {"symbol": None, "contract": None})
    return {k: f.get(k) for k in (FEATURE_KEYS + CAT_KEYS)}


def example_from_state(ms, bar, *, scene_id, symbol, date, tf, human_decision,
                       human_chosen_candidate_id=None, provenance="recovered", blinded=False):
    cands = [{"candidate_id": r.item.id, "features": candidate_features(ms, r, bar)}
             for r in ms.ranked_setups]
    return SelectionExample(scene_id=scene_id, symbol=symbol, date=date, tf=tf,
                            human_decision=human_decision, candidates=cands,
                            human_chosen_candidate_id=human_chosen_candidate_id,
                            provenance=provenance, blinded=blinded)


@dataclass
class FidelityRanker:
    trained: bool = False
    status: str = "untrained"
    n_scenes: int = 0
    n_chosen: int = 0
    _model: object = None
    _cols: list = field(default_factory=list)

    def fit(self, examples: list[SelectionExample]) -> "FidelityRanker":
        self.n_scenes = len(examples)
        self.n_chosen = sum(1 for e in examples if e.human_chosen_candidate_id)
        if self.n_scenes < MIN_LABELED_SCENES or self.n_chosen < MIN_CHOSEN_CANDIDATES:
            self.trained = False
            self.status = (f"insufficient_training_data "
                           f"({self.n_scenes} scenes / {self.n_chosen} candidate-level choices; "
                           f"need >= {MIN_LABELED_SCENES} scenes and {MIN_CHOSEN_CANDIDATES} choices)")
            return self
        # (training path — only reached once enough labels accrue)
        import numpy as np
        import pandas as pd
        from sklearn.linear_model import LogisticRegression
        rows, y = [], []
        for e in examples:
            for c in e.candidates:
                rows.append(c["features"])
                y.append(1 if c["candidate_id"] == e.human_chosen_candidate_id else 0)
        X = pd.get_dummies(pd.DataFrame(rows), columns=CAT_KEYS, dummy_na=True)
        for col in FEATURE_KEYS:
            if col in X:
                X[col] = pd.to_numeric(X[col], errors="coerce").fillna(0.0)
        self._cols = list(X.columns)
        self._model = LogisticRegression(max_iter=2000, class_weight="balanced").fit(X.values, y)
        self.trained, self.status = True, "trained"
        return self

    def predict(self, candidates: list) -> dict:
        """Per-candidate P(the human would select it) + P(NO_TRADE). Abstains if untrained."""
        if not self.trained:
            return {"status": self.status, "abstain": True,
                    "per_candidate": [{"candidate_id": c["candidate_id"], "p": None} for c in candidates],
                    "p_no_trade": None}
        import pandas as pd
        X = pd.get_dummies(pd.DataFrame([c["features"] for c in candidates]),
                           columns=CAT_KEYS, dummy_na=True).reindex(columns=self._cols, fill_value=0)
        for col in FEATURE_KEYS:
            if col in X:
                X[col] = pd.to_numeric(X[col], errors="coerce").fillna(0.0)
        p = self._model.predict_proba(X.values)[:, 1]
        per = [{"candidate_id": c["candidate_id"], "p": round(float(pi), 4)}
               for c, pi in zip(candidates, p)]
        # NO-TRADE probability: the scene is a no-trade to the extent no candidate looks selectable
        p_nt = round(float((1.0 - max(p)) if len(p) else 1.0), 4)
        return {"status": "trained", "abstain": False, "per_candidate": per, "p_no_trade": p_nt}


def shadow_report(ms, ranker: FidelityRanker, bar) -> dict:
    """SHADOW comparison for a recommendation: deterministic decision vs fidelity ranker (no effect
    on the engine's recommendation)."""
    cands = [{"candidate_id": r.item.id, "features": candidate_features(ms, r, bar)}
             for r in ms.ranked_setups]
    pred = ranker.predict(cands)
    return {"deterministic": ms.recommendation.decision,
            "deterministic_setup": ms.recommendation.setup.id if ms.recommendation.setup else None,
            "fidelity_shadow": pred, "shadow_only": True}
