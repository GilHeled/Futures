"""Domain-agnostic ranking engine: modular evaluators, lexicographic order, no discard, ties,
and complete pairwise (why #N lost to #N-1)."""
from ict_live.structure.ranking import FactorValue, rank


def _ev(name):
    return lambda it: FactorValue(name, it[name], f"{name}={it[name]}")


def test_lexicographic_order_no_discard():
    items = [{"x": 1, "y": 5, "id": "a"}, {"x": 2, "y": 0, "id": "b"}, {"x": 1, "y": 9, "id": "c"}]
    out = rank(items, [_ev("x"), _ev("y")])
    assert [o.item["id"] for o in out] == ["b", "c", "a"]     # x dominates, then y
    assert [o.rank for o in out] == [1, 2, 3]
    assert len(out) == len(items)                             # nothing discarded


def test_ranker_is_domain_agnostic():
    # evaluators carry all the semantics; the ranker only compares FactorValue.value
    out = rank([{"v": 3}, {"v": 7}], [lambda it: FactorValue("v", it["v"], "")])
    assert out[0].item["v"] == 7 and out[1].item["v"] == 3


def test_pairwise_explains_first_differing_factor():
    items = [{"x": 1, "y": 5}, {"x": 1, "y": 9}, {"x": 0, "y": 100}]
    out = rank(items, [_ev("x"), _ev("y")])
    # #1 is (1,9), #2 is (1,5) -> x equal, y decides; #3 is (0,..) -> x decides
    assert out[0].lost_to_prev == ""
    assert "x equal (1)" in out[1].lost_to_prev and "y: 5 < 9 ← decides" in out[1].lost_to_prev
    assert "x: 0 < 1 ← decides" in out[2].lost_to_prev


def test_ties_flagged_and_explained():
    out = rank([{"x": 1}, {"x": 1}], [_ev("x")])
    assert all(o.tied for o in out)
    assert "tie" in out[1].lost_to_prev.lower()
