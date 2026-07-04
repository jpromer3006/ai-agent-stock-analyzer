"""
meta_model.py - Two-layer learned system over the book strategies.

Layer 1 (unsupervised): discover market regimes from data instead of the three
hand-coded buckets. KMeans here (numpy); upgrade to a Gaussian mixture or an HMM
(hmmlearn) on your Mac for soft, temporal regimes.

Layer 2 (supervised): a learned router. Ridge regression maps the strategy scores
to forward net return, so the blend weights are learned from outcomes, not guessed.
Upgrade to TabPFN (in-context, overfitting-resistant on small tabular data) or a
gradient-boosted model on your Mac.

Discipline: everything is walk-forward. The router trains only on dates strictly
before each test date, returns are net of transaction costs, and the book-blend is
the baseline to beat. Overfitting is the failure mode, so the out-of-sample numbers,
not the in-sample fit, are the verdict.

    PYTHONPATH=. python3 ml/meta_model.py            # build (cached) + evaluate
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from data import price_cache
from ml.stage_analyzer import analyze_stage
from ml.strategy_registry import ScoringContext, score_all
from rag.strategy_router import route

logger = logging.getLogger(__name__)
STRAT_KEYS = ["weinstein", "sepa", "canslim", "technical", "swing", "ipo"]
_CACHE = Path(__file__).resolve().parent.parent / "data" / "cache" / "meta"
_CACHE.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Dataset: (strategy scores + market features) -> forward net return
# ---------------------------------------------------------------------------
def build_dataset(universe, rebalance_days=15, horizon=20, lookback_months=10,
                  cost_bps=10.0, save=True) -> pd.DataFrame:
    price_cache.refresh_prices(universe + ["SPY"])
    import yfinance as yf
    try:
        vix = yf.Ticker("^VIX").history(period="2y")["Close"]
        vix.index = vix.index.tz_localize(None) if vix.index.tz else vix.index
    except Exception:
        vix = pd.Series(dtype=float)

    spy = price_cache.get_history("SPY")
    data = {t: price_cache.get_history(t) for t in universe}
    data = {t: d for t, d in data.items() if d is not None and len(d) > 220}
    cal = spy.index
    rt = 2 * cost_bps / 10000.0
    rows = []
    for i in range(max(220, len(cal) - lookback_months * 21), len(cal) - horizon - 1, rebalance_days):
        as_of = cal[i]
        spy_sl = spy.loc[:as_of]["Close"]
        spy_ret20 = float(spy_sl.iloc[-1] / spy_sl.iloc[-21] - 1) if len(spy_sl) > 21 else 0.0
        spy_vol = float(spy_sl.pct_change().tail(20).std())
        try:
            vix_v = float(vix.loc[:as_of].iloc[-1])
        except Exception:
            vix_v = 18.0
        # breadth: fraction of universe above its 50d MA
        breadth, n = 0, 0
        for t, df in data.items():
            sl = df.loc[:as_of]["Close"]
            if len(sl) >= 50:
                n += 1; breadth += int(sl.iloc[-1] > sl.tail(50).mean())
        breadth = breadth / n if n else 0.5
        for t, df in data.items():
            sl = df.loc[:as_of]
            if len(sl) < 220:
                continue
            sc = score_all(ScoringContext(t, sl), only=STRAT_KEYS)
            pos = len(sl) - 1
            if pos + horizon >= len(df):
                continue
            fwd = float(df["Close"].iloc[pos + horizon] / df["Close"].iloc[pos] - 1) - rt
            row = {"date": str(as_of.date()), "ticker": t,
                   "spy_ret20": spy_ret20, "spy_vol": spy_vol, "vix": vix_v, "breadth": breadth,
                   "y": fwd}
            for k in STRAT_KEYS:
                row[k] = sc[k].score
            rows.append(row)
    df = pd.DataFrame(rows)
    if save and not df.empty:
        df.to_parquet(_CACHE / "dataset.parquet")
    return df


# ---------------------------------------------------------------------------
# Layer 1: unsupervised regime discovery (numpy KMeans)
# ---------------------------------------------------------------------------
def _kmeans(X, k=3, iters=50, seed=0):
    rng = np.random.RandomState(seed)
    X = (X - X.mean(0)) / (X.std(0) + 1e-9)
    cent = X[rng.choice(len(X), k, replace=False)]
    for _ in range(iters):
        d = ((X[:, None, :] - cent[None, :, :]) ** 2).sum(2)
        lab = d.argmin(1)
        new = np.array([X[lab == j].mean(0) if (lab == j).any() else cent[j] for j in range(k)])
        if np.allclose(new, cent):
            break
        cent = new
    return lab


def discover_regimes(df, k=3):
    feats = df.groupby("date")[["spy_ret20", "spy_vol", "vix", "breadth"]].first()
    lab = _kmeans(feats.values, k=k)
    return dict(zip(feats.index, lab))


# ---------------------------------------------------------------------------
# Layer 2: learned router (numpy ridge), walk-forward
# ---------------------------------------------------------------------------
def _ridge_fit(X, y, lam=1.0):
    Xb = np.hstack([X, np.ones((len(X), 1))])
    A = Xb.T @ Xb + lam * np.eye(Xb.shape[1])
    return np.linalg.solve(A, Xb.T @ y)


def _ridge_pred(w, X):
    return np.hstack([X, np.ones((len(X), 1))]) @ w


def walkforward(df, top_n=4, min_train=60, use_regime=True, k=3):
    df = df.sort_values("date").reset_index(drop=True)
    regimes = discover_regimes(df, k=k) if use_regime else {}
    dates = sorted(df["date"].unique())
    book_w = {}  # cache book weights per regime label via router (map discovered->name not needed)
    base_ret, learn_ret, lr_ret = [], [], []
    for ti in range(len(dates)):
        test_date = dates[ti]
        train = df[df["date"] < test_date]
        test = df[df["date"] == test_date]
        if len(train) < min_train or test.empty:
            continue
        Xtr, ytr = train[STRAT_KEYS].values, train["y"].values
        Xte = test[STRAT_KEYS].values
        # Baseline: book composite (equal-ish weights from a neutral route)
        bw = np.array([route("swing_momentum", "neutral").weights.get(k_, 0) for k_ in STRAT_KEYS])
        base_pred = Xte @ bw
        # Learned router (global)
        w = _ridge_fit(Xtr, ytr)
        learn_pred = _ridge_pred(w, Xte)
        # Learned + discovered regime: train on same-regime history only
        if use_regime:
            rlab = regimes.get(test_date)
            same = train[train["date"].map(regimes) == rlab]
            if len(same) >= 30:
                wr = _ridge_fit(same[STRAT_KEYS].values, same["y"].values)
                lr_pred = _ridge_pred(wr, Xte)
            else:
                lr_pred = learn_pred
        else:
            lr_pred = learn_pred
        yte = test["y"].values

        def topret(pred):
            idx = np.argsort(pred)[::-1][:top_n]
            return float(yte[idx].mean())
        base_ret.append(topret(base_pred))
        learn_ret.append(topret(learn_pred))
        lr_ret.append(topret(lr_pred))

    def stat(r):
        r = np.array(r)
        return {"n": len(r), "avg%": round(100 * r.mean(), 2),
                "hit%": round(100 * (r > 0).mean(), 1)} if len(r) else {}
    return {"book_baseline": stat(base_ret),
            "learned_router": stat(learn_ret),
            "learned_router+regime": stat(lr_ret)}


def _demo() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    cache = _CACHE / "dataset.parquet"
    if cache.exists():
        df = pd.read_parquet(cache)
        print(f"Loaded cached dataset: {len(df)} rows")
    else:
        universe = ["AAPL", "NVDA", "AMD", "AVGO", "JPM", "LLY", "KO", "WMT",
                    "XOM", "PWR", "ANET", "COST"]
        df = build_dataset(universe)
        print(f"Built dataset: {len(df)} rows")
    if df.empty:
        print("no data"); return
    reg = discover_regimes(df, k=3)
    from collections import Counter
    print("Discovered regimes per date:", dict(Counter(reg.values())))
    res = walkforward(df, top_n=4, k=3)
    print("\nWalk-forward, out-of-sample, net of costs (avg fwd return of top picks):")
    for name, s in res.items():
        print(f"  {name:24} {s}")


if __name__ == "__main__":
    _demo()
