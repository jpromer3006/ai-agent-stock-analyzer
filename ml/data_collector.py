"""
data_collector.py - Point-in-time feature warehouse for training.

The fix for "are we collecting data to train on?" Each run snapshots, for every
scanned name, the features we actually saw that day - the strategy scores, the
classic factors, the sector, the liquidity, and the market state - and appends
them to a growing warehouse with that date stamped on them. Later, forward returns
are joined in to make labeled training examples.

Why a live snapshot and not a rebuild: price-based features can be reconstructed
from history, but signals like news sentiment, social froth, regime, and the
analyst's verdict cannot be backfilled. Snapshotting them as-of each day is the
only way to build an honest, survivorship-bias-free training set (a name is
recorded even if it later delists, because we saw it that day).

This is the corpus the learned router and the time-series transformer train on.

    PYTHONPATH=. python3 ml/data_collector.py
"""

from __future__ import annotations

import logging
from datetime import date as _date
from pathlib import Path
from typing import Optional

import pandas as pd

from data import price_cache
from ml.meta_features import _factors, STRAT_KEYS
from ml.strategy_registry import ScoringContext, score_all

logger = logging.getLogger(__name__)
_WH = Path(__file__).resolve().parent.parent / "data" / "warehouse"
_WH.mkdir(parents=True, exist_ok=True)
_SNAP = _WH / "snapshots.parquet"


def _market_state(symbols) -> dict:
    import numpy as np
    vols, moms = [], []
    for s in symbols[:400]:
        df = price_cache.get_history(s)
        if df is None or len(df) < 252:
            continue
        c = df["Close"]
        vols.append(float(c.pct_change().tail(60).std()))
        moms.append(1 if float(c.iloc[-21] / c.iloc[-252] - 1) > 0 else 0)
    state = {"mkt_vol": float(np.mean(vols)) if vols else 0.02,
             "mkt_breadth": float(np.mean(moms)) if moms else 0.5,
             "yield_slope": 0.5, "credit_spread": 3.0}
    try:
        from ml.meta_features import fred_series
        d10, d2, hy = fred_series("DGS10"), fred_series("DGS2"), fred_series("BAMLH0A0HYM2")
        state["yield_slope"] = float(d10.iloc[-1]) - float(d2.iloc[-1])
        state["credit_spread"] = float(hy.iloc[-1])
    except Exception:
        pass
    return state


def collect_snapshot(symbols: list[str], max_names: int = 600,
                     as_of: Optional[str] = None) -> int:
    """Append one dated feature row per name to the warehouse. Token-free."""
    as_of = as_of or str(_date.today())
    spy = price_cache.get_history("SPY")
    spyc = spy["Close"] if spy is not None else None
    state = _market_state(symbols)
    rows = []
    for s in symbols[:max_names]:
        df = price_cache.get_history(s)
        if df is None or len(df) < 60:
            continue
        sc = score_all(ScoringContext(s, df), only=STRAT_KEYS)
        fac = _factors(df, spyc) if spyc is not None else {}
        # px_entry: the close we actually saw on snapshot day. Stored so a
        # forward return can be computed later without depending on the entry
        # bar still being inside the rolling 2y price cache.
        row = {"date": as_of, "ticker": s, "fwd": None,
               "px_entry": float(df["Close"].iloc[-1]), **state}
        row.update({k: sc[k].score for k in STRAT_KEYS})
        row.update({k: fac.get(k) for k in ("mom121", "vol60", "beta", "liq")})
        rows.append(row)
    if not rows:
        return 0
    new = pd.DataFrame(rows)
    if _SNAP.exists():
        old = pd.read_parquet(_SNAP)
        # avoid duplicate (date,ticker) rows for the same day
        old = old[~((old["date"] == as_of) & (old["ticker"].isin(new["ticker"])))]
        new = pd.concat([old, new], ignore_index=True)
    new.to_parquet(_SNAP)
    logger.info("warehouse: +%d rows for %s (total %d)", len(rows), as_of, len(new))
    return len(rows)


def label_warehouse(horizon: int = 20) -> pd.DataFrame:
    """Fill forward returns for snapshots old enough, and return the labeled corpus."""
    if not _SNAP.exists():
        return pd.DataFrame()
    df = pd.read_parquet(_SNAP)
    has_entry = "px_entry" in df.columns
    for i, r in df[df["fwd"].isna()].iterrows():
        px = price_cache.get_history(r["ticker"])
        if px is None or px.empty:
            continue
        # Locate the forward bar by DATE offset from the snapshot, not by the
        # entry bar's position. This keeps labeling independent of how far back
        # the rolling 2y price cache reaches: as long as `horizon` bars exist
        # AFTER the snapshot date, the row can be labeled even once the entry
        # bar itself has rolled off the window.
        try:
            idx = px.index.tz_localize(None) if px.index.tz is not None else px.index
        except (TypeError, AttributeError):
            idx = px.index
        snap = pd.Timestamp(r["date"]).normalize()
        norm = idx.normalize()
        close = px["Close"].to_numpy()
        after = close[(norm > snap).to_numpy() if hasattr(norm > snap, "to_numpy")
                      else (norm > snap)]
        if len(after) < horizon:
            continue                                   # horizon not elapsed yet
        # Denominator: the close stored at snapshot time, so the return is
        # anchored to the real entry even if that bar is gone. Fall back to the
        # last bar at/before the snapshot for legacy rows with no px_entry.
        entry_px = float(r["px_entry"]) if has_entry and pd.notna(r.get("px_entry")) else None
        if not entry_px or entry_px <= 0:
            at_mask = (norm <= snap).to_numpy() if hasattr(norm <= snap, "to_numpy") else (norm <= snap)
            at = close[at_mask]
            if len(at) == 0:
                continue
            entry_px = float(at[-1])
        df.at[i, "fwd"] = float(after[horizon - 1]) / entry_px - 1.0
    df.to_parquet(_SNAP)
    return df


def warehouse_stats() -> dict:
    if not _SNAP.exists():
        return {"rows": 0}
    df = pd.read_parquet(_SNAP)
    return {"rows": len(df), "dates": df["date"].nunique(),
            "tickers": df["ticker"].nunique(),
            "labeled": int(df["fwd"].notna().sum())}


def _demo() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sample = ["NVDA", "AMD", "AAPL", "JPM", "LLY", "KO", "XOM", "PWR", "ANET", "FFBC", "NVEC"]
    price_cache.refresh_prices(sample + ["SPY"])
    n = collect_snapshot(sample)
    print(f"collected {n} feature rows")
    label_warehouse()
    print("warehouse stats:", warehouse_stats())


if __name__ == "__main__":
    _demo()
