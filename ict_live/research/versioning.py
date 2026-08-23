"""Recommendation versioning + regression tool.

Snapshot the deterministic recommendation (and the key ranked objects) for a fixed set of scenes,
tagged with an engine version. When a code change later alters a historical recommendation, diff
the snapshots to see exactly WHAT changed — decision, chosen setup, per-layer top candidate, object
counts — so behaviour changes are caught, explained, and (with the annotation queue) prioritised
for re-annotation. Pandas-free (engine + stdlib).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ict_live.engine.pipeline import MarketState

_ENGINE_DIRS = ["engine", "structure"]     # under ict_live/ — the deterministic engine surface


def engine_version() -> dict:
    """Version = git short commit + a content hash of the engine source (so uncommitted edits count)."""
    root = Path(__file__).resolve().parents[1]      # ict_live/
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root,
                                capture_output=True, text=True).stdout.strip() or "nogit"
    except Exception:
        commit = "nogit"
    h = hashlib.sha256()
    for d in _ENGINE_DIRS:
        for f in sorted((root / d).rglob("*.py")):
            if "__pycache__" in f.parts:
                continue
            h.update(f.read_bytes())
    return {"commit": commit, "engine_hash": h.hexdigest()[:12]}


def fingerprint(ms: MarketState) -> dict:
    """Compact, comparable fingerprint of a recommendation + its key objects."""
    rec = ms.recommendation
    def top(L): return L[0].item.id if L else None
    return {"decision": rec.decision, "setup_id": rec.setup.id if rec.setup else None,
            "top_sweep": top(ms.ranked_sweeps), "top_mss": top(ms.ranked_mss),
            "top_fvg": top(ms.ranked_fvgs), "top_setup": top(ms.ranked_setups),
            "n_sweeps": len(ms.ranked_sweeps), "n_mss": len(ms.ranked_mss),
            "n_fvgs": len(ms.ranked_fvgs), "n_setups": len(ms.ranked_setups),
            "n_actionable": sum(1 for r in ms.ranked_setups if r.item.actionable),
            "entry": rec.setup.entry if rec.setup else None,
            "stop": rec.setup.stop if rec.setup else None,
            "target": rec.setup.target if rec.setup else None}


def snapshot(scene_states: list[tuple[str, MarketState]], path: str | None = None) -> dict:
    """scene_states: [(scene_id, MarketState)]. Returns {version, generated, records:{id:fingerprint}}."""
    snap = {"version": engine_version(), "generated": datetime.now(timezone.utc).isoformat(),
            "records": {sid: fingerprint(ms) for sid, ms in scene_states}}
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(snap, indent=1, default=str))
    return snap


def load(path: str) -> dict:
    return json.loads(Path(path).read_text())


def diff(old: dict, new: dict) -> dict:
    """What changed between two snapshots. Reports per-scene field deltas and a 'why' summary."""
    o, n = old["records"], new["records"]
    changes = []
    for sid in sorted(set(o) & set(n)):
        deltas = {k: [o[sid].get(k), n[sid].get(k)] for k in n[sid]
                  if o[sid].get(k) != n[sid].get(k)}
        if deltas:
            why = []
            if "decision" in deltas:
                why.append(f"decision {deltas['decision'][0]}→{deltas['decision'][1]}")
            for layer in ("top_setup", "top_fvg", "top_mss", "top_sweep"):
                if layer in deltas:
                    why.append(f"{layer} {deltas[layer][0]}→{deltas[layer][1]}")
            for cnt in ("n_setups", "n_fvgs", "n_mss", "n_sweeps", "n_actionable"):
                if cnt in deltas:
                    why.append(f"{cnt} {deltas[cnt][0]}→{deltas[cnt][1]}")
            changes.append({"scene_id": sid, "deltas": deltas, "why": "; ".join(why)})
    return {"old_version": old["version"], "new_version": new["version"],
            "n_scenes": len(set(o) & set(n)), "n_changed": len(changes),
            "added": sorted(set(n) - set(o)), "removed": sorted(set(o) - set(n)),
            "changed_scene_ids": [c["scene_id"] for c in changes], "changes": changes}
