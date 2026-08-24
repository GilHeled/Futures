"""User trade-control OVERLAY — records what the user decided about each advised trade
(placed / skipped / cancelled / closed) WITHOUT touching the engine's simulated tracker.

The system stays advisory: this only reflects the user's decisions and drives what the dashboard
shows as actionable. The simulation keeps running underneath for the unbiased engine ledger — a
control action never opens/closes/cancels a simulated trade. Persisted as JSON in the data dir so
decisions survive a restart. (Recording ACTUAL fills/outcomes is a separate, later feature.)
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

STATUSES = {"placed", "skipped", "cancelled", "closed"}


class TradeControl:
    def __init__(self, path: Optional[str | Path] = None):
        self.path = Path(path) if path else None
        self.status: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if self.path and self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                if isinstance(data, dict):
                    self.status = data
            except (ValueError, OSError):
                self.status = {}

    def _save(self) -> None:
        if self.path:
            try:
                self.path.write_text(json.dumps(self.status))
            except OSError:
                pass

    def set(self, ticket_id: str, status: str) -> dict:
        """Record the user's decision for a ticket. Raises ValueError on an unknown status."""
        status = str(status).lower()
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}; must be one of {sorted(STATUSES)}")
        if not ticket_id:
            raise ValueError("ticket_id required")
        rec = {"status": status, "ts": int(time.time() * 1000)}
        self.status[ticket_id] = rec
        self._save()
        return rec

    def all(self) -> dict:
        return dict(self.status)
