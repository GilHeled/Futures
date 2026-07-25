"""
Purged + embargoed expanding walk-forward CV (López de Prado).

The forward-RV label spans HORIZON_BARS bars into the future, so a training
sample whose label window reaches into (or near) the test period shares
information with it. For each chronological test fold, the training set keeps
only samples whose label window ended before the test fold starts, minus an
embargo — removing that leakage. Fold 0 is never a test fold (no prior data).

Self-contained copy (the generic routine from the prior project) so this frozen
research package carries no dependency on a closed experiment.
"""
from __future__ import annotations

import numpy as np


def purged_walk_forward_splits(entry_pos, exit_pos, n_splits: int, embargo_bars: int = 0):
    """entry_pos / exit_pos: positional bar indices per sample (same length).
    Yields (train_idx, test_idx) arrays of SAMPLE indices, expanding-window,
    purged + embargoed. Fold 0 is training-only (never yielded as a test fold)."""
    entry_pos = np.asarray(entry_pos)
    exit_pos = np.asarray(exit_pos)
    order = np.argsort(entry_pos, kind="stable")
    folds = np.array_split(order, n_splits)

    for f in range(1, n_splits):
        test_idx = folds[f]
        if len(test_idx) == 0:
            continue
        test_start = int(entry_pos[test_idx].min())
        train_pool = np.concatenate(folds[:f])
        # PURGE: keep only training samples whose label ended strictly before
        # (test_start - embargo) — no horizon overlap with the test period.
        keep = exit_pos[train_pool] < (test_start - embargo_bars)
        train_idx = train_pool[keep]
        if len(train_idx) == 0:
            continue
        yield train_idx, test_idx
