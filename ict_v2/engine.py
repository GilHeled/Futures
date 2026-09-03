"""ICT v2 — cascade organized by the RESPONSIBILITY of each timeframe (user redesign 2026-08-28):

    4H  = STRATEGIC context   (bias, dealing range, P/D, IRL/ERL, pools, HTF PD-arrays, fib, draws)
    1H  = INTRADAY context    (confirm bias | establish intraday direction; more contextual PD-arrays)
          → SCENARIO LAYER: maintain the top 2-3 stable market theses from ALL liquidity objectives
    15m = EXECUTION setup     (monitor the scenarios: is one retracing into its entry zone?)
    1m  = EXECUTION trigger   (entry-role PD array retraced into → that scenario is the trade)

The context stages complete on their structural read — NEVER on whether an FVG happened to form on
that timeframe (that concept is gone from the context stages). FVG is just one liquidity objective whose
role is context-assigned. Scenarios are (re)built only on a context close and persist structurally, so
the active set is stable and changes only on a meaningful structural event, not on price noise.

Each `on_*_close` recomputes only its layer; higher layers stay fixed until their own close. Reuses the
stage helpers in `pipeline` (which reuse the frozen v1 engine read-only). v1 is never modified.
"""
from __future__ import annotations

from datetime import timezone
from zoneinfo import ZoneInfo

from ict_live.engine import pipeline as v1
from ict_live.market.calendar import Calendar        # frozen CME session-day (18:00→17:00 ET trade date)

_ET = ZoneInfo("America/New_York")


def _et_iso(dt) -> str:
    """A bar's close_time as an ET-clock ISO string — so scenario-timeline stamps read in ET (the chart's
    clock), not the raw UTC the bars carry. Matches live.py's _et_iso for the price/last-tick times."""
    if dt is None:
        return ""
    if getattr(dt, "tzinfo", None) is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_ET).isoformat()
from ict_v2 import liquidity as LQ
from ict_v2 import pdarrays as PDA
from ict_v2 import pipeline as P
from ict_v2 import scenarios as SC

_SCENARIO_TARGET = 2       # user: keep the top 2 theses …
_SCENARIO_MAX = 3          # … at most 3


class MTFEngine:
    def __init__(self, context_tf: str = "4H", setup_tf: str = "1H", confirm_tf: str = "15m",
                 trigger_tf: str = "1m", refine_tf: str | None = None, min_stop: float | None = None,
                 anchor_tf: str | None = None, entry_models=None, point_value: float | None = None,
                 price_dp: int = 2):
        # ≥15-minute liquidity floor (Lesson 6/8): the CONTEXT/execution-setup TFs must be ≥15m; the 1m
        # trigger (and refine) may be finer — they only trigger, they do not mark liquidity.
        P.assert_liquidity_floor(context_tf, setup_tf, confirm_tf)
        self.context_tf, self.setup_tf = context_tf, setup_tf
        self.confirm_tf, self.trigger_tf = confirm_tf, trigger_tf
        self.refine_tf, self.min_stop, self.anchor_tf = refine_tf, min_stop, anchor_tf
        self.entry_models = entry_models
        # --- responsibility-based state ---
        self.strategic = None          # 4H StrategicContext (HTFContext) — fixed until next 4H close
        self.intraday = None           # 1H IntradayContext  (HTFContext) — fixed until next 1H close
        self.price_dp = price_dp                 # price decimals (2 index scale, 5 FX) for keys/display
        self.book = SC.ScenarioBook(target=_SCENARIO_TARGET, maxn=_SCENARIO_MAX, point_value=point_value,
                                    price_dp=price_dp)
        self._cal = Calendar()                     # CME session-day for the no-overnight-holds rule
        self.objectives: list = []     # latest full liquidity-objective inventory (for the dashboard)
        self.exec_tf = None            # the TF the current execution states were monitored on
        self.last_price = None
        self._ctx_bars = None          # last 4H bars (NWOG window)
        self._confirm_bars = None      # last 15m bars (ORG window + execution setup)
        self._trigger_bars = None      # last 1m bars (execution trigger)

    # ---- context stages (produce context; NEVER require an FVG) --------------------------------
    def on_context_close(self, bars, anchor_bars=None):
        """4H strategic context. Optional Daily/Weekly anchor vetoes a counter-trend 4H bias to neutral."""
        self._ctx_bars = bars
        anchor = (P.htf_bias_of(anchor_bars, self.anchor_tf) if (self.anchor_tf and anchor_bars) else "")
        self.strategic = P.htf_context(bars, self.context_tf, anchor=anchor,
                                       anchor_tf=(self.anchor_tf if anchor else ""))
        self._rebuild_scenarios(self._ctx_key(bars))
        return self.strategic

    def on_setup_close(self, bars, refine_bars=None):
        """1H intraday context — REUSES the context builder (bias/range/draws/trend). It is context, not
        a setup: it never 'waits for a 1H FVG'. Its close refreshes the scenario set."""
        if self.strategic is None:
            return None
        self.intraday = P.htf_context(bars, self.setup_tf)
        self._rebuild_scenarios(self._ctx_key(bars))
        return self.intraday

    # ---- execution stages (monitor the scenarios; the ONLY place an entry lives) ---------------
    def on_confirm_close(self, bars, refine_bars=None):
        """15m execution setup — monitor whether any active scenario is retracing into its entry zone."""
        self._confirm_bars = bars
        self._monitor(bars, self.confirm_tf)
        return self.book.active

    def on_trigger_close(self, bars):
        """1m execution trigger — the precise entry: an entry-role PD array retraced into its zone."""
        self._trigger_bars = bars
        if bars:
            self.last_price = bars[-1].close
        self._monitor(bars, self.trigger_tf)
        return self.book.active

    # ---- scenario maintenance -----------------------------------------------------------------
    @staticmethod
    def _ctx_key(bars) -> str:
        return _et_iso(bars[-1].close_time) if bars else ""

    def _gaps(self):
        """NWOG (from the 4H buffer) + ORG (from the 15m buffer) as liquidity-objective source dicts."""
        gaps = []
        for g in (PDA.nwogs(self._ctx_bars or [])):
            g = dict(g); g["_kind"] = "nwog"; g["tf"] = self.context_tf; gaps.append(g)
        org = PDA.org(self._confirm_bars or [])
        if org:
            org = dict(org); org["_kind"] = "org"; org["tf"] = self.confirm_tf; gaps.append(org)
        return gaps

    def _rebuild_scenarios(self, context_key: str):
        """Collect EVERY liquidity objective from the 4H + 1H context, build ranked scenario proposals,
        and reconcile the stable active set. Called only on a context (4H/1H) close."""
        if self.strategic is None:
            return
        objs = LQ.collect_objectives(self.strategic, direction=(self.strategic.bias or None),
                                     gaps=self._gaps())
        if self.intraday is not None:
            objs += LQ.collect_objectives(self.intraday, direction=(self.intraday.bias or None))
        # NEARER liquidity: also collect the execution-TF (15m) intraday pools/FVGs so the scenario can
        # target a CLOSER draw than the coarse 4H/1H pools (Lesson 10: price seeks the NEXT draw). The
        # 15m is ≥ the liquidity floor, so its pools are valid draws for an intraday target.
        if self._confirm_bars:
            try:
                ctx15 = P.htf_context(self._confirm_bars, self.confirm_tf)
                objs += LQ.collect_objectives(ctx15, direction=(self.strategic.bias or None))
            except Exception:
                pass
        self.objectives = objs
        proposals = SC.build_scenarios(self.strategic, self.intraday or self.strategic, objs,
                                       price=self.last_price, price_dp=self.price_dp)
        rk = SC._range_key(self.strategic.dealing_range, self.price_dp)
        self.book.observe(proposals, context_key=context_key, cur_range_key=rk)
        # refresh execution state on the finest bars we have, so a rebuild doesn't blank the states
        self._monitor(self._trigger_bars or self._confirm_bars,
                      self.trigger_tf if self._trigger_bars else self.confirm_tf)

    def _monitor(self, bars, tf):
        """Update each active scenario's execution state from entry candidates on the execution TF. An
        OPEN trade resolves only on stop/target and is checked against the finest (trigger-TF) bar, so a
        trigger never falls back to watching. Membership is untouched."""
        if not self.book.active:
            return
        if not bars or self.strategic is None:
            self.book.monitor(lambda s: None, bar=None)
            return
        ms = v1.analyze(bars, tf, min_stop=self.min_stop)
        cands = P.generate_candidates(ms, self.strategic, tf=tf, min_stop=self.min_stop, bars=bars,
                                      entry_models=self.entry_models)   # honour the configured model set
        price = bars[-1].close
        # the position lifecycle (open + stop/target/EOD resolution) runs ONLY on the trigger TF (1m,
        # finest, no look-ahead); the coarser 15m pass only advances watching/retracing/armed for display.
        bar = bars[-1] if tf == self.trigger_tf else None
        day = None
        if bar is not None:
            ct = bar.close_time
            if getattr(ct, "tzinfo", None) is None:
                ct = ct.replace(tzinfo=timezone.utc)
            try:
                day = self._cal.session_day(ct)          # CME trade date (None during the maintenance halt)
            except Exception:
                day = None
        ts = _et_iso(bars[-1].close_time) if bars else None      # ET cursor time → scenario-timeline stamps
        self.book.monitor(lambda s: P.execution_for_scenario(s, cands, price, objectives=self.objectives,
                                                             ms=ms, price_dp=self.price_dp),
                          bar=bar, day=day, ts=ts)
        self.exec_tf = tf

    # ---- accessors ----------------------------------------------------------------------------
    def state(self):
        return self

    def describe(self) -> str:
        sc = self.book.active
        head = (f"4H {getattr(self.strategic, 'bias', '?')} · 1H {getattr(self.intraday, 'bias', '?')} · "
                f"{len(sc)} scenario(s)")
        lines = [head]
        for s in sc:
            ex = s.execution or {}
            lines.append(f"  #{s.rank} {s.direction} → {s.draw.label} {round(s.draw.price, 2)} "
                         f"[{s.state}] {ex.get('why', '')}")
        return "\n".join(lines)


def _demo() -> None:
    base = P._base_1m(20000, 7)
    h4, h1, m15 = P.resample(base, 240, "4H"), P.resample(base, 60, "1H"), P.resample(base, 15, "15m")
    eng = MTFEngine("4H", "1H", "15m", "1m")
    eng.on_trigger_close(base[-400:])          # prime price
    eng.on_context_close(h4)
    eng.on_setup_close(h1)
    eng.on_confirm_close(m15)
    eng.on_trigger_close(base[-400:])
    print("ICT v2 — responsibility cascade + scenario layer\n")
    print(eng.describe())


if __name__ == "__main__":
    _demo()
