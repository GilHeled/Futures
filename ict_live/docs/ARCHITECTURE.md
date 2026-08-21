# ARCHITECTURE — ict_live (Phase 1: live analysis / recommendation only)

> **Status: DRAFT for approval.** No engine code is written until the unresolved
> methodology choices (`UNRESOLVED_DECISIONS.md`) are approved. Phase 1 produces
> LONG / SHORT / NO-TRADE recommendations with a full audit trail; it does **not**
> execute trades and does **not** touch any locked OOS/hold-out dataset.

## 0. Design principles (carried from the project's hard-won lessons)
- **Fidelity before performance.** Make the engine internally correct and faithful to the
  course first; freeze the spec; only then backtest. No P&L-driven parameter tuning during
  implementation. (This is why every discretionary knob is labelled in
  `UNRESOLVED_DECISIONS.md` and none is invented silently.)
- **Causality is a first-class invariant.** The project has already been burned by a
  look-ahead artifact (the overnight line). Every value used at time *t* must be derivable
  from bars closed at or before *t*. Swings/FVGs/session extremes are *forming* until their
  confirmation bar closes and are labelled as such.
- **The engine is the strategy brain; the feed is dumb.** TradingView/Pine only transports
  completed 1-minute OHLCV bars. All timeframe construction and all methodology live in
  one testable Python engine, so the feed can later be swapped (broker API) without
  touching the strategy.
- **Multiple candidates, ranked — never "first sweep wins."** The blind rounds showed that
  selecting the *actual* manipulation is the hardest part. The engine maintains a set of
  live liquidity/sweep candidates and ranks them by an explicit, auditable rule.

## 1. What already exists and is REUSED (do not rebuild)
| Need | Reuse from | Notes |
|---|---|---|
| Bar/candle model | `mnq_system/candlesticks.py` (`Bar`) | extend, don't fork |
| Swing/fractal detection | `mnq_system/swings.py`, `ict_faithful` `_fractals`/`_last_confirmed` | causal, confirmation-width based |
| Timeframe resample + causal align | `mnq_system/timeframe_alignment.py`, `ict_faithful` `_resample`/`_asof` | as-of alignment already leakage-safe |
| Sessions / prior-day / prior-week levels | `mnq_system/session_features.py`, `ict_faithful` `prior_rth_high_low_by_date` / `_session_hl` | ET, DST-safe patterns exist |
| ATR / vol | `mnq_system/indicators.py` (`atr`) | displacement threshold input |
| **FVG detection + mitigation + P/D eligibility** | **`ict_faithful/strategy.py` + `SPEC.md` §7b** | geometry (bull high₁<low₃ / bear low₁>high₃), body-close mitigation (interp A), P/D-zone eligibility — all course-resolved |
| **Displacement-leg boundary + arming** | `ict_faithful/SPEC.md` §7c | boundary = "through the FVG-creating impulse" (cand. B); arm at FVG formation k+1 (≠ MSS close) |
| **Manipulation-extreme tracking + geometry invariants** | `ict_faithful/strategy.py` | running max/min sweep→MSS; entry-below/above-extreme + stop-beyond, reject on invalid geometry |
| Premium/Discount | `ict_faithful` P/D on the swept dealing range | Fib-50% |
| Matched-random control, bootstrap, DSR, breadth | `trend_carry/stats.py`, `ict_mech`, `market_state/bootstrap.py` | for the *later* validation phase only |
| Blinded fidelity-audit tooling | `ict_faithful/FIDELITY_SELECTION_PROTOCOL.md` + scratch tools | to compare engine vs discretionary reads |

**Net:** the FVG, mitigation, P/D, displacement-boundary, arming, and geometry logic are
lifted from `ict_faithful` (already course-validated). The genuinely NEW work is: the live
feed/bar-builder, **multi-candidate liquidity/manipulation management + ranking**, ERL/IRL
+ NWOG/ORG PD-arrays, HTF-context *labelling* (not veto), liquidity-target R:R, the
recommendation formatter + event trail, and the FastAPI service.

## 2. Module structure (adapts the requested layout; ⇒ = reuse)
```
ict_live/
  docs/            ARCHITECTURE · METHODOLOGY_SPEC · DATA_FEED_SPEC · STATE_MACHINE_SPEC · UNRESOLVED_DECISIONS
  feeds/           base.py · tradingview_webhook.py            # dumb transport only
  market/          bar.py(⇒candlesticks) · bar_builder.py · timeframe.py(⇒timeframe_alignment) · sessions.py(⇒session_features) · calendar.py
  structure/       swings.py(⇒mnq_system.swings) · trend.py · dealing_range.py
  liquidity/       pools.py · equal_highs_lows.py · session_levels.py · prior_day_week.py · erl_irl.py
  pd_arrays/       fvg.py(⇒ict_faithful) · nwog.py · org.py
  context/         premium_discount.py(⇒ict_faithful) · htf_context.py · amd.py
  setups/          candidates.py · sweep.py · displacement.py · mss.py · manipulation.py · fvg_entry.py · state_machine.py
  risk/            stop.py · targets.py · rr.py
  recommendations/ model.py · scorer.py · formatter.py
  storage/         market_store.py · setup_log.py · recommendation_log.py · event_trail.py
  api/             webhook.py(FastAPI) · status.py
  tests/           (mirrors the testing-requirements list)
```

## 3. Data flow (per completed 1-minute bar)
```
Pine alert (1m close) --webhook--> api/webhook.py
  -> validate schema, symbol, ordering; dedupe; detect gaps           [DATA_FEED_SPEC]
  -> storage/market_store: persist raw 1m
  -> market/bar_builder: deterministically resample -> 5m/15m/1H/4H/D/W
       (higher TF bar emitted only when it CLOSES; forming bar flagged)
  -> engine.tick(now):
       structure (swings/trend, per TF, causal)
       liquidity (pools/ERL/IRL, session levels, PDH/PDL/PWH/PWL, equal H/L, NWOG/ORG)
       context (dealing range -> premium/discount; HTF context label; AMD phase)
       setups/state_machine: update candidate liquidity events; per candidate advance
         sweep -> displacement -> candidate/confirmed MSS -> same-leg FVG(s) -> arm@k+1
         -> await post-MSS retrace into FVG -> geometry+R:R validation
       risk: stop (beyond manip extreme/pool), target (next liquidity), R:R>=min
       recommendations: rank candidates -> LONG/SHORT/NO-TRADE + quality + reason
  -> storage/event_trail + setup_log + recommendation_log
  -> api/status exposes current market state + latest recommendation
```

## 4. Service (FastAPI)
- `POST /webhook/tradingview` — ingest one completed 1m bar (see DATA_FEED_SPEC).
- `GET /status` — current per-symbol market state, live candidates, latest recommendation.
- `GET /recommendations?symbol=&from=&to=` — recommendation + event-trail history (audit).
- `GET /health` — feed liveness, last-bar timestamp, gap flags.
Single process, in-memory engine state + append-only durable logs (Parquet/JSONL). No DB
required in Phase 1; storage layer is an interface so a DB can slot in later.

## 5. Explicitly out of scope for Phase 1
Order execution; broker connectivity; parameter optimization; touching any locked OOS
dataset; the carry sleeve / trend_carry work (separate line). Backtest/validation of this
engine happens only after the spec is frozen (see METHODOLOGY_SPEC §Validation).
