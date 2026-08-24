"""FREE daily data adapter (Yahoo continuous futures) for Phase-1 trend.

PROTOCOL AMENDMENT (documented, user-directed 2026-07-28): the frozen pre-registration
specified paid GLBX dated contracts ($25) to build a self-audited causal roll + carry.
The user directed a FREE path. Yahoo `=F` gives free continuous daily candles for all 14
roots (full 2010→2026 coverage), which is adequate for TREND (sign-of-lookback-return,
risk-scaled). Two deviations vs the frozen design, recorded honestly:
  1. Yahoo's roll/back-adjustment is opaque -> the §3 prefix-stability *roll* audit cannot
     be performed; we instead flag roll-gap artifacts (extreme daily returns) as a
     data-quality check. A GO here would need confirmation on clean back-adjusted data.
  2. CARRY (Phase 2) needs two dated ranks -> NOT available free; free path is trend-only.
Everything downstream (signal, sizing, costs, splits, Go/No-Go) is the FROZEN code, unchanged.
"""
from __future__ import annotations
import pathlib, warnings
import numpy as np, pandas as pd
from trend_carry import config as C

warnings.filterwarnings("ignore")
CACHE = pathlib.Path(__file__).resolve().parent.parent / "cache" / "bars"
CACHE.mkdir(parents=True, exist_ok=True)
YMAP = {"ES":"ES=F","NQ":"NQ=F","ZT":"ZT=F","ZN":"ZN=F","ZB":"ZB=F","6E":"6E=F","6J":"6J=F",
        "6A":"6A=F","GC":"GC=F","HG":"HG=F","CL":"CL=F","NG":"NG=F","ZC":"ZC=F","ZS":"ZS=F"}


def _cache_path(root): return CACHE / f"yf_{root}_1d.parquet"


def pull_free(force: bool = False) -> None:
    import yfinance as yf
    for root, tk in YMAP.items():
        p = _cache_path(root)
        if p.exists() and not force:
            continue
        df = yf.download(tk, start="2010-06-01", end="2026-07-10", interval="1d",
                         progress=False, auto_adjust=False)
        if df is None or len(df) == 0:
            raise RuntimeError(f"no free data for {root} ({tk})")
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df[["Open", "High", "Low", "Close"]].dropna()
        df.to_parquet(p)
    print(f"[free_data] cached {len(YMAP)} roots -> {CACHE}")


def _close_panel() -> pd.DataFrame:
    cols = {}
    for root in C.ROOTS:
        df = pd.read_parquet(_cache_path(root))
        s = df["Close"].copy(); s.index = pd.DatetimeIndex(s.index)
        cols[root] = s
    panel = pd.DataFrame(cols).sort_index()
    panel.index = panel.index.tz_localize("UTC") if panel.index.tz is None else panel.index.tz_convert("UTC")
    return panel


# ---- frozen-signature builders (mirror continuous.build_*) ----
def build_returns():   return _close_panel().pct_change()
def build_adjusted():  return _close_panel()                 # Yahoo continuous close = trend price index
def build_front_close(): return _close_panel()


def roll_gap_diagnostic(thresh=0.12) -> dict:
    """Count suspected roll-gap artifacts (|daily return| > thresh) per root."""
    r = build_returns()
    return {root: int((r[root].abs() > thresh).sum()) for root in C.ROOTS}
