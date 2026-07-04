"""
meta_features.py - Quant-grade feature engineering and evaluation.

Built to survive scrutiny. Three upgrades over the first meta-model:

1. Classic cross-sectional factors alongside the book-strategy scores: 12-1 month
   momentum (Jegadeesh-Titman), 60-day realized volatility, beta to SPY, and
   dollar-volume liquidity. These are the academically standard return predictors.
2. Cross-sectional standardization: features are z-scored WITHIN each date, so the
   model ranks names against their peers that day, not against history. This is how
   quant signals are actually used and it removes time-trend bias.
3. Information Coefficient evaluation: the rank correlation between predicted and
   realized forward EXCESS return (return minus the cross-sectional mean, i.e.
   market-neutral). Mean IC, its t-stat, and the long-short top-minus-bottom spread
   are the metrics a quant expects, not just "average of the top N".

Macro features (FRED): yield-curve slope (DGS10-DGS2) and high-yield credit spread
are per-date regime conditioners, not cross-sectional signals, so they feed the
regime layer rather than the stock ranking.

Point-in-time and walk-forward throughout. Survivorship bias (current-listing
universe) is the main remaining caveat and must be stated.

    PYTHONPATH=. python3 ml/meta_features.py
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

from data import price_cache
from ml.strategy_registry import ScoringContext, score_all

logger = logging.getLogger(__name__)
STRAT_KEYS = ["weinstein", "sepa", "canslim", "technical", "swing", "ipo"]
FACTOR_KEYS = ["mom121", "vol60", "beta", "liq"]
FEATURE_KEYS = STRAT_KEYS + FACTOR_KEYS
_FRED = Path(__file__).resolve().parent.parent / "data" / "cache" / "fred"
_FRED.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Macro (FRED)
# ---------------------------------------------------------------------------
def fred_series(series_id: str) -> pd.Series:
    cache = _FRED / f"{series_id}.json"
    if cache.exists():
        obs = json.loads(cache.read_text())
    else:
        key = os.environ.get("FRED_API_KEY", "")
        url = (f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}"
               f"&api_key={key}&file_type=json")
        try:
            obs = json.loads(urllib.request.urlopen(url, timeout=20).read())["observations"]
            cache.write_text(json.dumps(obs))
        except Exception as e:  # noqa: BLE001
            logger.warning("FRED %s failed: %s", series_id, e)
            return pd.Series(dtype=float)
    s = pd.Series({o["date"]: (float(o["value"]) if o["value"] not in (".", "") else np.nan)
                   for o in obs}).dropna()
    s.index = pd.to_datetime(s.index)
    return s


def macro_at(as_of, dgs10, dgs2, hy):
    def look(s):
        try:
            return float(s.loc[:as_of].iloc[-1])
        except Exception:
            return np.nan
    return {"yield_slope": look(dgs10) - look(dgs2), "credit_spread": look(hy)}


# ---------------------------------------------------------------------------
# Per-stock factors
# ---------------------------------------------------------------------------
def _factors(df: pd.DataFrame, spy: pd.Series) -> dict:
    c = df["Close"]
    out = {"mom121": np.nan, "vol60": np.nan, "beta": np.nan, "liq": np.nan}
    if len(c) > 252:
        out["mom121"] = float(c.iloc[-21] / c.iloc[-252] - 1)     # 12-1 momentum
    if len(c) > 60:
        out["vol60"] = float(c.pct_change().tail(60).std())
        out["liq"] = float(np.log((c * df["Volume"]).tail(20).mean() + 1))
    r = c.pct_change().tail(120)
    sr = spy.pct_change().reindex(r.index)
    j = pd.concat([r, sr], axis=1).dropna()
    if len(j) > 30 and j.iloc[:, 1].var() > 0:
        out["beta"] = float(np.cov(j.iloc[:, 0], j.iloc[:, 1])[0, 1] / j.iloc[:, 1].var())
    return out


def build_quant_dataset(universe, rebalance_days=15, horizon=20, lookback_months=10) -> pd.DataFrame:
    price_cache.refresh_prices(universe + ["SPY"])
    dgs10, dgs2, hy = fred_series("DGS10"), fred_series("DGS2"), fred_series("BAMLH0A0HYM2")
    spy = price_cache.get_history("SPY")
    spyc = spy["Close"]; spyc.index = spyc.index.tz_localize(None) if spyc.index.tz else spyc.index
    data = {t: price_cache.get_history(t) for t in universe}
    data = {t: d for t, d in data.items() if d is not None and len(d) > 260}
    cal = spy.index
    rows = []
    for i in range(max(260, len(cal) - lookback_months * 21), len(cal) - horizon - 1, rebalance_days):
        as_of = cal[i]; aod = as_of.tz_localize(None) if as_of.tz else as_of
        mac = macro_at(aod, dgs10, dgs2, hy)
        for t, df in data.items():
            sl = df.loc[:as_of]
            if len(sl) < 260:
                continue
            sc = score_all(ScoringContext(t, sl), only=STRAT_KEYS)
            fac = _factors(sl, spyc.loc[:aod])
            pos = len(sl) - 1
            if pos + horizon >= len(df):
                continue
            fwd = float(df["Close"].iloc[pos + horizon] / df["Close"].iloc[pos] - 1)
            row = {"date": str(as_of.date()), "ticker": t, "fwd": fwd, **mac}
            row.update({k: sc[k].score for k in STRAT_KEYS})
            row.update(fac)
            rows.append(row)
    df = pd.DataFrame(rows)
    # forward EXCESS return = market-neutral label
    df["excess"] = df["fwd"] - df.groupby("date")["fwd"].transform("mean")
    if not df.empty:
        out = Path(__file__).resolve().parent.parent / "data" / "cache" / "meta"
        out.mkdir(parents=True, exist_ok=True)
        df.to_parquet(out / "quant_dataset.parquet")
    return df


# ---------------------------------------------------------------------------
# Cross-sectional z-score + IC evaluation
# ---------------------------------------------------------------------------
def _xs_z(g: pd.DataFrame, cols):
    z = g.copy()
    for c in cols:
        v = g[c].astype(float)
        z[c] = (v - v.mean()) / (v.std() + 1e-9)
    return z.fillna(0.0)


def _ridge(X, y, lam=1.0):
    Xb = np.hstack([X, np.ones((len(X), 1))])
    return np.linalg.solve(Xb.T @ Xb + lam * np.eye(Xb.shape[1]), Xb.T @ y)


def _spearman(a, b):
    if len(a) < 3:
        return np.nan
    ra, rb = pd.Series(a).rank().values, pd.Series(b).rank().values
    if np.std(ra) == 0 or np.std(rb) == 0:
        return np.nan
    return float(np.corrcoef(ra, rb)[0, 1])


def ic_walkforward(df, feature_cols=FEATURE_KEYS, min_train=80):
    df = df.sort_values("date").reset_index(drop=True)
    # cross-sectional z per date (manual to avoid pandas apply-on-grouping deprecation)
    df = pd.concat([_xs_z(g, feature_cols) for _, g in df.groupby("date")]).reset_index(drop=True)
    dates = sorted(df["date"].unique())
    ic_book, ic_learn = [], []
    ls_book, ls_learn = [], []
    book_w = np.array([1, 1, 1, 1, 1, 1, 0.5, -0.3, -0.2, 0.3][:len(feature_cols)], float)
    for d in dates:
        train, test = df[df["date"] < d], df[df["date"] == d]
        if len(train) < min_train or len(test) < 4:
            continue
        Xte, yte = test[feature_cols].values, test["excess"].values
        book_pred = Xte @ book_w[:Xte.shape[1]]
        w = _ridge(train[feature_cols].values, train["excess"].values)
        learn_pred = (np.hstack([Xte, np.ones((len(Xte), 1))]) @ w)
        ic_book.append(_spearman(book_pred, yte))
        ic_learn.append(_spearman(learn_pred, yte))

        def ls(pred):
            n = max(1, len(pred) // 3)
            order = np.argsort(pred)
            return float(yte[order[-n:]].mean() - yte[order[:n]].mean())
        ls_book.append(ls(book_pred)); ls_learn.append(ls(learn_pred))

    def rep(ics, ls):
        ics = np.array([x for x in ics if not np.isnan(x)])
        if len(ics) == 0:
            return {}
        t = ics.mean() / (ics.std() + 1e-9) * np.sqrt(len(ics))
        return {"mean_IC": round(float(ics.mean()), 3), "IC_t": round(float(t), 2),
                "n": len(ics), "LS_spread%": round(100 * float(np.mean(ls)), 2)}
    return {"book_signal": rep(ic_book, ls_book), "learned": rep(ic_learn, ls_learn)}


def _demo() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    universe = ["AAPL", "NVDA", "AMD", "AVGO", "JPM", "LLY", "KO", "WMT",
                "XOM", "PWR", "ANET", "COST", "MSFT", "HD"]
    df = build_quant_dataset(universe)
    print(f"Dataset: {len(df)} rows, {df['date'].nunique()} dates, {df['ticker'].nunique()} names")
    print("Macro sample:", df[["yield_slope", "credit_spread"]].iloc[-1].to_dict())
    res = ic_walkforward(df)
    print("\nInformation Coefficient (rank corr of signal vs forward EXCESS return),")
    print("walk-forward, cross-sectionally standardized. |IC|>0.03 and IC_t>2 is meaningful:")
    for name, s in res.items():
        print(f"  {name:14} {s}")


if __name__ == "__main__":
    _demo()
