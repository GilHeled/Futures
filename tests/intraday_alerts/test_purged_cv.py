import numpy as np

from intraday_alerts.purged_cv import purged_walk_forward_splits


def test_purge_removes_horizon_overlap_and_embargo():
    # 12 samples, entry_pos = 0..11, each label spans 2 bars
    entry_pos = np.arange(12)
    exit_pos = entry_pos + 2
    splits = list(purged_walk_forward_splits(entry_pos, exit_pos, n_splits=4, embargo_bars=1))
    assert len(splits) >= 1
    for train_idx, test_idx in splits:
        test_start = int(entry_pos[test_idx].min())
        # every training label must END before test_start - embargo (no leakage)
        assert np.all(exit_pos[train_idx] < test_start - 1)
        # train strictly precedes test
        assert entry_pos[train_idx].max() < entry_pos[test_idx].min()


def test_fold_zero_never_test_and_expanding():
    entry_pos = np.arange(20)
    exit_pos = entry_pos + 1
    splits = list(purged_walk_forward_splits(entry_pos, exit_pos, n_splits=5, embargo_bars=0))
    # training set grows across successive folds (expanding window)
    sizes = [len(tr) for tr, _ in splits]
    assert sizes == sorted(sizes)
    # earliest test fold starts after the first chunk (fold 0 is training-only)
    first_test_start = int(entry_pos[splits[0][1]].min())
    assert first_test_start >= 4
