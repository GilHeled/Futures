"""Market-data helpers for the data-update path — pure, testable, no I/O.

The key piece is `sanitize_final_bar`: yfinance stamps the last bar of a completed session with the
CME SETTLEMENT price as its close, which pins the close to one extreme (the settle) while the true
LAST TRADE was the opposite extreme. This repairs that one bar deterministically (no magnitude
threshold), so charts match the trading platform's last-traded close.
"""
from __future__ import annotations


def df_to_records(df, symbol: str) -> list[dict]:
    """Convert a yfinance 1m OHLCV DataFrame to store JSONL records (chronological)."""
    if getattr(df, "empty", True):
        return []
    if getattr(df.columns, "nlevels", 1) > 1:            # flatten yfinance's (field, ticker) columns
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    out: list[dict] = []
    for ts, r in df.iterrows():
        o_ms = int(ts.timestamp() * 1000)
        v = float(r["Volume"]) if r["Volume"] == r["Volume"] else 0.0   # NaN-safe
        out.append({"symbol": symbol, "open_ms": o_ms, "close_ms": o_ms + 60000,
                    "o": round(float(r["Open"]), 2), "h": round(float(r["High"]), 2),
                    "l": round(float(r["Low"]), 2), "c": round(float(r["Close"]), 2), "v": v})
    return out


def sanitize_final_bar(records: list[dict]) -> dict | None:
    """Repair the settlement-artifact on the FINAL record IN PLACE. Returns a summary dict when a
    repair was made, else None.

    Deterministic signature (settle pins the close to an extreme, open on the other side):
      • down-settle: close == low  and open > close -> last trade = high -> close:=high, low:=min(open,high)
      • up-settle:   close == high and open < close -> last trade = low  -> close:=low,  high:=max(open,low)
    Every other final bar is left untouched.
    """
    if not records:
        return None
    b = records[-1]
    o, h, l, c = b["o"], b["h"], b["l"], b["c"]
    if c == l and o > c:                                  # down-settle
        b["c"], b["l"] = h, min(o, h)
        return {"symbol": b.get("symbol"), "direction": "down-settle", "old_close": c, "new_close": h}
    if c == h and o < c:                                  # up-settle
        b["c"], b["h"] = l, max(o, l)
        return {"symbol": b.get("symbol"), "direction": "up-settle", "old_close": c, "new_close": l}
    return None
