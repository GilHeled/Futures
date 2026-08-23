"""CSV annotation interface: column schema, import conversion through the validated schema, and an
export smoke test (monkeypatched scene builder — no 5m data load)."""
import csv
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from ict_live.engine import annotations as anno
from ict_live.engine import pipeline
from ict_live.market.bar import Bar
from ict_live.research import annotation_csv as CSV

ET = ZoneInfo("America/New_York")


def test_column_schema_consistency():
    assert CSV.ALL_COLS == CSV.CONTEXT_COLS + CSV.ANNOTATION_COLS
    assert len(set(CSV.ALL_COLS)) == len(CSV.ALL_COLS)          # no dupes
    assert set(CSV._TAG_COLS) <= anno.ERROR_TAGS                # tag columns are real error tags
    for col in ("pd_location", "ce_distance", "rr_realism", "confirmation", "fvg_location"):
        assert col in CSV.CONTEXT_COLS                          # every execution factor is exported


def _write_csv(path, rows):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV.ALL_COLS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in CSV.ALL_COLS})


def test_import_converts_reviewed_rows(tmp_path):
    out = tmp_path / "fidelity.jsonl"
    rows = [
        # a reviewed LONG the human accepts, confidence on a 1–5 scale, no pass reason
        {"scene_id": "s1", "candidate_id": "SET1", "symbol": "MES", "contract": "MESZ23",
         "timeframe": "1H", "timestamp": "2023-09-01T10:00:00-04:00",
         "engine_version": "abc:def", "decision": "LONG", "engine": "accept",
         "execute_live": "yes", "confidence": "4"},
        # a reviewed NO-TRADE reject with an execution pass reason + a wrong_fvg tag, execute no
        {"scene_id": "s2", "symbol": "MNQ", "contract": "MNQZ23", "timeframe": "1H",
         "timestamp": "2023-09-02T11:00:00-04:00", "decision": "NO-TRADE", "engine": "reject",
         "wrong_fvg": "1", "reason_for_pass": "premium_discount, rr_misleading",
         "execute_live": "no", "confidence": "0.6"},
        # an un-reviewed row (blank decision + engine) -> skipped, not an error
        {"scene_id": "s3", "symbol": "MES", "contract": "MESZ23", "timeframe": "1H",
         "timestamp": "2023-09-03T12:00:00-04:00"},
    ]
    p = tmp_path / "batch.csv"
    _write_csv(p, rows)

    dry = CSV.import_csv(str(p), out_path=str(out), round_name="csvtest", dry_run=True)
    assert dry["written"] == 2 and dry["skipped_unreviewed"] == 1 and not dry["errors"]
    assert not out.exists()                                    # dry run wrote nothing

    rep = CSV.import_csv(str(p), out_path=str(out), round_name="csvtest")
    assert rep["written"] == 2 and rep["skipped_unreviewed"] == 1 and not rep["errors"]
    recs = {r["scene_id"]: r for r in anno.load_annotations(str(out))}

    a1 = recs["s1"]
    assert a1["decisions"] == ["ACCEPT", "LONG"] and a1["candidate_id"] == "SET1"
    assert a1["would_execute"] is True and a1["confidence"] == 0.8    # 4/5 rescaled
    assert a1["provenance"] == {"round": "csvtest", "interface": "csv", "blinded": False}

    a2 = recs["s2"]
    assert a2["decisions"] == ["REJECT", "NO_TRADE"] and a2["error_tags"] == ["wrong_fvg"]
    assert a2["reason_for_pass"] == ["premium_discount", "rr_misleading"]
    assert a2["would_execute"] is False and a2["confidence"] == 0.6
    assert a2["candidate_id"] == "s2"                          # falls back to scene_id


def test_import_is_idempotent(tmp_path):
    out = tmp_path / "f.jsonl"
    row = {"scene_id": "s1", "candidate_id": "SET1", "symbol": "MES", "contract": "MESZ23",
           "timeframe": "1H", "timestamp": "t", "decision": "LONG", "engine": "accept"}
    p = tmp_path / "b.csv"
    _write_csv(p, [row])
    r1 = CSV.import_csv(str(p), out_path=str(out), round_name="R")
    assert r1["written"] == 1 and r1["skipped_duplicate"] == 0
    # second import of the same (round, scene_id) is skipped, not appended
    r2 = CSV.import_csv(str(p), out_path=str(out), round_name="R")
    assert r2["written"] == 0 and r2["skipped_duplicate"] == 1 and r2["duplicates"] == ["s1"]
    assert len(anno.load_annotations(str(out))) == 1          # no duplicate line written
    # a DIFFERENT round is allowed (distinct key)
    r3 = CSV.import_csv(str(p), out_path=str(out), round_name="R2")
    assert r3["written"] == 1
    # explicit overwrite re-appends (append-only + last-wins supersedes)
    r4 = CSV.import_csv(str(p), out_path=str(out), round_name="R", overwrite=True)
    assert r4["written"] == 1 and r4["skipped_duplicate"] == 0
    assert len(anno.load_annotations(str(out))) == 3


def test_import_reports_bad_rows_without_raising(tmp_path):
    out = tmp_path / "f.jsonl"
    _write_csv(tmp_path / "bad.csv", [
        {"scene_id": "b1", "symbol": "MES", "contract": "MESZ23", "timeframe": "1H",
         "timestamp": "t", "decision": "MAYBE", "engine": "accept"}])
    rep = CSV.import_csv(str(tmp_path / "bad.csv"), out_path=str(out), dry_run=True)
    assert rep["written"] == 0 and len(rep["errors"]) == 1
    assert rep["errors"][0]["scene_id"] == "b1"


def _series(n, seed):
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


def test_export_smoke_no_dataload(tmp_path, monkeypatch):
    # find a synthetic scene the engine turns into a live setup, monkeypatch build_state to serve it
    live_ms = None
    for seed in range(1, 200):
        ms = pipeline.analyze(_series(240, seed=seed), "15m")
        if ms.recommendation.decision in ("LONG", "SHORT"):
            live_ms = ms
            break
    assert live_ms is not None
    monkeypatch.setattr(CSV.APP, "build_state", lambda scene: (live_ms, None))
    monkeypatch.setattr(CSV.VER, "engine_version", lambda: {"commit": "test", "engine_hash": "0" * 12})

    queue = tmp_path / "q.jsonl"
    queue.write_text('{"scene_id":"z1","symbol":"MNQ","contract":"MNQM26",'
                     '"signal_tf":"15m","time":"2026-06-05T10:00:00-04:00"}\n')
    csv_path = tmp_path / "out.csv"
    rep = CSV.export(str(queue), str(csv_path))
    assert rep["exported"] == 1 and rep["engine_version"] == "test:000000000000"

    with open(csv_path, newline="") as fh:
        r = list(csv.DictReader(fh))[0]
    assert list(r.keys()) == CSV.ALL_COLS                      # header order == schema
    assert r["engine_direction"] == live_ms.recommendation.decision
    assert r["pd_location"] != "" and r["RR"] != ""            # factors + levels populated
    assert all(r[c] == "" for c in CSV.ANNOTATION_COLS)        # labels left blank for the human
