"""The v2 SEMANTIC LAYER — four explicit, separated concerns (user decision 2026-08-27).

    1. STRUCTURE      — is there a valid ICT setup? (the sweep→displacement→MSS→entry chain + valid
                        geometry). INDEPENDENT of RR, HTF bias, and premium/discount.
    2. QUALITY        — RR grade, HTF alignment, premium/discount, AMD phase, liquidity — MEASURED and
                        displayed, NEVER gating.
    3. COURSE FILTERS — course execution rules (≥3R, killzone, …) that decide TAKE vs SKIP. A failed
                        filter is a SKIP, never structural invalidation.
    4. RECOMMENDATION — TAKE / SKIP / WATCH, derived from the three above.

HTF context (bias, premium/discount) is QUALITY — never a structural veto. This removes the old
bias-veto contradiction and makes HTF behave as the course intends (§1/§17: context, not a veto).
Pure functions; no v1 or engine state. `structure` is computed by the pipeline (it owns the chain);
this module owns the FILTER and RECOMMENDATION logic.
"""
from __future__ import annotations

# Course-faithful defaults. Each is a FILTER (→ SKIP), NEVER structural (→ invalid).
# NOTE on min_rr: the RAW lessons give NO numeric R:R (only Lesson 9's qualitative "better risk/reward
# in premium/discount"). The old 3.0 came from the distilled METHODOLOGY §16 / FROZEN B4 — a PROJECT
# choice, not course methodology. Set to 2.0 by the user (2026-08-27): "around 2R". It is a tunable
# take/skip threshold, not a course rule; RR itself remains a quality metric (rr_quality grade).
COURSE_FILTERS = {
    "min_rr": 2.0,      # user-set take/skip R:R floor (~2R); NOT a course-specified number
    "killzone": True,   # §11 / Lesson 5: the manipulation should occur inside a trading killzone
}

# When True (faithful default), a valid+filtered setup whose FVG is NOT yet retraced is ARMED → WATCH
# ("waiting for retrace", course §14). Set False (a VALIDATION relaxation) to let armed setups read as
# TAKE immediately — so the pipeline fires on more setups when eyeballing the logic against charts.
REQUIRE_RETRACE = True

RECOMMENDATIONS = ("TAKE", "SKIP", "WATCH")


def configure(*, min_rr=None, killzone=None, require_retrace=None) -> None:
    """Set the take/skip knobs from the service (env-driven). These are validation/tuning levers, NOT
    course methodology — the faithful defaults are min_rr=2.0, killzone=on, require_retrace=on."""
    global REQUIRE_RETRACE
    if min_rr is not None:
        COURSE_FILTERS["min_rr"] = float(min_rr)
    if killzone is not None:
        COURSE_FILTERS["killzone"] = bool(killzone)
    if require_retrace is not None:
        REQUIRE_RETRACE = bool(require_retrace)


def evaluate_filters(*, rr, killzone, cfg=None) -> list:
    """The COURSE FILTERS for a structurally-valid setup → list of {name, ok, reason}. Each is a
    course execution rule; failing one makes the recommendation SKIP (the setup stays structurally
    valid). Defaults are course-faithful (≥3R + killzone); pass `cfg` to override/toggle."""
    cfg = COURSE_FILTERS if cfg is None else cfg
    out = []
    mr = cfg.get("min_rr")
    if mr:
        has_rr = rr is not None and rr > 0
        ok = has_rr and rr >= mr
        reason = "" if ok else (f"RR {rr:g} < {mr:g}R" if has_rr else "no liquidity target / RR")
        out.append({"name": f"≥{mr:g}R", "ok": ok, "reason": reason})
    if cfg.get("killzone"):
        ok = bool(killzone)
        out.append({"name": "killzone", "ok": ok,
                    "reason": "" if ok else "manipulation outside a trading killzone"})
    return out


def recommend(*, structure, structure_reason="", filters=(), entry_live=True) -> tuple:
    """Derive the RECOMMENDATION + its reasons from structure + course filters + entry readiness:
      TAKE  — structure valid, every course filter passes, AND the entry is LIVE (price has retraced
              into it). This is a trade to take now.
      WATCH — structure still forming (chain incomplete), OR a valid+filtered setup whose entry is
              ARMED but not yet retraced into (`entry_live=False`) — 'waiting for retrace'.
      SKIP  — structure valid but a course filter failed (a valid setup, filtered out), OR invalid structure.
    A valid setup whose FVG has not been retraced is NOT a SKIP — it is a WATCH awaiting the retrace
    (course §14: enter on the later retrace into the FVG). Cascade eligibility (promotion) is separate
    from TAKE: an armed, filter-passing setup is still eligible; only its recommendation waits."""
    if structure == "forming":
        return "WATCH", [structure_reason or "developing — the ICT chain is not yet complete"]
    if structure == "invalid":
        return "SKIP", [structure_reason or "invalid structure"]
    fails = [f for f in filters if not f["ok"]]
    if fails:
        return "SKIP", [f"{f['name']} — {f['reason']}" for f in fails]
    if not entry_live and REQUIRE_RETRACE:
        return "WATCH", ["armed — waiting for price to retrace into the entry (FVG not yet touched)"]
    return "TAKE", []
