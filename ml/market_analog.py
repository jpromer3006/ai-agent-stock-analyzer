"""
market_analog.py - "What does today look like, and what worked then?"

Realizes the analog idea: describe the current market as a STATE vector, find the
most similar past states, and see which book strategy actually ranked stocks best
in those analog periods. The output is interpretable, "today resembles these past
dates, and in them strategy X led", which is both a strategy-selection signal and
an explanation a reviewer can follow.

State vector per date (all backtestable): average 60-day volatility, breadth (share
of names in positive 12-1 momentum), yield-curve slope, and the high-yield credit
spread. At LIVE time the current state can be enriched with news sentiment (forward
only, since an LLM/news signal cannot be backtested without lookahead).

Per-date strategy quality = the Information Coefficient of each strategy's score vs
forward excess return that date. Analog recommendation = average those ICs over the
k nearest past states.

Caveats a reviewer will (rightly) raise: states are non-stationary (a VIX level in
2010 != 2026), analogs need years of history to be meaningful, and this must be
walk-forward validated (does analog-tilted weighting beat static weighting OOS?).

    PYTHONPATH=. python3 ml/market_analog.py
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from ml.meta_features import STRAT_KEYS, _spearman

logger = logging.getLogger(__name__)
_DATASET = Path(__file__).resolve().parent.parent / "data" / "cache" / "meta" / "quant_dataset.parquet"
STATE_COLS = ["avg_vol", "breadth", "yield_slope", "credit_spread"]


def _load() -> pd.DataFrame:
    if not _DATASET.exists():
        raise FileNotFoundError("Run ml.meta_features.build_quant_dataset first to cache the dataset.")
    return pd.read_parquet(_DATASET)


def state_and_ic_table(df: pd.DataFrame) -> pd.DataFrame:
    """One row per date: the market-state vector + each strategy's IC that date."""
    recs = []
    for d, g in df.groupby("date"):
        row = {"date": d,
               "avg_vol": float(g["vol60"].mean()),
               "breadth": float((g["mom121"] > 0).mean()),
               "yield_slope": float(g["yield_slope"].iloc[0]),
               "credit_spread": float(g["credit_spread"].iloc[0])}
        for k in STRAT_KEYS:
            row[f"ic_{k}"] = _spearman(g[k].values, g["excess"].values)
        recs.append(row)
    return pd.DataFrame(recs).sort_values("date").reset_index(drop=True)


def analog_recommend(table: pd.DataFrame, as_of_idx: int = -1, k: int = 5) -> dict:
    """Find the k nearest past states to the as_of row; average strategy ICs there."""
    feats = table[STATE_COLS].values.astype(float)
    mu, sd = feats.mean(0), feats.std(0) + 1e-9
    z = (feats - mu) / sd
    target = z[as_of_idx]
    # only PAST states (strictly before as_of) are valid analogs
    cutoff = as_of_idx if as_of_idx >= 0 else len(table) + as_of_idx
    past = [i for i in range(len(table)) if i < cutoff]
    if len(past) < k:
        return {}
    dist = [(np.linalg.norm(z[i] - target), i) for i in past]
    dist.sort()
    nn = [i for _, i in dist[:k]]
    analog_dates = [table.iloc[i]["date"] for i in nn]
    ic_means = {k_: float(np.nanmean([table.iloc[i][f"ic_{k_}"] for i in nn])) for k_ in STRAT_KEYS}
    ranked = sorted(ic_means.items(), key=lambda x: (np.nan_to_num(x[1], nan=-9)), reverse=True)
    return {"as_of": table.iloc[as_of_idx]["date"],
            "state": {c: round(float(table.iloc[as_of_idx][c]), 3) for c in STATE_COLS},
            "analog_dates": analog_dates,
            "strategy_ic_in_analogs": {kk: round(vv, 3) for kk, vv in ranked}}


def _demo() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    table = state_and_ic_table(_load())
    print(f"State/IC table: {len(table)} dates")
    rec = analog_recommend(table, as_of_idx=-1, k=min(5, len(table) - 1))
    if not rec:
        print("not enough history for analogs (expected on the small demo dataset)")
        return
    print(f"\nAs of {rec['as_of']}, market state: {rec['state']}")
    print(f"Most similar past dates: {rec['analog_dates']}")
    print("Strategy IC in those analog states (which book ranked best then):")
    for k, v in rec["strategy_ic_in_analogs"].items():
        print(f"   {k:10} {v:+.3f}")


if __name__ == "__main__":
    _demo()
