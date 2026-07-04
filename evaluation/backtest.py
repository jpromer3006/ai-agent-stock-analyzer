"""
backtest.py - Point-in-time backtest of the book-equal strategy engine.

Methodology (honest, no lookahead):
  * Roll an "as of" date across the period on the SPY trading calendar.
  * At each date, slice every ticker's price history to <= as_of and score it
    with the registry's price strategies (Weinstein, SEPA, CANSLIM, technical,
    swing, IPO). Fundamentals (Graham/Lynch) are excluded because the only source
    is current-snapshot and would leak the future.
  * Determine the regime (sliced SPY stage + VIX) and route -> per-strategy weights.
  * Select the top N by the regime-blended composite, recording which book LED.
  * Hold each pick `horizon` trading days; forward return uses only future bars.
  * Aggregate hit rate and average return overall, per leading strategy, and per
    regime, against SPY over the same windows.

Caveats this does NOT remove: survivorship bias (universe = current listings),
no transaction costs/slippage, no news layer (a live overlay, not backtested),
and small samples are noise. Educational, not financial advice.

    PYTHONPATH=. python3 evaluation/backtest.py
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from data import price_cache
from ml.stage_analyzer import analyze_stage
from ml.strategy_registry import ScoringContext, score_all
from rag.strategy_router import route

logger = logging.getLogger(__name__)
PRICE_KEYS = ["weinstein", "sepa", "canslim", "technical", "swing", "ipo"]


@dataclass
class Trade:
    as_of: str
    ticker: str
    led_by: str
    regime: str
    composite: float
    fwd_return: float


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    spy_returns: list[float] = field(default_factory=list)
    cost_bps: float = 0.0

    def _agg(self, rows: list[Trade]) -> dict:
        if not rows:
            return {"n": 0, "avg": 0.0, "hit": 0.0, "med": 0.0}
        rets = [t.fwd_return for t in rows]
        return {"n": len(rows),
                "avg": round(100 * statistics.mean(rets), 2),
                "hit": round(100 * sum(1 for r in rets if r > 0) / len(rets), 1),
                "med": round(100 * statistics.median(rets), 2)}

    def summary(self) -> str:
        L = ["=" * 64, "BACKTEST SUMMARY", "=" * 64]
        o = self._agg(self.trades)
        spy_avg = round(100 * statistics.mean(self.spy_returns), 2) if self.spy_returns else 0.0
        L.append(f"Trades: {o['n']}   Avg fwd return: {o['avg']}%   "
                 f"Hit rate: {o['hit']}%   Median: {o['med']}%")
        L.append(f"SPY over same windows: {spy_avg}%   "
                 f"Edge vs SPY: {round(o['avg'] - spy_avg, 2)}%")
        L.append(f"(returns NET of {self.cost_bps:.0f}bps one-way "
                 f"= {2*self.cost_bps:.0f}bps round-trip; SPY is buy-and-hold)")
        L.append("\nBy LEADING strategy (does each book earn its weight?):")
        L.append(f"  {'strategy':12}{'n':>5}{'avg%':>8}{'hit%':>7}{'med%':>8}")
        keys = sorted({t.led_by for t in self.trades})
        for k in keys:
            a = self._agg([t for t in self.trades if t.led_by == k])
            L.append(f"  {k:12}{a['n']:>5}{a['avg']:>8}{a['hit']:>7}{a['med']:>8}")
        L.append("\nBy REGIME:")
        for k in sorted({t.regime for t in self.trades}):
            a = self._agg([t for t in self.trades if t.regime == k])
            L.append(f"  {k:12}{a['n']:>5}{a['avg']:>8}{a['hit']:>7}{a['med']:>8}")
        L.append("=" * 64)
        return "\n".join(L)


def _regime_at(spy_full: pd.DataFrame, vix: pd.Series, as_of: pd.Timestamp) -> str:
    spy_sliced = spy_full.loc[:as_of]
    if len(spy_sliced) < 150:
        return "neutral"
    stage = analyze_stage("SPY", df=spy_sliced, use_cache=False).stage
    base = {2: "bull", 4: "bear"}.get(stage, "neutral")
    try:
        v = float(vix.loc[:as_of].iloc[-1])
        if v > 22 and base == "bull":
            base = "neutral"
    except Exception:
        pass
    return base


def _fwd_return(df: pd.DataFrame, as_of: pd.Timestamp, horizon: int) -> Optional[float]:
    sliced = df.loc[:as_of]
    if sliced.empty:
        return None
    pos = len(sliced) - 1
    if pos + horizon >= len(df):
        return None
    p0 = float(df["Close"].iloc[pos])
    p1 = float(df["Close"].iloc[pos + horizon])
    return p1 / p0 - 1.0 if p0 > 0 else None


def backtest(universe: list[str], rebalance_days: int = 10, horizon: int = 20,
             top_n: int = 5, mandate: str = "swing_momentum",
             lookback_months: int = 12, cost_bps: float = 10.0) -> BacktestResult:
    """cost_bps = one-way cost (commission + slippage) in basis points. Each pick is
    entered and exited, so a round trip of 2*cost_bps is deducted from its return.
    SPY is the buy-and-hold baseline (no per-trade cost)."""
    round_trip = 2 * cost_bps / 10000.0
    price_cache.refresh_prices(universe + ["SPY"])
    import yfinance as yf
    try:
        vix = yf.Ticker("^VIX").history(period="2y")["Close"]
        vix.index = vix.index.tz_localize(None) if vix.index.tz else vix.index
    except Exception:
        vix = pd.Series(dtype=float)

    spy = price_cache.get_history("SPY")
    if spy is None:
        raise RuntimeError("no SPY data")
    cal = spy.index
    start_pos = max(200, len(cal) - lookback_months * 21)
    end_pos = len(cal) - horizon - 1            # need horizon bars of future
    schedule = list(range(start_pos, end_pos, rebalance_days))

    data = {t: price_cache.get_history(t) for t in universe}
    data = {t: d for t, d in data.items() if d is not None and len(d) > 220}

    res = BacktestResult(cost_bps=cost_bps)
    for i in schedule:
        as_of = cal[i]
        regime = _regime_at(spy, vix, as_of)
        w = route(mandate, regime).weights
        scored = []
        for t, df in data.items():
            sl = df.loc[:as_of]
            if len(sl) < 220:
                continue
            sc = score_all(ScoringContext(t, sl), only=PRICE_KEYS)
            elig = [k for k in PRICE_KEYS if sc[k].eligible]
            if not elig:
                continue
            wsum = sum(w.get(k, 0) for k in PRICE_KEYS) or 1
            comp = sum(w.get(k, 0) * sc[k].score for k in PRICE_KEYS) / wsum
            led = max(elig, key=lambda k: sc[k].score)
            scored.append((comp, t, led))
        scored.sort(reverse=True)
        for comp, t, led in scored[:top_n]:
            fr = _fwd_return(data[t], as_of, horizon)
            if fr is None:
                continue
            net = fr - round_trip                    # deduct transaction costs
            res.trades.append(Trade(str(as_of.date()), t, led, regime, round(comp, 3), net))
        spy_fr = _fwd_return(spy, as_of, horizon)
        if spy_fr is not None:
            res.spy_returns.append(spy_fr)
    return res


def _demo() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    universe = ["AAPL", "NVDA", "MSFT", "AMD", "AVGO", "JPM", "LLY", "KO", "WMT",
                "XOM", "PWR", "ANET", "VRT", "COST", "PG", "NEE", "CAT", "HD"]
    res = backtest(universe, rebalance_days=10, horizon=20, top_n=5, lookback_months=10)
    print(res.summary())


if __name__ == "__main__":
    _demo()
