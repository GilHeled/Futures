"""
Purged + embargoed expanding walk-forward CV (López de Prado).

Triple-barrier labels overlap in time (a label spans entry→exit), so naive
CV leaks: a training sample whose label horizon reaches into the test period
shares information with it. Here, for each chronological test fold, the
training set keeps only samples whose label **ended before the test fold
starts, minus an embargo** — removing that leakage.
"""
from __future__ import annotations

import numpy as np


def purged_walk_forward_splits(entry_pos, exit_pos, n_splits: int, embargo_bars: int = 0):
    """entry_pos / exit_pos: positional bar indices per sample (same length).
    Yields (train_idx, test_idx) arrays of sample indices, expanding-window,
    purged + embargoed. Fold 0 is never a test fold (no prior training data)."""
    entry_pos = np.asarray(entry_pos)
    exit_pos = np.asarray(exit_pos)
    order = np.argsort(entry_pos, kind="stable")
    folds = np.array_split(order, n_splits)

    for f in range(1, n_splits):
        test_idx = folds[f]
        if len(test_idx) == 0:
            continue
        test_start = int(entry_pos[test_idx].min())
        train_pool = np.concatenate(folds[:f]) if f > 0 else np.array([], dtype=int)
        # PURGE: keep only training samples whose label ended strictly before
        # (test_start − embargo) — no horizon overlap with the test period.
        keep = exit_pos[train_pool] < (test_start - embargo_bars)
        train_idx = train_pool[keep]
        if len(train_idx) == 0:
            continue
        yield train_idx, test_idx
