"""
portfolio_journal.py - Proposal journal for forward paper-trading.

The pre-Phase-6 discipline: before any real capital, the system must propose and
journal, never trade, and accumulate a forward track record on data it has never
seen. This module records every constructed portfolio with its entries and stops,
lists past proposals, and lets outcomes be filled in later so performance can be
measured honestly out of sample.

Stored as one JSON per run under data/journal/portfolio_YYYY-MM-DD[_HHMM].json.

    PYTHONPATH=. python3 ml/portfolio_journal.py
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)
_JOURNAL = Path(__file__).resolve().parent.parent / "data" / "journal"
_JOURNAL.mkdir(parents=True, exist_ok=True)


def record_proposal(plan, account: float, trace=None, note: str = "") -> Path:
    """Write a constructed portfolio (and optional agent trace) to the journal."""
    ts = datetime.utcnow()
    rec = {
        "timestamp": ts.isoformat() + "Z",
        "date": str(ts.date()),
        "regime": plan.regime,
        "mandate": plan.mandate,
        "account": account,
        "invested": plan.invested,
        "cash": plan.cash,
        "weights": plan.weights,
        "note": note or plan.note,
        "holdings": [{
            "ticker": h.ticker, "sector": h.sector, "conviction": h.composite,
            "shares": h.shares, "dollars": h.dollars, "weight_pct": h.weight_pct,
            "entry": h.entry, "stop": h.stop, "led_by":
                (max(h.strategy_scores, key=h.strategy_scores.get) if h.strategy_scores else "n/a"),
            "sentiment": h.sentiment, "earnings_risk": h.earnings_risk,
            "outcome_return": None,        # filled later by update_outcomes
        } for h in plan.holdings],
        "agent_trace": [{"agent": s.agent, "role": s.role, "summary": s.summary}
                        for s in (trace or [])],
    }
    path = _JOURNAL / f"portfolio_{ts.strftime('%Y-%m-%d_%H%M')}.json"
    path.write_text(json.dumps(rec, indent=2))
    logger.info("journaled proposal: %s", path.name)
    return path


def list_proposals() -> list[Path]:
    return sorted(_JOURNAL.glob("portfolio_*.json"))


def load_proposal(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def update_outcomes(path: Path, horizon_days: int = 20) -> Optional[dict]:
    """Fill realized forward returns for a past proposal using current prices.

    Marks each holding's return from its entry to the close `horizon_days` after
    the proposal date (or the latest available if the horizon has not elapsed).
    """
    from data import price_cache
    rec = load_proposal(path)
    prop_date = rec["date"]
    rets = []
    for h in rec["holdings"]:
        df = price_cache.get_history(h["ticker"])
        if df is None:
            continue
        sl = df.loc[:prop_date]
        if sl.empty:
            continue
        pos = len(sl) - 1
        end = min(pos + horizon_days, len(df) - 1)
        if end <= pos:
            continue
        r = float(df["Close"].iloc[end] / df["Close"].iloc[pos] - 1.0)
        h["outcome_return"] = round(r, 4)
        rets.append(r)
    rec["realized_avg_return"] = round(sum(rets) / len(rets), 4) if rets else None
    rec["realized_hit_rate"] = round(sum(1 for r in rets if r > 0) / len(rets), 3) if rets else None
    Path(path).write_text(json.dumps(rec, indent=2))
    return rec


def compare_by_mandate() -> dict:
    """Group journaled proposals by mandate and report realized performance so far.
    This is the swing vs fast (or any mandate) paper-trade scoreboard."""
    from collections import defaultdict
    groups: dict[str, list] = defaultdict(list)
    for p in list_proposals():
        try:
            rec = load_proposal(p)
        except Exception:
            continue
        groups[rec.get("mandate", "?")].append(rec)
    out = {}
    for m, recs in groups.items():
        rets = [r["realized_avg_return"] for r in recs if r.get("realized_avg_return") is not None]
        hits = [r["realized_hit_rate"] for r in recs if r.get("realized_hit_rate") is not None]
        recs.sort(key=lambda r: r.get("timestamp", ""))
        latest = [h["ticker"] for h in recs[-1].get("holdings", [])] if recs else []
        out[m] = {
            "proposals": len(recs),
            "scored": len(rets),
            "avg_realized_pct": round(100 * sum(rets) / len(rets), 2) if rets else None,
            "avg_hit_rate_pct": round(100 * sum(hits) / len(hits), 1) if hits else None,
            "latest_holdings": latest,
        }
    return out


def _demo() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from data import price_cache
    from ml.agent_orchestrator import run_orchestrated
    from ml.portfolio_engine import PortfolioConfig
    sample = ["NVDA", "AMD", "AAPL", "JPM", "LLY", "KO", "XOM", "PWR", "ANET", "AMAT"]
    price_cache.refresh_prices(sample)
    res = run_orchestrated(sample, PortfolioConfig(max_positions=6, use_social=False))
    p = record_proposal(res.plan, 100_000, trace=res.trace)
    print("wrote", p.name)
    print("proposals on file:", [x.name for x in list_proposals()][-3:])
    rec = update_outcomes(p)
    print("realized avg:", rec.get("realized_avg_return"), "hit:", rec.get("realized_hit_rate"))


if __name__ == "__main__":
    _demo()
