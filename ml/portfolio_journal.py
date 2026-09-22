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
        "bench": list(getattr(plan, "bench", []) or []),
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


# Each mandate is graded at ITS OWN declared horizon (rag/strategy_router
# ARCHETYPES), not one arbitrary window. Grading a 2-day trade at 20 days
# measures the round trip; grading Graham value at 20 days measures noise.
MANDATE_HORIZONS = {
    "fast_momentum": 5,
    "swing_momentum": 20,
    "growth_hold": 60,
    "value_hold": 120,
}
_DEFAULT_HORIZON = 20


def update_outcomes(path: Path, horizon_days: Optional[int] = None) -> Optional[dict]:
    """Fill realized forward returns for a past proposal, stop-aware and
    horizon-aware (the honest ruler).

    For each holding, walk forward daily closes from the proposal date:
      - If a close breaches the journaled stop first, the position exits THERE
        (at that day's close — conservative: gap-downs through the stop are
        charged in full, as a real fill would suffer).
      - Otherwise it exits at the mandate's horizon (fast 5d, swing 20d,
        growth 60d, value 120d).
      - If neither has happened yet, the return-so-far is recorded but flagged
        non-final, and EXCLUDED from the scoreboard aggregates so partial
        returns can't pollute the track record.

    Stops are checked on completed daily closes, never intraday ticks —
    consistent with the Weinstein close-based invalidation discipline.
    Re-running re-grades old proposals, so the whole journal migrates to the
    new ruler automatically each night.
    """
    from data import price_cache
    rec = load_proposal(path)
    prop_date = rec["date"]
    horizon = int(horizon_days or MANDATE_HORIZONS.get(rec.get("mandate", ""), _DEFAULT_HORIZON))
    rec["horizon_days"] = horizon
    finals, wins, losses, stop_exits = [], [], [], 0
    for h in rec["holdings"]:
        df = price_cache.get_history(h["ticker"])
        if df is None or "Close" not in df.columns:
            continue
        sl = df.loc[:prop_date]
        if sl.empty:
            continue
        pos = len(sl) - 1
        closes = df["Close"]
        entry = float(h.get("entry") or closes.iloc[pos])
        stop = float(h.get("stop") or 0.0)
        if entry <= 0:
            continue
        end = min(pos + horizon, len(df) - 1)
        exit_reason, exit_i = "open", end
        for i in range(pos + 1, end + 1):
            if stop > 0 and float(closes.iloc[i]) <= stop:
                exit_reason, exit_i = "stop", i
                break
        else:
            if pos + horizon <= len(df) - 1:
                exit_reason = "horizon"
        if exit_i <= pos:
            continue
        r = float(closes.iloc[exit_i] / entry - 1.0)
        h["outcome_return"] = round(r, 4)
        h["outcome_exit"] = exit_reason
        h["outcome_days"] = exit_i - pos
        h["outcome_final"] = exit_reason in ("stop", "horizon")
        if h["outcome_final"]:
            finals.append(r)
            (wins if r > 0 else losses).append(r)
            if exit_reason == "stop":
                stop_exits += 1
    n = len(finals)
    rec["realized_avg_return"] = round(sum(finals) / n, 4) if n else None
    rec["realized_hit_rate"] = round(len(wins) / n, 3) if n else None
    rec["realized_final_count"] = n
    rec["realized_stop_exits"] = stop_exits
    Path(path).write_text(json.dumps(rec, indent=2))
    return rec


def regrade_open(limit: Optional[int] = None) -> dict:
    """Re-grade every proposal that still holds a non-final position.

    THE BUG THIS REPLACES (found 2026-08-31): scripts/oros_run.py re-graded
    only `list_proposals()[-30:]`. A position that stops out finalises within
    days, while it is still inside that 30-file window, and is counted
    forever. A position that WORKS needs its full 20/60/120-day horizon, by
    which time its file has long fallen out of the window, so it is never
    re-graded and stays `outcome_exit: "open"` -- excluded from the
    scoreboard permanently. Result: 1,142 positions stuck at "open" against
    290 stops and 97 horizon exits, and every mandate reading negative
    because its winners were structurally invisible.

    Work is bounded: a proposal whose holdings are ALL final is skipped."""
    done = graded = 0
    for path in list_proposals():
        try:
            rec = load_proposal(path)
        except Exception:
            continue
        hs = rec.get("holdings") or []
        if hs and all(h.get("outcome_final") for h in hs):
            done += 1
            continue
        try:
            update_outcomes(path)
            graded += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("regrade failed for %s: %s", path.name, e)
        if limit and graded >= limit:
            break
    logger.info("regrade_open: %d re-graded, %d already complete", graded, done)
    return {"regraded": graded, "already_final": done}


def compare_by_mandate() -> dict:
    """Group journaled proposals by mandate and report realized performance so far.
    This is the paper-trade scoreboard — FINAL outcomes only (stop hit or
    horizon elapsed), so partial returns can't flatter or damn a mandate.

    Adds expectancy, the number that actually decides whether a strategy makes
    money: hit_rate * avg_win + (1 - hit_rate) * avg_loss. A 36% hit rate is
    fine IF the winners are 3x the losers; a 55% hit rate still loses money if
    they aren't."""
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
        # Position-level final outcomes across every proposal in the mandate.
        finals, stop_exits = [], 0
        for r in recs:
            for h in r.get("holdings", []):
                if h.get("outcome_final") and h.get("outcome_return") is not None:
                    finals.append(float(h["outcome_return"]))
                    if h.get("outcome_exit") == "stop":
                        stop_exits += 1
        wins = [x for x in finals if x > 0]
        losses = [x for x in finals if x <= 0]
        n = len(finals)
        hit = len(wins) / n if n else None
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0
        expectancy = (hit * avg_win + (1 - hit) * avg_loss) if n else None
        # Proposal-level averages (backward compatible with old consumers).
        rets = [r["realized_avg_return"] for r in recs if r.get("realized_avg_return") is not None]
        hits = [r["realized_hit_rate"] for r in recs if r.get("realized_hit_rate") is not None]
        recs.sort(key=lambda r: r.get("timestamp", ""))
        latest = [h["ticker"] for h in recs[-1].get("holdings", [])] if recs else []
        out[m] = {
            "proposals": len(recs),
            "scored": len(rets),
            "avg_realized_pct": round(100 * sum(rets) / len(rets), 2) if rets else None,
            "avg_hit_rate_pct": round(100 * sum(hits) / len(hits), 1) if hits else None,
            "positions_scored": n,
            "hit_rate_pct": round(100 * hit, 1) if hit is not None else None,
            "avg_win_pct": round(100 * avg_win, 2) if wins else None,
            "avg_loss_pct": round(100 * avg_loss, 2) if losses else None,
            "expectancy_pct": round(100 * expectancy, 2) if expectancy is not None else None,
            "stop_exit_pct": round(100 * stop_exits / n, 1) if n else None,
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


def previous_holdings_same_mandate(mandate: str) -> list[str]:
    """Tickers of the most recent PRIOR journaled build with THIS mandate.

    The daily added/dropped diff must compare like with like. The journal
    also records in-app experiments (Custom Builds, Compare mode, other
    mandates) and any second automation's runs — and diffing against those
    produced "phantom drops": names reported as removed that the emailed
    book never held (JP's audit 2026-07-27 found 48 phantom mentions in
    11 of 18 reports; worst case, a Saturday value_hold experiment made
    Monday's swing report "drop" an entire value book). Only a build of
    the same mandate is a valid baseline.
    """
    import json as _json
    try:
        props = list_proposals()
    except Exception:  # noqa: BLE001
        return []
    # props[-1] is the current build (already journaled by the runner);
    # walk backwards through the rest for the same mandate.
    for path in reversed(props[:-1]):
        try:
            rec = _json.loads(path.read_text())
        except Exception:  # noqa: BLE001
            continue
        if rec.get("mandate") == mandate:
            return [h.get("ticker") for h in rec.get("holdings", []) if h.get("ticker")]
    return []
