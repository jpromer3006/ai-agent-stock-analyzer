"""
market_memory.py - Persistent market memory for strategy selection.

This is the project's "memory" layer, and it is NOT LLM context. It is a growing,
on-disk record of: the market STATE on each date, and how well each book strategy
ranked stocks in that state (its Information Coefficient). The portfolio engine
reads this memory to answer "given today's market state, which strategies have
worked in similar states?" and tilts the strategy weights accordingly before it
constructs and sizes the portfolio.

Two memory functions, both deterministic and zero-token:
  * Episodic: append one row per run (state + per-strategy performance + outcome).
  * Semantic recall: nearest-state lookup that aggregates which strategies led in
    analogous past states, returning a weight tilt for the router.

It can be seeded from the historical quant dataset and then grows with every live
run, so the system literally accumulates experience over time. No Anthropic tokens
are used anywhere in this file.

    PYTHONPATH=. python3 ml/market_memory.py
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from ml.market_analog import STATE_COLS, state_and_ic_table
from ml.meta_features import STRAT_KEYS

logger = logging.getLogger(__name__)
_STORE = Path(__file__).resolve().parent.parent / "data" / "cache" / "memory"
_STORE.mkdir(parents=True, exist_ok=True)
_MEM = _STORE / "market_memory.parquet"
_DATASET = Path(__file__).resolve().parent.parent / "data" / "cache" / "meta" / "quant_dataset.parquet"


# ---------------------------------------------------------------------------
def load_memory() -> pd.DataFrame:
    if _MEM.exists():
        return pd.read_parquet(_MEM)
    cols = ["date"] + STATE_COLS + [f"ic_{k}" for k in STRAT_KEYS] + ["regime", "outcome"]
    return pd.DataFrame(columns=cols)


def save_memory(df: pd.DataFrame) -> None:
    df.to_parquet(_MEM)


def seed_from_dataset() -> int:
    """Populate memory from the cached historical quant dataset (one-time bootstrap)."""
    if not _DATASET.exists():
        logger.warning("No quant dataset to seed from. Run ml.meta_features.build_quant_dataset.")
        return 0
    table = state_and_ic_table(pd.read_parquet(_DATASET))
    table["regime"] = None
    table["outcome"] = np.nan
    save_memory(table)
    return len(table)


def record(date: str, state: dict, strategy_ic: dict,
           regime: Optional[str] = None, outcome: Optional[float] = None) -> None:
    """Append one observation: today's state and how each strategy ranked."""
    mem = load_memory()
    row = {"date": date, "regime": regime, "outcome": outcome}
    row.update({c: state.get(c) for c in STATE_COLS})
    row.update({f"ic_{k}": strategy_ic.get(k) for k in STRAT_KEYS})
    mem = pd.concat([mem, pd.DataFrame([row])], ignore_index=True)
    save_memory(mem)


# ---------------------------------------------------------------------------
# Semantic recall: which strategies worked in states like this one?
# ---------------------------------------------------------------------------
def recall(current_state: dict, k: int = 8) -> dict:
    mem = load_memory()
    if len(mem) < k:
        return {}
    feats = mem[STATE_COLS].astype(float).values
    mu, sd = feats.mean(0), feats.std(0) + 1e-9
    z = (feats - mu) / sd
    target = (np.array([current_state.get(c, mu[i]) for i, c in enumerate(STATE_COLS)]) - mu) / sd
    dist = np.linalg.norm(z - target, axis=1)
    nn = np.argsort(dist)[:k]
    analog_dates = mem.iloc[nn]["date"].tolist()

    def _safe_mean(col):
        vals = mem.iloc[nn][col].astype(float).dropna()
        return float(vals.mean()) if len(vals) else 0.0
    strat_ic = {kk: _safe_mean(f"ic_{kk}") for kk in STRAT_KEYS}
    ranked = sorted(strat_ic.items(), key=lambda x: np.nan_to_num(x[1], nan=-9), reverse=True)
    return {"analog_dates": analog_dates,
            "strategy_ic": {kk: round(vv, 3) for kk, vv in ranked}}


def memory_tilt(current_state: dict, k: int = 8, scale: float = 4.0) -> dict[str, float]:
    """Multipliers (around 1.0) the router applies: strategies that historically
    ranked well in states like today get >1, poor ones get <1."""
    r = recall(current_state, k=k)
    if not r:
        return {kk: 1.0 for kk in STRAT_KEYS}
    out = {}
    for kk in STRAT_KEYS:
        ic = r["strategy_ic"].get(kk, 0.0)
        out[kk] = float(np.clip(1.0 + scale * ic, 0.3, 1.7))
    return out


def _demo() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    n = seed_from_dataset()
    print(f"Seeded market memory with {n} historical states.")
    mem = load_memory()
    if mem.empty:
        print("memory empty - build the quant dataset first."); return
    latest = mem.iloc[-1]
    current = {c: float(latest[c]) for c in STATE_COLS}
    print(f"\nCurrent state: {{{', '.join(f'{c}={current[c]:.2f}' for c in STATE_COLS)}}}")
    r = recall(current, k=min(8, len(mem) - 1))
    print(f"Analogous past states: {r.get('analog_dates')}")
    print("What memory says worked in states like today (strategy IC):")
    for kk, vv in (r.get("strategy_ic") or {}).items():
        print(f"   {kk:10} {vv:+.3f}")
    print("\nWeight tilt the portfolio engine would apply:")
    for kk, vv in memory_tilt(current).items():
        print(f"   {kk:10} x{vv:.2f}")


if __name__ == "__main__":
    _demo()
