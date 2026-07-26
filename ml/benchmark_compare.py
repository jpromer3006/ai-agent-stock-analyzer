"""
benchmark_compare.py - Portfolio vs index fund benchmark comparison.

Answers the honest question every constructed book must face: would simply
buying an index fund have done as well? Builds a hypothetical growth-of-$10k
curve for the CURRENT portfolio weights held over a past window, next to the
same $10k in each chosen benchmark (SPY, QQQ, VTI, or any ETF/mutual fund
yfinance knows). The cash sleeve is held flat at 0% return, which is slightly
conservative but transparent.

This is a backtest of today's weights, not the journaled forward track record
(ml/portfolio_journal.py remains the honest out-of-sample scoreboard). Mutual
funds price once daily at NAV with no volume; that is fine here because this
module only needs closes.

    PYTHONPATH=. python3 ml/benchmark_compare.py
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

logger = logging.getLogger(__name__)

START_VALUE = 100_000.0   # match the $100,000 books everywhere on the desk

# Benchmarks offered by default in the UI. Users can add any ticker on top.
DEFAULT_BENCHMARKS = ["SPY", "QQQ"]
SUGGESTED_BENCHMARKS = ["SPY", "QQQ", "VTI", "IWM", "DIA", "VTSAX", "FXAIX", "VOO"]


@dataclass
class ComparisonResult:
    curves: pd.DataFrame                 # one column per series, growth of $10k
    stats: pd.DataFrame                  # total return, CAGR, max DD, beta vs first benchmark
    missing: list[str] = field(default_factory=list)   # tickers with no usable history
    note: str = ""


def _closes(ticker: str, lookback_days: int) -> pd.Series | None:
    """Daily closes for `ticker` from the local cache, fetching on miss."""
    from data import price_cache
    df = price_cache.get_history(ticker)
    if df is None or df.empty:
        price_cache.refresh_prices([ticker])
        df = price_cache.get_history(ticker)
    if df is None or df.empty or "Close" not in df.columns:
        return None
    s = df["Close"].dropna()
    if s.empty:
        return None
    # Normalize the index to dates so cache (tz-aware NY) and any fresh
    # frames align on the same calendar.
    s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
    s = s[~s.index.duplicated(keep="last")]
    return s.tail(lookback_days)


def _max_drawdown(curve: pd.Series) -> float:
    peak = curve.cummax()
    return float((curve / peak - 1.0).min())


def build_comparison(
    holdings: list[tuple[str, float]],
    benchmarks: list[str],
    lookback_days: int = 252,
    cash_weight: float = 0.0,
) -> ComparisonResult:
    """Build normalized growth-of-$10k curves for the portfolio and benchmarks.

    holdings: [(ticker, weight)] where weights are fractions of the account
              (0.08 = 8%). They need not sum to 1; `cash_weight` covers the rest
              and earns 0%.
    """
    missing: list[str] = []

    # ---- Per-holding daily return series -------------------------------
    rets: dict[str, pd.Series] = {}
    weights: dict[str, float] = {}
    for tkr, w in holdings:
        s = _closes(tkr.upper(), lookback_days)
        if s is None or len(s) < 30:
            missing.append(tkr.upper())
            continue
        rets[tkr.upper()] = s.pct_change().fillna(0.0)
        weights[tkr.upper()] = float(w)

    curves: dict[str, pd.Series] = {}
    if rets:
        rdf = pd.DataFrame(rets).dropna(how="all").fillna(0.0)
        w_total = sum(weights.values())
        # Renormalize the equity sleeve, then scale by invested fraction so the
        # cash sleeve drags exactly as much as it does in the real book.
        invested_frac = min(1.0, max(0.0, w_total)) if cash_weight else 1.0
        if w_total > 0:
            port_ret = sum((weights[c] / w_total) * rdf[c] for c in rdf.columns)
            eff = invested_frac / (invested_frac + cash_weight) if (invested_frac + cash_weight) > 0 else 1.0
            port_ret = port_ret * eff
            curves["Portfolio"] = START_VALUE * (1.0 + port_ret).cumprod()

    # ---- Benchmarks ------------------------------------------------------
    for b in dict.fromkeys(t.upper() for t in benchmarks):
        s = _closes(b, lookback_days)
        if s is None or len(s) < 30:
            missing.append(b)
            continue
        curves[b] = START_VALUE * (1.0 + s.pct_change().fillna(0.0)).cumprod()

    if not curves:
        return ComparisonResult(pd.DataFrame(), pd.DataFrame(), missing,
                                "No usable price history for any series.")

    cdf = pd.DataFrame(curves).dropna(how="any")
    if cdf.empty or len(cdf) < 10:
        # Fall back to a looser join when a young book shortens the overlap.
        cdf = pd.DataFrame(curves).dropna(how="all").ffill().dropna()
    # Re-anchor every series to the common first day so the race starts even.
    cdf = cdf / cdf.iloc[0] * START_VALUE

    # ---- Stats -----------------------------------------------------------
    ref = next((c for c in cdf.columns if c != "Portfolio"), cdf.columns[0])
    ref_ret = cdf[ref].pct_change().dropna()
    rows = []
    years = max(len(cdf) / 252.0, 1e-9)
    for col in cdf.columns:
        curve = cdf[col]
        total = float(curve.iloc[-1] / curve.iloc[0] - 1.0)
        cagr = float((curve.iloc[-1] / curve.iloc[0]) ** (1.0 / years) - 1.0)
        r = curve.pct_change().dropna()
        beta = float(r.cov(ref_ret) / ref_ret.var()) if col != ref and ref_ret.var() > 0 else 1.0
        rows.append({
            "Series": col,
            "Total return %": round(100 * total, 2),
            "CAGR %": round(100 * cagr, 2),
            "Max drawdown %": round(100 * _max_drawdown(curve), 2),
            f"Beta vs {ref}": round(beta, 2),
            "Ends at $": round(float(curve.iloc[-1])),
        })
    stats = pd.DataFrame(rows)

    note = (f"Hypothetical: today's weights held for the whole window; cash earns 0%. "
            f"{len(cdf)} common trading days.")
    return ComparisonResult(cdf, stats, missing, note)


def _demo() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    res = build_comparison(
        holdings=[("NVDA", 0.10), ("AAPL", 0.10), ("JPM", 0.08), ("XOM", 0.07)],
        benchmarks=DEFAULT_BENCHMARKS + ["VTSAX"],
        lookback_days=252,
        cash_weight=0.65,
    )
    print(res.note)
    if res.missing:
        print("missing:", res.missing)
    print(res.stats.to_string(index=False))


if __name__ == "__main__":
    _demo()
