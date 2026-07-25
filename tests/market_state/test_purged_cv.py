"""Purged + embargoed walk-forward leakage guarantees."""
import numpy as np

from market_state import config as C
from market_state.purged_cv import purged_walk_forward_splits


def _splits(embargo):
    n = 120
    entry = np.arange(n)
    exit_ = entry + C.HORIZON_BARS
    return list(purged_walk_forward_splits(entry, exit_, n_splits=6, embargo_bars=embargo)), entry, exit_


def test_no_label_overlap_into_test():
    splits, entry, exit_ = _splits(C.EMBARGO_BARS)
    assert len(splits) >= 1
    for train_idx, test_idx in splits:
        test_start = int(entry[test_idx].min())
        # every training label must END before test_start - embargo
        assert exit_[train_idx].max() < test_start - C.EMBARGO_BARS


def test_expanding_and_fold0_never_tested():
    splits, entry, _ = _splits(0)
    # test folds are strictly later than their training data
    for train_idx, test_idx in splits:
        assert entry[train_idx].max() < entry[test_idx].min()
    # fold 0 (earliest) is never a test fold
    first_test_start = min(int(np.asarray(t).min()) for _, t in splits)
    assert first_test_start > 0


def test_embargo_removes_training_samples():
    fewer, _, _ = _splits(C.EMBARGO_BARS)
    more, _, _ = _splits(0)
    n_fewer = sum(len(tr) for tr, _ in fewer)
    n_more = sum(len(tr) for tr, _ in more)
    assert n_fewer <= n_more
