"""Append-only audit trail. Every ingestion decision (accept/reject/dup/conflict/out-of-order/
gap) and, later, every market-state / candidate / accepted / rejected setup is recorded here
with a monotonic sequence number and a UTC wall-clock stamp. Append-only: entries are never
edited or deleted — the trail is the audit record.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class EventTrail:
    def __init__(self, path: Optional[str | Path] = None):
        self._events: list[dict] = []
        self._seq = 0
        self._path = Path(path) if path else None
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, event_type: str, **fields) -> dict:
        self._seq += 1
        ev = {"seq": self._seq, "ts": datetime.now(timezone.utc).isoformat(),
              "type": event_type, **fields}
        self._events.append(ev)
        if self._path:
            with self._path.open("a") as fh:
                fh.write(json.dumps(ev) + "\n")
        return ev

    def events(self, event_type: Optional[str] = None) -> list[dict]:
        if event_type is None:
            return list(self._events)
        return [e for e in self._events if e["type"] == event_type]

    def tail(self, n: int = 50) -> list[dict]:
        return self._events[-n:]
