"""CSV export/import — an ALTERNATIVE annotation interface (not a replacement for the web panel).

For large batches it is faster to review labels in a spreadsheet than click through the server UI.
The workflow is:

  1. export(queue) -> a flat CSV: one row per scene, all fields an expert needs to judge EXECUTION
     (engine direction + setup levels + object summaries + the five execution factors), and empty
     annotation columns. Deliberately NO historical graph dump — just the current thesis.
  2. Review / fill the annotation columns externally (spreadsheet).
  3. import_csv(completed) -> converts each filled row through the SAME validated schema
     (engine/annotations.make_annotation) and appends to the human-fidelity JSONL, identical in
     shape to what the web panel writes (provenance interface="csv").

Reproducibility: the engine_version that produced each scene is carried in the CSV, so a re-import
records which engine the human was judging. Run under the venv (pandas resample): .venv/bin/python.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Optional

from ict_live.engine import annotations as anno
from ict_live.engine import execution_quality as EQ
from ict_live.research import annotation_app as APP
from ict_live.research import versioning as VER

FIDELITY_PATH = "ict_live/research/datasets/human_fidelity.jsonl"

# context columns the engine fills (read-only for the annotator)
CONTEXT_COLS = [
    "scene_id", "symbol", "contract", "timeframe", "timestamp", "engine_version",
    "engine_direction", "candidate_id", "entry", "stop", "target", "RR",
    "manipulation_summary", "sweep_information", "mss_state", "displacement_summary",
    "fvg_summary", "dealing_range", "ce_location", "premium_discount_state",
    "pd_location", "ce_distance", "rr_realism", "confirmation", "fvg_location",
    "execution_reasons",
]
# annotation columns the human fills
ANNOTATION_COLS = [
    "decision", "engine",
    "wrong_manipulation", "wrong_sweep", "wrong_mss", "wrong_fvg",
    "wrong_dealing_range_context", "bad_location", "insufficient_confirmation",
    "execute_live", "reason_for_pass", "confidence", "note",
]
ALL_COLS = CONTEXT_COLS + ANNOTATION_COLS

# CSV error-tag column -> schema error_tag (name-identical, listed for clarity/ordering)
_TAG_COLS = ["wrong_manipulation", "wrong_sweep", "wrong_mss", "wrong_fvg",
             "wrong_dealing_range_context", "bad_location", "insufficient_confirmation"]


def _fmt(x, nd=2):
    return "" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def _summaries(ms) -> dict:
    """Compact, human-readable summaries of the CURRENT winning setup's dependency chain."""
    out = {k: "" for k in ("manipulation_summary", "sweep_information", "mss_state",
                            "displacement_summary", "fvg_summary", "dealing_range",
                            "ce_location", "premium_discount_state",
                            "entry", "stop", "target", "RR", "candidate_id")}
    win = ms.recommendation.setup
    if ms.ranges:
        dr = ms.ranges[0]
        out["dealing_range"] = f"{dr.source_tf} {_fmt(dr.low)}–{_fmt(dr.high)} ({dr.direction})"
        out["ce_location"] = _fmt(dr.ce)
        if win is not None:
            out["premium_discount_state"] = dr.zone_of(win.entry)
    if win is None:
        return out
    out.update(candidate_id=win.id, entry=_fmt(win.entry), stop=_fmt(win.stop),
               target=_fmt(win.target), RR=_fmt(win.rr))
    swp = EQ._by_dep(ms.ranked_sweeps, win.depends_on, "SWP")
    mss = EQ._by_dep(ms.ranked_mss, win.depends_on, "MSS")
    fvg = EQ._by_dep(ms.ranked_fvgs, win.depends_on, "FVG")
    disp = EQ._by_dep(ms.ranked_displacements, fvg.depends_on, "DISP") if fvg else None
    if swp is None and disp is not None:
        swp = EQ._by_dep(ms.ranked_sweeps, disp.depends_on, "SWP")
    if swp is not None:
        out["manipulation_summary"] = f"{swp.direction} sweep of {_fmt(swp.pool_price)}"
        out["sweep_information"] = (f"raid→{_fmt(swp.extreme)} close {_fmt(swp.close)} "
                                    f"(pool {_fmt(swp.pool_price)})")
    if mss is not None:
        out["mss_state"] = f"{mss.state} {mss.direction} @ {_fmt(mss.broken_price)}"
    if disp is not None:
        out["displacement_summary"] = (f"{disp.direction} net {_fmt(disp.net)} / {disp.span} bars"
                                       f"{' (exhausted)' if disp.exhausted else ''}")
    if fvg is not None:
        out["fvg_summary"] = (f"{fvg.direction} {fvg.status} {_fmt(fvg.bottom)}–{_fmt(fvg.top)} "
                              f"CE {_fmt(fvg.ce)} [{fvg.tf}]")
    return out


def _scene_row(scene: dict, engine_version: str) -> Optional[dict]:
    ms, _bar = APP.build_state(scene)
    if ms is None:
        return None
    s = _summaries(ms)
    ea = EQ.assess(ms)
    f = ea.factors or {}
    row = {c: "" for c in ALL_COLS}
    row.update(
        scene_id=scene["scene_id"], symbol=scene["symbol"], contract=scene["contract"],
        timeframe=scene["signal_tf"], timestamp=scene["time"], engine_version=engine_version,
        engine_direction=ms.recommendation.decision,
        candidate_id=s["candidate_id"] or scene["scene_id"],
        entry=s["entry"], stop=s["stop"], target=s["target"], RR=s["RR"],
        manipulation_summary=s["manipulation_summary"], sweep_information=s["sweep_information"],
        mss_state=s["mss_state"], displacement_summary=s["displacement_summary"],
        fvg_summary=s["fvg_summary"], dealing_range=s["dealing_range"],
        ce_location=s["ce_location"], premium_discount_state=s["premium_discount_state"],
        pd_location=_fmt(f.get("pd_location"), 4), ce_distance=_fmt(f.get("ce_distance"), 4),
        rr_realism=_fmt(f.get("rr_realism"), 4), confirmation=_fmt(f.get("confirmation"), 4),
        fvg_location=_fmt(f.get("fvg_location"), 4),
        execution_reasons=" | ".join(ea.reasons),
    )
    return row


def export(queue_path: str, csv_path: str) -> dict:
    """Export every scene in a queue JSONL to an annotation CSV (context filled, labels blank)."""
    v = VER.engine_version()
    ev = f"{v['commit']}:{v['engine_hash']}"
    scenes = [json.loads(l) for l in Path(queue_path).read_text().splitlines() if l.strip()]
    rows, skipped = [], []
    for sc in scenes:
        try:
            r = _scene_row(sc, ev)
        except SystemExit:
            r = None
        if r is None:
            skipped.append(sc["scene_id"])
        else:
            rows.append(r)
    Path(csv_path).parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=ALL_COLS)
        w.writeheader()
        w.writerows(rows)
    return {"queue": queue_path, "csv": csv_path, "exported": len(rows),
            "skipped": skipped, "engine_version": ev}


# ---- import -------------------------------------------------------------------------------------

_TRUE = {"1", "y", "yes", "true", "x", "t"}
_FALSE = {"0", "n", "no", "false", "f", ""}
_DIR = {"LONG": "LONG", "SHORT": "SHORT", "NO-TRADE": "NO_TRADE", "NO_TRADE": "NO_TRADE",
        "NT": "NO_TRADE"}


def _truthy(v: str) -> bool:
    return str(v).strip().lower() in _TRUE


def _confidence(v: str) -> Optional[float]:
    v = str(v).strip()
    if not v:
        return None
    x = float(v)
    return round(x / 5.0, 4) if x > 1.0 else x       # accept a 1–5 scale or an already-0–1 value


def _row_to_annotation(row: dict, annotator: str) -> Optional[dict]:
    """Convert one filled CSV row into a validated fidelity annotation. Blank decision => skip."""
    dec = str(row.get("decision", "")).strip().upper()
    eng = str(row.get("engine", "")).strip().lower()
    decisions = []
    if eng in ("accept", "a"):
        decisions.append("ACCEPT")
    elif eng in ("reject", "r"):
        decisions.append("REJECT")
    if dec:
        if dec not in _DIR:
            raise ValueError(f"row {row.get('scene_id')}: bad decision {dec!r}")
        decisions.append(_DIR[dec])
    if not decisions:                                # nothing filled in — an un-reviewed row
        return None
    error_tags = [c for c in _TAG_COLS if _truthy(row.get(c, ""))]
    reason_for_pass = [r.strip() for r in str(row.get("reason_for_pass", "")).replace(";", ",").split(",")
                       if r.strip()]
    ex = str(row.get("execute_live", "")).strip().lower()
    would_execute = True if ex in _TRUE else (False if ex in ({"no", "n", "0", "false"}) else None)
    a = anno.make_annotation(
        candidate_id=(row.get("candidate_id") or row.get("scene_id")),
        decisions=decisions, annotator=annotator, error_tags=error_tags,
        note=str(row.get("note", "")).strip(), confidence=_confidence(row.get("confidence", "")),
        candidate_time=row.get("timestamp") or None, would_execute=would_execute,
        location_quality=None, reason_for_pass=reason_for_pass)
    a.update({"scene_id": row.get("scene_id"), "symbol": row.get("symbol"),
              "contract": row.get("contract"), "signal_tf": row.get("timeframe"),
              "engine_version": row.get("engine_version"),
              "provenance": {"round": row.get("_round", "csv_import"),
                             "interface": "csv", "blinded": False}})
    return a


def _existing_keys(out_path: str) -> set:
    """Set of (round, scene_id) already present in the fidelity file — for idempotent import."""
    keys = set()
    for a in anno.load_annotations(out_path):
        keys.add((a.get("provenance", {}).get("round"), a.get("scene_id")))
    return keys


def import_csv(csv_path: str, *, out_path: str = FIDELITY_PATH, annotator: str = "gil",
               round_name: str = "csv_import", dry_run: bool = False,
               overwrite: bool = False) -> dict:
    """Read a completed CSV; append each reviewed row to the fidelity JSONL. IDEMPOTENT: a
    (round_name, scene_id) already present is SKIPPED unless overwrite=True. Reports counts+errors.

    overwrite=True re-appends the row (the file is append-only + last-wins per scene, so a re-import
    supersedes the prior label rather than editing it in place)."""
    with open(csv_path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    existing = _existing_keys(out_path)
    written, skipped, dup, errors = [], [], [], []
    for row in rows:
        row["_round"] = round_name
        try:
            a = _row_to_annotation(row, annotator)
        except Exception as e:
            errors.append({"scene_id": row.get("scene_id"), "error": str(e)})
            continue
        if a is None:
            skipped.append(row.get("scene_id"))
            continue
        key = (round_name, a["scene_id"])
        if key in existing and not overwrite:
            dup.append(a["scene_id"])
            continue
        if not dry_run:
            anno.append_annotation(out_path, a)
        existing.add(key)
        written.append(a["scene_id"])
    return {"csv": csv_path, "written": len(written), "skipped_unreviewed": len(skipped),
            "skipped_duplicate": len(dup), "duplicates": dup, "errors": errors,
            "out": out_path, "dry_run": dry_run, "overwrite": overwrite}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="CSV annotation export/import")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export"); e.add_argument("--queue", required=True); e.add_argument("--csv", required=True)
    i = sub.add_parser("import"); i.add_argument("--csv", required=True)
    i.add_argument("--out", default=FIDELITY_PATH); i.add_argument("--annotator", default="gil")
    i.add_argument("--round", default="csv_import"); i.add_argument("--dry-run", action="store_true")
    i.add_argument("--overwrite", action="store_true",
                   help="re-import rows whose (round, scene_id) already exist (default: skip them)")
    ns = ap.parse_args()
    if ns.cmd == "export":
        print(json.dumps(export(ns.queue, ns.csv), indent=1))
    else:
        print(json.dumps(import_csv(ns.csv, out_path=ns.out, annotator=ns.annotator,
                                    round_name=ns.round, dry_run=ns.dry_run,
                                    overwrite=ns.overwrite), indent=1))
