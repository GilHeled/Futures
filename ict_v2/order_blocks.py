"""Order Blocks (ICT concept) — STANDALONE, READ-ONLY.

Faithful Python port of the Pine Script "Order Blocks (ICT Concept)" indicator. Self-contained: it
imports nothing from the trading engine, has no side effects, and never influences trade generation.
A "main script" (the serving path) calls `detect_order_blocks(bars, ...)` and renders `summary(...)`.

Definition (identical to the indicator), replayed bar-by-bar:
  • swing pivots via symmetric `swing_len` bars each side (ta.pivothigh/pivotlow).
  • BULLISH OB — confirmed when a HIGHER HIGH prints (pivot high > previous pivot high): the OB candle
    is the last DOWN-close candle at/before the most recent swing low; top = that candle's high,
    bottom = min(that candle's low, the swing-low candle's low).
  • BEARISH OB — confirmed when a LOWER LOW prints: the OB candle is the last UP-close candle
    at/before the most recent swing high; top = max(that candle's high, the swing-high candle's high),
    bottom = that candle's low.
  • Skip if the zone was already violated between the swing and now, or a duplicate at that bar.
  • Mitigation/expiry: a bull OB dies when a bar CLOSES below its bottom, a bear OB when a bar closes
    above its top; both expire after `max_bars` age.
Works on engine `Bar` objects (`.open/.high/.low/.close/.open_time`) or snapshot dicts ('o','h','l','c','t').
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


def _o(b): return float(b.open if hasattr(b, "open") else b["o"])
def _h(b): return float(b.high if hasattr(b, "high") else b["h"])
def _l(b): return float(b.low if hasattr(b, "low") else b["l"])
def _c(b): return float(b.close if hasattr(b, "close") else b["c"])
def _t(b):
    if hasattr(b, "open_time"):
        return b.open_time.isoformat()
    return b.get("t") if isinstance(b, dict) else None


@dataclass
class OrderBlock:
    top: float
    bottom: float
    mid: float
    left_index: int
    left_time: Optional[str]
    is_bull: bool
    state: str                      # "active" | "mitigated" | "expired"
    mitigation_index: Optional[int]

    @property
    def label(self) -> str:
        return "OB+" if self.is_bull else "OB-"

    def to_dict(self) -> dict:
        return {"top": round(self.top, 2), "bottom": round(self.bottom, 2), "mid": round(self.mid, 2),
                "left_index": self.left_index, "left_time": self.left_time, "is_bull": self.is_bull,
                "state": self.state, "mitigation_index": self.mitigation_index, "label": self.label}


def _is_ph(bars, p, L):
    v = _h(bars[p])
    for k in range(1, L + 1):
        if _h(bars[p - k]) >= v or _h(bars[p + k]) >= v:
            return False
    return True


def _is_pl(bars, p, L):
    v = _l(bars[p])
    for k in range(1, L + 1):
        if _l(bars[p - k]) <= v or _l(bars[p + k]) <= v:
            return False
    return True


def _dup(active, left, is_bull):
    return any(o["left"] == left and o["is_bull"] == is_bull for o in active)


def _already_mit(bars, c, top, bot, is_bull, window):
    for j in range(0, window + 1):
        idx = c - j
        if idx < 0:
            break
        cc = _c(bars[idx])
        if (cc < bot) if is_bull else (cc > top):
            return True
    return False


def detect_order_blocks(bars, *, swing_len: int = 3, max_bars: int = 100) -> list[OrderBlock]:
    """Replay the indicator over `bars`; return every OB created, each with its final state."""
    n = len(bars)
    L = swing_len
    if n < 2 * L + 1:
        return []
    active: list[dict] = []
    allobs: list[dict] = []
    lsh = psh = lsl = psl = None
    lshbar = lslbar = None

    def _mk(left, top, bot, is_bull):
        ob = {"left": left, "top": top, "bot": bot, "is_bull": is_bull,
              "left_time": _t(bars[left]), "state": "active", "mit": None}
        active.append(ob)
        allobs.append(ob)

    for c in range(n):
        p = c - L
        ph = pl = None
        if p - L >= 0:                                   # p has L bars on each side (right side = up to c)
            if _is_ph(bars, p, L):
                ph = _h(bars[p])
            if _is_pl(bars, p, L):
                pl = _l(bars[p])
        if ph is not None:
            psh, lsh, lshbar = lsh, ph, p
        if pl is not None:
            psl, lsl, lslbar = lsl, pl, p

        close_c = _c(bars[c])
        for ob in active[:]:                             # mitigation + expiry
            too_old = (c - ob["left"]) > max_bars
            hit = (close_c < ob["bot"]) if ob["is_bull"] else (close_c > ob["top"])
            if hit or too_old:
                ob["state"] = "mitigated" if hit else "expired"
                ob["mit"] = c if hit else None
                active.remove(ob)

        # BULLISH OB — higher high
        if ph is not None and psh is not None and ph > psh and lslbar is not None:
            startJ = c - lslbar
            if 0 <= startJ <= max_bars:
                obIdx = -1
                for j in range(startJ, startJ + max_bars + 1):
                    idx = c - j
                    if idx < 0:
                        break
                    if _o(bars[idx]) > _c(bars[idx]):    # down-close candle
                        obIdx = j
                        break
                if obIdx >= 0:
                    left = c - obIdx
                    if (c - left) <= max_bars and not _dup(active, left, True):
                        top = _h(bars[c - obIdx])
                        bot = min(_l(bars[c - obIdx]), _l(bars[lslbar]))
                        if not _already_mit(bars, c, top, bot, True, c - lslbar):
                            _mk(left, top, bot, True)

        # BEARISH OB — lower low
        if pl is not None and psl is not None and pl < psl and lshbar is not None:
            startJ = c - lshbar
            if 0 <= startJ <= max_bars:
                obIdx = -1
                for j in range(startJ, startJ + max_bars + 1):
                    idx = c - j
                    if idx < 0:
                        break
                    if _c(bars[idx]) > _o(bars[idx]):    # up-close candle
                        obIdx = j
                        break
                if obIdx >= 0:
                    left = c - obIdx
                    if (c - left) <= max_bars and not _dup(active, left, False):
                        top = max(_h(bars[c - obIdx]), _h(bars[lshbar]))
                        bot = _l(bars[c - obIdx])
                        if not _already_mit(bars, c, top, bot, False, c - lshbar):
                            _mk(left, top, bot, False)

    out = []
    for ob in allobs:
        out.append(OrderBlock(top=ob["top"], bottom=ob["bot"], mid=(ob["top"] + ob["bot"]) / 2.0,
                              left_index=ob["left"], left_time=ob["left_time"], is_bull=ob["is_bull"],
                              state=ob["state"], mitigation_index=ob["mit"]))
    return out


def summary(obs: list[OrderBlock], max_bull: int = 1, max_bear: int = 1) -> dict:
    """The VISIBLE set (most-recent N still-active OBs per direction, as the indicator shows) + counts."""
    active = [o for o in obs if o.state == "active"]
    bull = sorted([o for o in active if o.is_bull], key=lambda o: -o.left_index)[:max_bull]
    bear = sorted([o for o in active if not o.is_bull], key=lambda o: -o.left_index)[:max_bear]
    return {"visible": [o.to_dict() for o in (bull + bear)],
            "active_bull": sum(1 for o in active if o.is_bull),
            "active_bear": sum(1 for o in active if not o.is_bull),
            "total": len(obs)}
