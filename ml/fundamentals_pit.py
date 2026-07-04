"""
fundamentals_pit.py - Point-in-time fundamentals from SEC XBRL.

yfinance .info gives only TODAY's P/E and P/B, which leaks the future in a backtest.
SEC XBRL company facts carry a `filed` date on every value, so we can ask "what was
the latest reported EPS / book value that a filer had PUBLISHED on or before date X"
and compute an honest as-of P/E and P/B. No lookahead.

Reuses data.sec_client.get_company_facts (cached). For live scoring pass as_of=None
to take the latest filing.

    PYTHONPATH=. python3 ml/fundamentals_pit.py
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from data import sec_client

logger = logging.getLogger(__name__)

EPS_CONCEPTS = ["EarningsPerShareDiluted", "EarningsPerShareBasic"]
EQUITY_CONCEPTS = ["StockholdersEquity",
                   "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]
SHARES_CONCEPTS = ["CommonStockSharesOutstanding",
                   "WeightedAverageNumberOfDilutedSharesOutstanding"]


def _latest(facts: dict, concepts: list[str], as_of: Optional[date],
            only_annual: bool = False, ttm: bool = False):
    """Most recent reported value with filed <= as_of (no lookahead).

    ttm=True sums the last four distinct quarterly values (for EPS TTM).
    """
    gaap = facts.get("facts", {}).get("us-gaap", {})
    cutoff = as_of.isoformat() if as_of else None
    for c in concepts:
        node = gaap.get(c)
        if not node:
            continue
        items = []
        for unit, arr in node.get("units", {}).items():
            for it in arr:
                filed, end, val = it.get("filed"), it.get("end"), it.get("val")
                if filed is None or end is None or val is None:
                    continue
                if cutoff and filed > cutoff:
                    continue
                if only_annual and it.get("form") != "10-K":
                    continue
                items.append((end, filed, float(val), it.get("form"), it.get("fp")))
        if not items:
            continue
        items.sort()                                   # by period end ascending
        if ttm:
            q = [x for x in items if x[4] and x[4].startswith("Q")]
            if len(q) >= 4:
                # last 4 distinct quarter-ends
                seen, picked = set(), []
                for end, filed, val, form, fp in reversed(items):
                    if end in seen:
                        continue
                    seen.add(end); picked.append(val)
                    if len(picked) == 4:
                        return sum(picked)
            # fall through to latest single value
        return items[-1][2]
    return None


def get_pit_fundamentals(ticker: str, as_of: Optional[date] = None) -> dict:
    facts = sec_client.get_company_facts(ticker)
    if not facts:
        return {}
    eps = _latest(facts, EPS_CONCEPTS, as_of, only_annual=True)
    equity = _latest(facts, EQUITY_CONCEPTS, as_of)
    shares = _latest(facts, SHARES_CONCEPTS, as_of)
    out = {"eps_annual": eps, "equity": equity, "shares": shares}
    if equity and shares and shares > 0:
        out["bvps"] = equity / shares
    return out


def pit_pe_pb(ticker: str, price: float, as_of: Optional[date] = None):
    """Return (pe, pb, raw) using fundamentals known as of `as_of`."""
    f = get_pit_fundamentals(ticker, as_of)
    pe = price / f["eps_annual"] if f.get("eps_annual") and f["eps_annual"] > 0 else None
    pb = price / f["bvps"] if f.get("bvps") and f["bvps"] > 0 else None
    return pe, pb, f


def _selftest() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    from data import price_cache
    price_cache.refresh_prices(["AAPL", "NVDA", "KO"])
    for t in ["AAPL", "NVDA", "KO"]:
        df = price_cache.get_history(t)
        for asof in [date(2025, 6, 16), None]:
            price = float(df.loc[:asof.isoformat()]["Close"].iloc[-1]) if asof else float(df["Close"].iloc[-1])
            pe, pb, f = pit_pe_pb(t, price, asof)
            label = asof.isoformat() if asof else "today"
            pe_s = f"{pe:.1f}" if pe else "n/a"
            pb_s = f"{pb:.1f}" if pb else "n/a"
            print(f"{t:5} as of {label:10} price={price:7.2f}  P/E={pe_s:>6}  P/B={pb_s:>6}  "
                  f"(eps={f.get('eps_annual')}, bvps={round(f['bvps'],2) if f.get('bvps') else None})")


if __name__ == "__main__":
    _selftest()
