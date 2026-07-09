"""
capstone_tracker.py - The professors' comparison (Fordham capstone, due 7/29).

Two simulated $100,000 books, measured honestly against each other and SPY:

  1. FIRST PORTFOLIO - the first cloud-built OROS book (2026-07-06), frozen.
     Buy-and-hold at the journaled entries. Its unrealized gains are REAL
     numbers that accrue daily (the answer to "Unreal% is always $0").
  2. LIVE OROS BOOK - the evolving daily portfolio. Because it rebuilds every
     run, its honest cumulative measure is the CHAIN of daily returns of
     whatever book was held each day (stored in capstone_chain.json, which
     the journal commit persists across cloud runners).
  3. SPY - the same $100,000 in the index fund, from the 07-06 close.

No hindsight, no cherry-picking: the frozen book cannot be edited, the chain
only ever appends, and all three lines start at the same dollar on the same
day. Educational, not financial advice.

    PYTHONPATH=. python3 ml/capstone_tracker.py
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_J = Path(__file__).resolve().parent.parent / "data" / "journal"
_BASELINE = _J / "capstone_baseline.json"
_CHAIN = _J / "capstone_chain.json"
END_DATE = "2026-07-29"


def _close_on_or_after(df, date: str):
    """First close on/after `date` (the baseline start uses the 07-06 close)."""
    import pandas as pd
    idx = pd.to_datetime(df.index)
    try:
        idx = idx.tz_localize(None)
    except TypeError:
        pass
    mask = idx.normalize() >= pd.Timestamp(date)
    sub = df.loc[mask]
    return float(sub["Close"].iloc[0]) if len(sub) else None


def baseline_status() -> dict | None:
    """Value the frozen first portfolio at current prices."""
    if not _BASELINE.exists():
        return None
    from data import price_cache
    b = json.loads(_BASELINE.read_text())
    rows, value = [], float(b.get("cash") or 0.0)
    for h in b["holdings"]:
        df = price_cache.get_history(h["ticker"])
        if df is None or "Close" not in df.columns:
            continue
        c = df["Close"].dropna()
        last, prev = float(c.iloc[-1]), float(c.iloc[-2]) if len(c) > 1 else float(c.iloc[-1])
        entry, sh = float(h["entry"]), float(h["shares"])
        value += sh * last
        rows.append({"ticker": h["ticker"], "shares": sh, "entry": entry,
                     "last": last,
                     "day_pct": 100 * (last / prev - 1),
                     "day_usd": sh * (last - prev),
                     "unreal_pct": 100 * (last / entry - 1),
                     "unreal_usd": sh * (last - entry)})
    rows.sort(key=lambda r: -r["unreal_usd"])
    acct = float(b.get("account") or 100_000.0)

    # SPY: the same $100k from the frozen book's start close.
    spy_value = None
    spy = price_cache.get_history("SPY")
    if spy is not None:
        start = _close_on_or_after(spy, b["start_date"])
        if start:
            spy_value = acct * float(spy["Close"].dropna().iloc[-1]) / start

    return {"name": b["name"], "start_date": b["start_date"], "end_date": END_DATE,
            "account": acct, "cash": float(b.get("cash") or 0.0),
            "rows": rows, "value": value,
            "return_pct": 100 * (value / acct - 1),
            "spy_value": spy_value,
            "spy_return_pct": (100 * (spy_value / acct - 1)) if spy_value else None}


def update_live_chain(day_return_frac: float) -> dict:
    """Append today's live-book daily return to the chain (idempotent per
    date: a re-run on the same day replaces that day's entry)."""
    chain = {"start_date": "2026-07-06", "dates": [], "day_returns": []}
    if _CHAIN.exists():
        try:
            chain = json.loads(_CHAIN.read_text())
        except Exception:
            pass
    today = f"{datetime.now():%Y-%m-%d}"
    if chain["dates"] and chain["dates"][-1] == today:
        chain["day_returns"][-1] = day_return_frac
    else:
        chain["dates"].append(today)
        chain["day_returns"].append(day_return_frac)
    _CHAIN.write_text(json.dumps(chain, indent=2))
    return chain


def live_chain_status(account: float = 100_000.0) -> dict | None:
    """Cumulative simulated value of the EVOLVING book: chain of daily returns."""
    if not _CHAIN.exists():
        return None
    try:
        chain = json.loads(_CHAIN.read_text())
    except Exception:
        return None
    v = account
    for r in chain.get("day_returns", []):
        v *= (1.0 + float(r))
    return {"value": v, "return_pct": 100 * (v / account - 1),
            "days": len(chain.get("dates", []))}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    s = baseline_status()
    if not s:
        print("no baseline frozen")
    else:
        print(f"{s['name']}  ({s['start_date']} -> {s['end_date']})")
        print(f"value ${s['value']:,.0f} ({s['return_pct']:+.2f}%)  "
              f"vs SPY ${s['spy_value']:,.0f} ({s['spy_return_pct']:+.2f}%)"
              if s.get("spy_value") else f"value ${s['value']:,.0f}")
        for r in s["rows"]:
            print(f"  {r['ticker']:6} unreal {r['unreal_pct']:+6.2f}%  ${r['unreal_usd']:+8,.0f}")
        lc = live_chain_status(s["account"])
        if lc:
            print(f"live book (chained): ${lc['value']:,.0f} ({lc['return_pct']:+.2f}%) over {lc['days']} day(s)")
