"""LiveRunner — the daily service loop. Consumes TradingView 1m webhooks through the existing
Ingestor, maintains the signal/entry timeframe buffers, and on every closed SIGNAL-TF (1H) bar:
  1. resolves any open trade against that bar (frozen fixed-2R exit),
  2. runs the frozen engine + execution v1 to build a TradeTicket,
  3. if the ticket is a TAKE, opens a tracked trade.
Everything else is ignored. No notifications, no orders, no sizing — advisory recording only.

State is reconstructable from the append-only raw-1m store: `warmup()` replays stored 1m to rebuild
buffers + tracker without re-writing logs, so a restart loses nothing.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from ict_live.feeds.ingestor import ACCEPTED, Ingestor
from ict_live.live import signal as SIG
from ict_live.live.tracker import DEFAULT_HORIZON, TradeTracker

RECENT_SIGNALS = 40                     # in-memory ring of recent tickets (report shows last 20)


class LiveRunner:
    def __init__(self, ingestor: Ingestor, *, signal_tf: str = "1H", entry_tf: str = "15m",
                 window: int = SIG.WINDOW, horizon: int = DEFAULT_HORIZON,
                 store_dir: Optional[str] = None):
        self.ingestor = ingestor
        self.signal_tf, self.entry_tf = signal_tf, entry_tf
        self.window, self.horizon = window, horizon
        self._entry_cap = window * 6 + 64
        self.buffers: dict[str, dict[str, list]] = {}
        self.trackers: dict[str, TradeTracker] = {}
        self.recent_signals: list[dict] = []
        self.last_signal_bar: dict[str, str] = {}
        self.last_signal: dict[str, dict] = {}          # latest ticket per symbol (for the live "current read")
        self.store_dir = Path(store_dir) if store_dir else None
        if self.store_dir:
            self.store_dir.mkdir(parents=True, exist_ok=True)
        # Boundary between historical seed bars and genuine live bars. Bars at or after it may OPEN
        # trades; bars before it only PRIME structure (buffers) so setups can be detected. None =
        # unset = every bar may trade (default; preserves prior behavior and all tests). Persisted so
        # a restart's warmup replay does not resurrect trades from backfilled history.
        self.live_since_ms: Optional[int] = self._load_live_since()

    # ---- seed/live boundary (operational; does not touch setup detection, filter, or exit) ----
    def _live_since_path(self) -> Optional[Path]:
        return (self.store_dir / "live_since.txt") if self.store_dir else None

    def _load_live_since(self) -> Optional[int]:
        p = self._live_since_path()
        try:
            return int(p.read_text().strip()) if p and p.exists() else None
        except (ValueError, OSError):
            return None

    def mark_live(self, ms: Optional[int] = None) -> int:
        """Set the seed→live boundary to `ms` (default: now). After this, only bars at/after the
        boundary open trades; earlier (warm-up) bars just prime structure. Persisted if store-backed."""
        self.live_since_ms = int(ms) if ms is not None else int(time.time() * 1000)
        p = self._live_since_path()
        if p:
            p.write_text(str(self.live_since_ms))
        return self.live_since_ms

    # ---- state accessors ----
    def _buf(self, symbol):
        return self.buffers.setdefault(symbol, {self.signal_tf: [], self.entry_tf: []})

    def tracker(self, symbol) -> TradeTracker:
        return self.trackers.setdefault(symbol, TradeTracker(horizon=self.horizon))

    def _append(self, name, obj):
        if self.store_dir:
            with (self.store_dir / name).open("a") as fh:
                fh.write(json.dumps(obj, default=str) + "\n")

    # ---- live entry point ----
    def feed(self, payload: dict, *, token: Optional[str] = None) -> dict:
        res = self.ingestor.ingest(payload, token=token)
        if res.status != ACCEPTED:
            return {"status": res.status, "reason": res.reason}
        out = self.on_closed_bars(res.symbol, res.closed_htf, warmup=False)
        return {"status": ACCEPTED, "symbol": res.symbol, **out}

    def on_closed_bars(self, symbol: str, closed: list, *, warmup: bool = False) -> dict:
        """Core: fold newly-closed TF bars into buffers; act on a closed signal-TF bar."""
        buf = self._buf(symbol)
        for b in closed:
            tf = b.timeframe
            if tf == self.signal_tf:
                buf[tf].append(b)
                if len(buf[tf]) > self.window + RECENT_SIGNALS:
                    del buf[tf][:-(self.window + RECENT_SIGNALS)]
            elif tf == self.entry_tf:
                buf[tf].append(b)
                if len(buf[tf]) > self._entry_cap:
                    del buf[tf][:-self._entry_cap]
        sig_bars = [b for b in closed if b.timeframe == self.signal_tf]
        if not sig_bars:
            return {"ticket": None, "closed_trades": []}
        sig_bar = sig_bars[-1]
        # Pre-live (historical seed) bars prime structure only — they never open/resolve trades,
        # emit signals, or write logs. This keeps warm-up backfill from surfacing phantom tickets.
        sig_ms = int(sig_bar.open_time.timestamp() * 1000)
        if self.live_since_ms is not None and sig_ms < self.live_since_ms:
            self.last_signal_bar[symbol] = sig_bar.open_time.isoformat()
            return {"ticket": None, "closed_trades": []}
        tr = self.tracker(symbol)
        closed_trades = tr.update(sig_bar)                          # resolve opens first
        ticket = SIG.build_ticket(buf[self.signal_tf], buf[self.entry_tf] or None,
                                  symbol=symbol, signal_tf=self.signal_tf, entry_tf=self.entry_tf,
                                  window=self.window)
        if ticket.action == "TAKE":
            tr.open_from_ticket(ticket)
        self.last_signal_bar[symbol] = sig_bar.open_time.isoformat()
        self.last_signal[symbol] = ticket.to_dict()
        self.recent_signals.append(ticket.to_dict())
        if len(self.recent_signals) > RECENT_SIGNALS:
            del self.recent_signals[:-RECENT_SIGNALS]
        if not warmup:
            self._append("signals.jsonl", ticket.to_dict())
            for ct in closed_trades:
                self._append("closed_trades.jsonl", ct.to_dict())
        return {"ticket": ticket, "closed_trades": closed_trades}

    # ---- restart recovery ----
    def warmup(self) -> dict:
        """Rebuild in-memory buffers + tracker state by replaying the append-only 1m store through
        the ingestor's bar builders. Writes NO logs (append-only history already on disk)."""
        replayed = {}
        for symbol in list(self.ingestor.store._bars.keys()):
            n = 0
            for b in self.ingestor.store.bars(symbol):
                closed = self.ingestor._builder(symbol).add_1m(b)
                if closed:
                    self.on_closed_bars(symbol, closed, warmup=True)
                n += 1
            replayed[symbol] = n
        return {"replayed_1m": replayed,
                "open_trades": {s: len(t.open) for s, t in self.trackers.items()},
                "last_signal_bar": dict(self.last_signal_bar)}

    def health(self) -> dict:
        return {"symbols": sorted(self._buf_symbols()),
                "last_signal_bar": dict(self.last_signal_bar),
                "open_trades": {s: len(t.open) for s, t in self.trackers.items()},
                "closed_trades": {s: len(t.closed) for s, t in self.trackers.items()},
                "signal_tf": self.signal_tf, "entry_tf": self.entry_tf,
                "live_since_ms": self.live_since_ms}

    def _buf_symbols(self):
        return set(self.buffers) | set(self.trackers)
