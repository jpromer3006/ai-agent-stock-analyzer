"""
top_picks.py - Weinstein Top Picks: today's best individual Stage 2 setups.

For the user who wants to consider single-stock risk on top of the constructed
portfolio, this module scans the liquid universe through the FULL Weinstein
discipline (ml/stage_analyzer) and surfaces only names that pass every gate:

  1. Stage 2 (Advancing) on the weekly view — the only buying stage.
  2. Not invalidated (no completed weekly close back below the 30-week MA).
  3. Weinstein BUY confirmed — the Ch. 1-2 gates: breakout volume surge AND
     positive, rising Mansfield relative strength vs SPY.
  4. Not extended — "fresh" or "watching" setups only; a stock far above a
     rising 30-week MA has spent its reward potential (the ATEX lesson).

Each pick ships with the classic trade plan: buy-stop above the pivot,
stop-loss at the 30-week MA invalidator, measured-move target, risk/reward,
and a position size at 1% account risk so the reader sees what disciplined
sizing looks like.

OROS proposes only — it never trades. Educational, not financial advice.

    PYTHONPATH=. python3 ml/top_picks.py
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

DEFAULT_TOP_N = 5
DEFAULT_MAX_SCAN = 400
DEFAULT_RISK_PCT = 0.01          # 1% of account risked per pick (illustrative)
_MIN_HISTORY = 160               # ~32 weeks of dailies for a real 30-week MA


def weinstein_top_picks(symbols: list[str] | None = None,
                        max_scan: int = DEFAULT_MAX_SCAN,
                        top_n: int = DEFAULT_TOP_N,
                        account: float | None = None,
                        risk_pct: float = DEFAULT_RISK_PCT) -> list[dict]:
    """Scan for the best fully-gated Weinstein Stage 2 setups. Token-free.

    Ranking: fresh setups first (near a clean breakout point), then by
    bull_probability, then risk/reward. Returns at most `top_n` dicts.
    """
    from data import price_cache
    from ml.stage_analyzer import analyze_stage

    if account is None:
        try:
            from ml.auto_config import last_account
            account = last_account()
        except Exception:
            account = 100_000.0

    if symbols is None:
        try:
            from data.universe import load_liquid_ranking
            symbols = load_liquid_ranking() or []
        except Exception:
            symbols = []
        if not symbols:
            symbols = sorted(price_cache.cached_symbols())

    picks: list[dict] = []
    checked = 0
    for s in symbols:
        if checked >= max_scan:
            break
        df = price_cache.get_history(s)
        if df is None or len(df) < _MIN_HISTORY:
            continue
        checked += 1
        try:
            r = analyze_stage(s, df=df, use_cache=True)
        except Exception:
            continue
        # ---- The full Weinstein gauntlet -------------------------------
        if r.stage != 2 or r.is_invalidated:
            continue
        if not r.weinstein_buy_confirmed:
            continue
        if r.setup_type not in ("fresh", "watching"):
            continue
        ts = r.trade_setup
        if ts is None or not ts.applicable or ts.direction != "LONG":
            continue
        stop = float(ts.stop_loss or r.ma_30w)
        entry = float(ts.entry_price or r.last_close)
        risk_ps = entry - stop
        if risk_ps <= 0 or entry <= 0:
            continue
        shares = int((account * risk_pct) // risk_ps)
        if shares <= 0:
            continue
        picks.append({
            "ticker": r.ticker,
            "setup": r.setup_type,
            "last_close": round(r.last_close, 2),
            "buy_stop": round(entry, 2),          # enter ONLY on breakout through this
            "stop_loss": round(stop, 2),          # 30-week MA invalidator
            "target": round(float(ts.target_1 or 0.0), 2),
            "risk_reward": round(float(ts.risk_reward_ratio or 0.0), 2),
            "bull_prob": round(float(r.bull_probability), 2),
            "pct_above_ma": round(float(r.pct_above_ma) * 100, 1),
            "mansfield_rs": round(float(r.mansfield_rs), 2),
            "volume_surge": round(float(r.volume_surge), 2),
            "shares_1pct": shares,
            "risk_dollars": round(shares * risk_ps),
            "note": (ts.narrative or "").strip()[:140],
            # Signal provenance for the chart page: the exact breakout bar the
            # analyzer anchored this Stage 2 call to.
            "breakout_date": r.stage2_entry_date,
            "breakout_price": round(float(r.stage2_entry_price or 0.0), 2),
            "weeks_held": int(r.weeks_held or 0),
        })
    picks.sort(key=lambda p: (p["setup"] != "fresh",       # fresh first
                              -p["bull_prob"], -p["risk_reward"]))
    top = picks[:top_n]
    logger.info("top picks: %d candidates passed all gates from %d scanned; returning %d",
                len(picks), checked, len(top))
    return top


def format_picks_text(picks: list[dict], account: float | None = None) -> str:
    """Plain-text block for the daily email body."""
    if not picks:
        return ("WEINSTEIN TOP PICKS: none today. No name passed every gate "
                "(Stage 2 + volume + relative strength + not extended). "
                "In Weinstein's book, no setup means no trade.")
    lines = ["WEINSTEIN TOP PICKS (fully gated Stage 2 setups — educational, not advice):"]
    for i, p in enumerate(picks, 1):
        lines.append(
            f"{i}. {p['ticker']:<6} {p['setup']:<8} last {p['last_close']:>8.2f} | "
            f"buy-stop {p['buy_stop']:.2f} | stop {p['stop_loss']:.2f} | "
            f"target {p['target']:.2f} (R/R {p['risk_reward']:.1f}) | "
            f"bull {p['bull_prob']:.0%} | +{p['pct_above_ma']:.1f}% vs 30w MA | "
            f"{p['shares_1pct']} sh at 1% risk")
    lines.append("Enter ONLY if price takes out the buy-stop; exit on a weekly "
                 "close below the stop. Sized at 1% account risk per name.")
    return "\n".join(lines)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ps = weinstein_top_picks(max_scan=200)
    print(format_picks_text(ps))
