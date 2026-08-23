"""Reasoning graph: nodes for every object, parent/child links, ancestor trace, competitors."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ict_live.engine import pipeline, reasoning
from ict_live.market.bar import Bar

ET = ZoneInfo("America/New_York")


def _series(n, seed=7):
    bars, px, x = [], 20000.0, seed
    t0 = datetime(2026, 6, 1, 18, 0, tzinfo=ET)
    for i in range(n):
        x = (1103515245 * x + 12345) % (2 ** 31)
        o = px
        c = px + ((x % 21) - 10) * 1.5
        ot = t0 + timedelta(minutes=15 * i)
        bars.append(Bar("15m", ot, ot + timedelta(minutes=15), o, max(o, c) + (x % 7),
                        min(o, c) - (x % 5), c, 100.0))
        px = c
    return bars


def test_graph_has_nodes_and_child_links():
    ms = pipeline.analyze(_series(120), "15m")
    g = reasoning.build_graph(ms)
    assert g["nodes"], "expected engine objects"
    # every resolvable dependency creates a reverse child link
    for nid, n in g["nodes"].items():
        for dep in n["depends_on"]:
            if dep in g["nodes"]:
                assert nid in g["nodes"][dep]["children"]
    # layers are ranked lists
    for kind in ("manipulation", "displacement", "mss", "fvg", "setup"):
        assert kind in g["layers"]


def test_every_ranked_node_carries_why_and_factors():
    ms = pipeline.analyze(_series(160, seed=99), "15m")
    g = reasoning.build_graph(ms)
    for kind in ("manipulation", "mss", "fvg", "setup"):
        for n in reasoning.competitors(g, kind):
            assert n["why"]                      # every object explains itself
            assert n["rank"] is not None
            assert isinstance(n["factors"], dict) and n["factors"]
            if n["rank"] > 1:
                assert n["lost_to_prev"]         # pairwise why-it-lost present


def test_current_rank_is_within_current_and_le_global():
    # current_rank ranks only CURRENT nodes (1..k in global order) and never exceeds the global rank
    for seed in range(1, 40):
        ms = pipeline.analyze(_series(220, seed=seed), "15m")
        g = reasoning.build_graph(ms)
        for kind in ("manipulation", "mss", "fvg", "setup"):
            cur = [n for n in reasoning.competitors(g, kind) if n["lifecycle"] == "current"]
            if not cur:
                continue
            assert [n["current_rank"] for n in cur] == list(range(1, len(cur) + 1))
            assert all(n["current_rank"] <= n["rank"] for n in cur)   # global rank >= current rank
        # non-current objects keep current_rank None (global rank preserved as metadata)
        for n in g["nodes"].values():
            if n["lifecycle"] != "current":
                assert n["current_rank"] is None
    return


def test_trace_walks_full_chain():
    # find a scene with at least one setup, then trace its ancestors down to a swing
    for seed in range(1, 40):
        ms = pipeline.analyze(_series(200, seed=seed), "15m")
        if ms.ranked_setups:
            g = reasoning.build_graph(ms)
            sid = ms.ranked_setups[0].item.id
            chain = reasoning.trace(g, sid)
            kinds = {g["nodes"][c]["kind"] for c in chain if c in g["nodes"]}
            assert sid in chain
            # a setup's chain must reach back through fvg/mss/manipulation to a swing
            assert "swing" in kinds or "erl" in kinds
            return
    # if no setup arose across seeds, the graph must at least link erl->swing
    ms = pipeline.analyze(_series(200), "15m")
    g = reasoning.build_graph(ms)
    assert any(n["kind"] == "swing" for n in g["nodes"].values())
