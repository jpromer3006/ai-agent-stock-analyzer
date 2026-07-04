"""
performance_tracker.py - Live performance for journaled portfolios.

The morning run journals a proposal per day (ml/portfolio_journal.py) with each
holding's entry price, weight, stop, and mandate. This module turns those
proposals into performance:

    * mark_book()        - one proposal valued at the latest cached close
    * current_book()     - the most recent proposal, marked to market
    * book_curve()       - account-level equity curve for a book vs SPY
    * track_record()     - every proposal as a paper trade: live return,
                           SPY over the same window, alpha, hit rate, and
                           per-mandate aggregates
    * build_report_xlsx()- a multi-sheet workbook the cron writes daily

Return convention: account-level. A holding contributes weight_pct/100 * its
return; the un-invested remainder is cash at 0%. SPY is the benchmark, measured
from each proposal's date to the latest cached close (same window as the book).

Educational, not financial advice. Prices come from the local price cache, so
figures are only as fresh as the last price refresh / morning run.
"""

from __future__ import annotations

import json
import glob
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
_JOURNAL = _ROOT / "data" / "journal"
_BENCH = "SPY"


# ───────────────────────────── loaders ─────────────────────────────────────
def _proposal_paths() -> list[Path]:
    return sorted(_JOURNAL.glob("portfolio_*.json"))


def _load(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def _history(ticker: str):
    """Cached daily OHLCV with a DatetimeIndex and a Close column, or None."""
    try:
        from data import price_cache
        df = price_cache.get_history(ticker)
        if df is None or df.empty or "Close" not in df.columns:
            return None
        return df
    except Exception:
        return None


def _latest_close(df) -> Optional[float]:
    try:
        return float(df["Close"].iloc[-1])
    except Exception:
        return None


def _close_asof(df, date_str: str) -> Optional[float]:
    """Close on the last bar at or before date_str."""
    try:
        sl = df.loc[:date_str]
        if sl.empty:
            return None
        return float(sl["Close"].iloc[-1])
    except Exception:
        return None


def _bench_return(prop_date: str) -> Optional[float]:
    """SPY total return from prop_date to the latest cached close."""
    df = _history(_BENCH)
    if df is None:
        return None
    start = _close_asof(df, prop_date)
    end = _latest_close(df)
    if not start or not end:
        return None
    return end / start - 1.0


# ───────────────────────────── marking ─────────────────────────────────────
def mark_book(rec: dict) -> dict:
    """Value one journaled proposal at the latest cached close.

    Returns the per-holding marks plus account-level book_return, the SPY
    benchmark over the same window, and alpha.
    """
    prop_date = rec.get("date", "")
    rows: list[dict] = []
    book_ret = 0.0          # account-level: sum(weight * holding_return)
    priced_w = 0.0
    for h in rec.get("holdings", []):
        t = h.get("ticker")
        entry = h.get("entry")
        df = _history(t)
        cur = _latest_close(df) if df is not None else None
        ret = (cur / entry - 1.0) if (cur and entry) else None
        w = (h.get("weight_pct") or 0.0) / 100.0
        stop = h.get("stop")
        stop_hit = bool(cur is not None and stop is not None and cur <= stop)
        if ret is not None:
            book_ret += w * ret
            priced_w += w
        rows.append({
            "ticker": t,
            "sector": h.get("sector"),
            "mandate_lead": h.get("led_by"),
            "weight_pct": round((h.get("weight_pct") or 0.0), 2),
            "entry": round(entry, 2) if entry else None,
            "current": round(cur, 2) if cur else None,
            "return_pct": round(100 * ret, 2) if ret is not None else None,
            "stop": round(stop, 2) if stop else None,
            "stop_hit": stop_hit,
        })
    bench = _bench_return(prop_date)
    invested = (rec.get("invested") or 0.0)
    account = (rec.get("account") or 0.0) or 1.0
    invested_frac = invested / account if account else priced_w
    return {
        "date": prop_date,
        "timestamp": rec.get("timestamp"),
        "mandate": rec.get("mandate", "?"),
        "regime": rec.get("regime"),
        "n_holdings": len(rec.get("holdings", [])),
        "invested_pct": round(100 * invested_frac, 1),
        "book_return_pct": round(100 * book_ret, 2),
        "bench_return_pct": round(100 * bench, 2) if bench is not None else None,
        "alpha_pct": round(100 * (book_ret - bench), 2) if bench is not None else None,
        "holdings": rows,
    }


def current_book() -> Optional[dict]:
    """The most recent journaled proposal, marked to the latest close."""
    paths = _proposal_paths()
    if not paths:
        return None
    return mark_book(_load(paths[-1]))


# ──────────────────────────── equity curve ─────────────────────────────────
def book_curve(rec: dict) -> Optional[pd.DataFrame]:
    """Account-level daily value index for one book vs SPY, from the book's
    date to the latest close. Both series start at 1.0.

    value_t = cash_frac + Σ w_i * (Close_i,t / entry_i)
    """
    start = rec.get("date", "")
    cols = []
    weights = []
    for h in rec.get("holdings", []):
        df = _history(h.get("ticker"))
        if df is None:
            continue
        sl = df.loc[start:]
        if sl.empty:
            continue
        entry = h.get("entry") or float(sl["Close"].iloc[0])
        growth = (sl["Close"] / entry).rename(h["ticker"])
        cols.append(growth)
        weights.append((h["ticker"], (h.get("weight_pct") or 0.0) / 100.0))
    if not cols:
        return None
    M = pd.concat(cols, axis=1).ffill()
    w = pd.Series({t: wt for t, wt in weights})
    invested = float(w.sum())
    cash_frac = max(0.0, 1.0 - invested)
    port_index = cash_frac + M.mul(w, axis=1).sum(axis=1)

    out = pd.DataFrame({"Portfolio": port_index})
    spy = _history(_BENCH)
    if spy is not None:
        s = spy.loc[start:]["Close"]
        if not s.empty:
            out["SPY"] = (s / float(s.iloc[0])).reindex(out.index).ffill()
    # Normalize index to plain dates for charting.
    out.index = pd.to_datetime(out.index).tz_localize(None)
    return out


# ──────────────────────────── track record ─────────────────────────────────
def track_record(live: bool = True) -> dict:
    """Every journaled proposal as a paper trade.

    live=True marks each proposal entry->latest close (so it includes still-open
    books). The stored realized_avg_return (fixed 20-day horizon) is also carried
    through for proposals that have it.
    """
    rows: list[dict] = []
    for p in _proposal_paths():
        try:
            rec = _load(p)
        except Exception:
            continue
        m = mark_book(rec) if live else None
        rows.append({
            "date": rec.get("date"),
            "timestamp": rec.get("timestamp", ""),
            "mandate": rec.get("mandate", "?"),
            "regime": rec.get("regime"),
            "n_holdings": len(rec.get("holdings", [])),
            "live_return_pct": m["book_return_pct"] if m else None,
            "bench_return_pct": m["bench_return_pct"] if m else None,
            "alpha_pct": m["alpha_pct"] if m else None,
            "realized_avg_pct": (round(100 * rec["realized_avg_return"], 2)
                                 if rec.get("realized_avg_return") is not None else None),
            "realized_hit_pct": (round(100 * rec["realized_hit_rate"], 1)
                                 if rec.get("realized_hit_rate") is not None else None),
            "file": Path(p).name,
        })
    df = pd.DataFrame(rows).sort_values("timestamp") if rows else pd.DataFrame()

    # Aggregates
    def _avg(col):
        s = df[col].dropna() if col in df else pd.Series(dtype=float)
        return round(float(s.mean()), 2) if len(s) else None

    live_series = df["live_return_pct"].dropna() if "live_return_pct" in df else pd.Series(dtype=float)
    hit = round(100 * float((live_series > 0).mean()), 1) if len(live_series) else None

    # Per-mandate
    by_mandate = {}
    if not df.empty:
        for m, g in df.groupby("mandate"):
            ls = g["live_return_pct"].dropna()
            by_mandate[m] = {
                "proposals": int(len(g)),
                "avg_live_pct": round(float(ls.mean()), 2) if len(ls) else None,
                "avg_bench_pct": _avg("bench_return_pct") if False else (
                    round(float(g["bench_return_pct"].dropna().mean()), 2)
                    if len(g["bench_return_pct"].dropna()) else None),
                "hit_pct": round(100 * float((ls > 0).mean()), 1) if len(ls) else None,
            }

    return {
        "rows": df,
        "summary": {
            "proposals": int(len(df)),
            "avg_live_pct": _avg("live_return_pct"),
            "avg_bench_pct": _avg("bench_return_pct"),
            "avg_alpha_pct": _avg("alpha_pct"),
            "hit_pct": hit,
            "avg_realized_pct": _avg("realized_avg_pct"),
        },
        "by_mandate": by_mandate,
    }


# ──────────────────────────── xlsx report ───────────────────────────────────
def build_report_xlsx(out_path: Optional[Path] = None) -> Optional[Path]:
    """Write a multi-sheet performance workbook. Returns the path (or None)."""
    cur = current_book()
    tr = track_record(live=True)
    if cur is None:
        return None
    if out_path is None:
        out_path = _JOURNAL / f"performance_{datetime.now():%Y-%m-%d}.xlsx"
    out_path = Path(out_path)
    try:
        with pd.ExcelWriter(out_path, engine="openpyxl") as xl:
            # Summary
            s = tr["summary"]
            pd.DataFrame([{
                "as_of": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "current_book_date": cur["date"],
                "current_mandate": cur["mandate"],
                "current_book_return_%": cur["book_return_pct"],
                "current_SPY_return_%": cur["bench_return_pct"],
                "current_alpha_%": cur["alpha_pct"],
                "track_proposals": s["proposals"],
                "track_avg_return_%": s["avg_live_pct"],
                "track_avg_SPY_%": s["avg_bench_pct"],
                "track_avg_alpha_%": s["avg_alpha_pct"],
                "track_hit_%": s["hit_pct"],
            }]).T.rename(columns={0: "value"}).to_excel(xl, sheet_name="Summary")
            # Current book holdings
            pd.DataFrame(cur["holdings"]).to_excel(xl, sheet_name="Current Book", index=False)
            # Track record
            if not tr["rows"].empty:
                tr["rows"].to_excel(xl, sheet_name="Track Record", index=False)
            # By mandate
            if tr["by_mandate"]:
                pd.DataFrame(tr["by_mandate"]).T.to_excel(xl, sheet_name="By Mandate")
            # Equity curve of the current book
            curve = book_curve(_load(_proposal_paths()[-1]))
            if curve is not None:
                curve.round(4).to_excel(xl, sheet_name="Equity Curve")
        return out_path
    except Exception:
        return None


if __name__ == "__main__":
    import pprint
    cb = current_book()
    print("CURRENT BOOK:", cb["date"] if cb else None,
          "ret%", cb["book_return_pct"] if cb else None,
          "spy%", cb["bench_return_pct"] if cb else None)
    tr = track_record()
    pprint.pp(tr["summary"])
    pprint.pp(tr["by_mandate"])
    rp = build_report_xlsx()
    print("report:", rp)
