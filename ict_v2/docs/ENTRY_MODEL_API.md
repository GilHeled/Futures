# v2 Execution-Model Plugin Contract  — **FROZEN v1.1 (2026-08-27)**

> **SCOPE — this course.** v2 implements the course methodology in `ict_live/docs/METHODOLOGY_SPEC.md`.
> **CORRECTED 2026-09-03 against the RAW lesson slides:** there are **two** valid execution models —
> (1) **`structure`**, the confirmed intraday market-structure reversal (Lessons 15/16: Low→High→HL→HH
> for a long, High→Low→LH→LL for a short), which is the course's core entry and needs no FVG; and
> (2) **`fvg`**, a Fair Value Gap used as a **contextual PD array / optional lower-timeframe entry
> refinement** (Lesson 12), **no longer mandatory**. The prior claim "FVG is the sole execution model"
> was an over-hardening that inverted the course emphasis (see memory `project_v2_structure_entry_correction`).
> NWOG/ORG remain context/targets. Order Blocks / Breakers / Mitigation Blocks (broader-ICT constructs
> the course does not teach) stay out of scope. This contract keeps adding a course model a plugin.

> **v1.1 change (2026-08-27).** `detect()` carries a final `bars` argument:
> `detect(disp, mss, ms, direction, bars) -> list[Entry]`. The engine hands the raw OHLC window (same
> cursor) to **every** model, generically — no `if model ==`. It was added when an exploratory
> candle-based detector needed the actual candles (v1 pre-computes only FVGs onto `ms`); that detector
> was later removed as out-of-course-scope, but the argument is retained as general, model-agnostic
> plumbing a future course-defined candle model may use. A model that reads pre-computed objects off
> `ms` (FVG) ignores `bars`. Verified inert for FVG: threading real bars vs `None` gives byte-identical
> candidates (0 diffs / 8267 candidates / 59 seeds; `test_detect_v11_bars_arg_is_inert_for_fvg`).

This document freezes the API that every v2 execution / entry model must implement. It exists so that
adding a (course-defined) model is a **plugin**, never an engine change.

> **The invariant.** The engine (`ict_v2/pipeline.py`) MUST NOT contain model-specific logic — no
> `if model == "fvg"`, no `if model == "<any model>"`. The engine only ever runs the four generic
> verbs below and reads the common `Entry` contract. All model-specific knowledge lives in the
> model's registry entry. Verified: the data-driven engine is byte-for-byte identical to the prior
> FVG-only implementation (0 diffs / 8267 candidates / 59 seeds).

All symbols below live in **`ict_v2/entry_models.py`**.

---

## The engine's four verbs (per manipulation → displacement)

```
generate  →  entries = EM.detect(model, disp, mss, ms, direction, bars)  # ask each enabled model
assemble  →  geom    = EM.assemble(entry, sweep_extreme, active_erl, min_stop)   # uniform geometry
validate  →  ok,why  = EM.validate(model, entry, geom, context)       # universal + model-specific
execute   →  engine gates by HTF context and builds a Candidate tagged entry.model
```

The engine never branches on the model name. A model is selected only through `resolve()` /
`REGISTRY`; everything else is data + callables the model provides.

---

## 1. The `Entry` contract (the common object the engine + dashboard consume)

Every detector returns a list of `Entry`. This is the ONLY entry type the engine understands.

| field | type | meaning |
|---|---|---|
| `model` | str | registry key that produced it (the course's entry model is `"fvg"`) |
| `direction` | `"long"`/`"short"` | trade side |
| `ref` | float | **reference entry price** (where the order rests) |
| `invalidation` | float | **invalidation level** (price beyond which the entry object is void) |
| `lifecycle` | str | **model-specific sub-state** (dashboard only) — must be in the model's lifecycle vocab |
| `state` | str | **common state** (engine only) — one of `COMMON_STATES`; DERIVED from `lifecycle` if omitted |
| `quality` | float\|None | optional confidence 0..1 the model may set; `None` if not applicable |
| `reason` | str | why rejected / not usable (filled by the engine; empty when fine) |
| `origin_index` | int | bar index where the object formed (audit) |
| `id` | str | stable id (audit / dedup) |
| `source` | obj | the underlying detector object (audit/deps); **consumers must ignore it** |

`to_dict()` serialises exactly: `{model, direction, ref, invalidation, state, lifecycle, quality, reason}`.

### Two-level state (frozen)

* **Common state** — `COMMON_STATES = ("waiting", "valid", "rejected", "completed")`. **All the engine reads.**
  * `waiting` — object exists but not yet usable (forming / awaiting its trigger)
  * `valid` — live/usable (an order can rest here now)
  * `rejected` — never became valid (structural fault)
  * `completed` — was valid and then played out / consumed (e.g. an FVG mitigated)
* **Model lifecycle** — each model's own richer sub-states, declared in `LIFECYCLE[model]`:
  ```python
  LIFECYCLE["<model>"] = {
      "vocab": [ ...ordered sub-states... ],          # dashboard shows these
      "map":   { "<sub-state>": "<common state>", ... }   # engine reads the common state only
  }
  ```
  `Entry.__post_init__` derives `state = common_state(model, lifecycle)` when `state` is omitted, so a
  detector normally sets only `lifecycle`. The one defined model:
  * FVG: `waiting → valid → mitigated`   (mitigated ⇒ `completed`)

  A future course-defined model registers its own `vocab` + `map` the same way; the mechanism does not
  privilege FVG (a model/sub-state absent from `LIFECYCLE` falls back to the safe `waiting`).

---

## 2. `detect()` — the model's detector

```python
def detect_fn(disp, mss, ms, direction, bars) -> list[Entry]: ...
```

* Registered as `REGISTRY["<model>"]["detect"]`; the engine calls it via
  `EM.detect(model, disp, mss, ms, direction, bars)`.
* Inputs: the displacement `disp` (leg off the manipulation), its `mss` (may be `None`), the v1
  `MarketState` `ms` (read-only — for structure/`active_erl`), the chain `direction`, and `bars` —
  the raw OHLC window up to the cursor (v1.1). A future candle-based model would read `bars` for the
  candles v1 does not pre-compute; the course model (FVG) sources pre-detected gaps off `ms` and
  ignores `bars`. The engine passes the SAME `bars` to every model — never a per-model branch.
* Output: zero or more `Entry` objects that this model finds **within that displacement**. Set each
  entry's `model`, `direction`, `ref`, `invalidation`, and `lifecycle` (state derives itself).
* **Causal / no-repaint.** Only use data up to the cursor; once emitted, an entry's identity/ref must
  not change. Return `[]` when the model has no entry on this leg (not an error).
* A model with no detector yet (`detect=None`) returns `[]` and is inert — this is how planned models
  stay off until implemented.

---

## 3. `assemble()` — universal geometry (models do NOT reimplement this)

```python
def assemble(entry, sweep_extreme, active_erl, min_stop=None) -> dict
# -> {"entry", "stop", "target", "rr", "objective", "reject"}
```

The SAME geometry for every model, so the engine treats all models identically:

* `entry` = `entry.ref`
* `stop` = the manipulation extreme (`sweep_extreme`)
* `target` = nearest **opposing active ERL** (the draw): a high pool above `ref` for longs, a low pool
  below for shorts
* `rr` = reward / risk (a **quality metric, not a gate**)
* `reject` = a STRUCTURAL invalidation string, or `""`:
  common-state `completed`/`rejected` (e.g. mitigated) · degenerate stop (`risk < min_stop`) ·
  bad geometry (entry not beyond the extreme) · no opposing-ERL target.

A model expresses its geometry **only** through the `Entry` (`ref` + `invalidation`); the setup
geometry (stop = manip extreme, target = draw) is universal to ICT and owned by the engine.

---

## 4. `validate()` — optional model-specific validation

```python
def validate_fn(entry, geom, context) -> (ok: bool, reason: str)
```

* Registered as the optional `REGISTRY["<model>"]["validate"]`; engine calls
  `EM.validate(model, entry, geom, context)`. **Default (no validator) = `(True, "")`.**
* Runs AFTER the universal `assemble()` reject and BEFORE the RR-quality floor. Use it only for a rule
  the universal geometry can't express that a course-defined model requires. Return `(False,
  "<reason>")` to reject.
* Must be pure/causal and must not mutate `entry`, `geom`, or `context`.

---

## 5. Optional confirmation hooks (reserved)

Today the cascade's confirmation (15m confirms the 1H setup; 1m triggers the 15m) is **engine-level
and model-agnostic** — it only checks direction against a gated higher-layer setup, so models need no
confirmation code. A model that later needs its own confirmation step registers an **optional**
callable and the engine calls it generically — no `if model ==`:

```python
def confirm_fn(entry, geom, context, higher_layer) -> (ok: bool, reason: str)   # RESERVED / optional
```

This slot is reserved in the contract; it is **not required** and no model implements it yet. When
introduced it follows the same rules as `validate()` (pure, causal, default `(True, "")`).

---

## 6. Registering a model (the whole checklist)

A model is one registry entry + (optionally) one lifecycle entry. **No engine edit.** Register a model
ONLY when authoritative course material defines it as an entry array (see SCOPE at the top).

```python
LIFECYCLE["<model>"] = {"vocab": [...], "map": {...}}          # sub-states → common states
REGISTRY["<model>"] = {
    "implemented": True,                 # False until built + verified → inert
    "detect": <model>_entries,           # detect_fn above
    "validate": <model>_validate,        # optional
    "desc": "<model> — ...",             # shown in the dashboard catalog
}
```

Rules:

1. **Course-defined only.** A model is registered only if the captured course methodology defines it.
   FVG is the course's entry array (`DEFAULT_MODELS = ("fvg",)`). General-ICT models the course does
   not teach (Order Block, Breaker, Mitigation Block, IFVG, IOFED) are deliberately NOT registered.
2. **Off by default.** Any additional model is enabled only via `ICT_V2_ENTRY_MODELS=...` (serve) or
   the `entry_models=` parameter.
3. **Inert until implemented.** `resolve()` drops any model whose `implemented` is False (and always
   keeps at least `fvg`), so a declared-but-unbuilt model can never run.
4. **No PnL / tuning to add a model.** A model is added for *faithfulness to the course*; validating
   whether it has edge is a separate, pre-registered study — never a reason to change the detector.
5. **Tag + verify.** Every candidate is tagged `entry.model`; add tests asserting the model conforms
   to this contract (fields, lifecycle vocab, common-state mapping) and that the engine stays generic.

---

## 7. Freeze

This contract is **frozen at v1.1**. Changing it (new required field, changed verb signature, changed
common-state vocabulary) is a deliberate, versioned change to this document + the tests — not an
incidental edit, and only when a real, course-defined plugin exposes a genuine missing capability.
Adding a *model* never touches this contract.

### Models registered against this contract
* **FVG** (default, the course's entry PD array) — sources v1's pre-computed gaps off `ms`.
* No others. Order Block / Breaker / Mitigation Block / IFVG / IOFED are broader-ICT constructs the
  captured course does not teach; they are intentionally absent (see SCOPE). Register a new model only
  when authoritative course material defines it.

### Changelog
* **v1.1 (2026-08-27)** — `detect()` carries `bars` (raw OHLC, passed generically to every model).
  Added while exploring a candle-based detector later removed as out-of-course-scope; retained as
  general model-agnostic plumbing. Inert for FVG (byte-for-byte verified).
* **v1.0 (2026-08-26)** — initial frozen contract (four verbs, Entry contract, two-level state).
