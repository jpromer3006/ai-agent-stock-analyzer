#!/usr/bin/env python3
"""
oros_run.py - Unattended nightly/intraday runner for OROS (Phase 6).

One entry point for cron / launchd. It:
  1. Builds the dynamic universe and refreshes the price cache (batched).
  2. Ranks the universe by liquidity (kills the alphabetical heuristic).
  3. Runs the multi-agent orchestrator over the top-N liquid names.
  4. Journals the proposed portfolio (paper-trading; it NEVER trades).
  5. Updates outcomes on past proposals so the forward track record accrues.
  6. Enforces a deferring budget cap and prints a brief.

Guardrails: propose-and-journal-only (no orders, no money moved); the budget cap
defers LLM work rather than blocking; everything is logged.

Examples:
    PYTHONPATH=. python3 scripts/oros_run.py --refresh-cap 1500 --cap 800
    PYTHONPATH=. python3 scripts/oros_run.py --light          # quick run, no full refresh
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("oros")
_ROOT = Path(__file__).resolve().parent.parent
_LEDGER = _ROOT / "data" / "journal" / f"cost_{datetime.utcnow():%Y-%m}.json"

# Load .env so unattended runs (launchd / GitHub Actions) see Gmail + API keys.
try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except Exception:
    pass


# ---------------------------------------------------------------------------
# Deferring budget governor (ready for when LLM memos are enabled; ~$0 today)
# ---------------------------------------------------------------------------
def budget_ok(monthly_cap: float) -> bool:
    spent = 0.0
    if _LEDGER.exists():
        try:
            spent = float(json.loads(_LEDGER.read_text()).get("spent", 0.0))
        except Exception:
            spent = 0.0
    if spent >= monthly_cap:
        log.warning("budget cap reached ($%.2f >= $%.2f) - deferring LLM work", spent, monthly_cap)
        return False
    return True


def record_cost(amount: float) -> None:
    spent = 0.0
    if _LEDGER.exists():
        try:
            spent = float(json.loads(_LEDGER.read_text()).get("spent", 0.0))
        except Exception:
            spent = 0.0
    _LEDGER.write_text(json.dumps({"month": f"{datetime.utcnow():%Y-%m}", "spent": spent + amount}))


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh-cap", type=int, default=1500, help="names to refresh prices for")
    ap.add_argument("--cap", type=int, default=800, help="liquid names to scan")
    ap.add_argument("--positions", type=int, default=12)
    ap.add_argument("--account", type=float, default=100_000.0)
    ap.add_argument("--mandate", default="auto",
                    help="'auto' lets OROS pick from the scoreboard + regime "
                         "(ml/auto_config.py); or name one, e.g. swing_momentum")
    ap.add_argument("--monthly-cap", type=float, default=60.0, help="USD LLM budget cap")
    ap.add_argument("--light", action="store_true", help="skip the bulk price refresh")
    ap.add_argument("--no-email", action="store_true",
                    help="skip the scan-complete + daily-report emails")
    ap.add_argument("--report-to", default=None,
                    help="daily report recipient (default OROS_REPORT_TO / JPR20@fordham.edu)")
    args = ap.parse_args()

    from data import price_cache
    from data.universe import build_liquid_ranking, load_liquid_ranking, get_universe
    from ml.agent_orchestrator import run_orchestrated
    from ml.portfolio_engine import PortfolioConfig
    from ml.portfolio_journal import record_proposal, list_proposals, update_outcomes
    from ml.market_memory import record as memory_record
    from ml.market_analog import STATE_COLS

    t0 = time.time()
    log.info("OROS run start | refresh_cap=%d cap=%d positions=%d", args.refresh_cap, args.cap, args.positions)

    # 1-2. Universe + price refresh (batched) -> liquidity ranking
    if not args.light:
        uni = list(get_universe(filtered=False).keys())
        # liquid core first so the refresh covers the real names within the cap
        try:
            from ml.momentum_screener import _SP100, _SECTOR_LEADERS
            core = [s for s in (_SP100 + _SECTOR_LEADERS) if s in set(uni)]
        except Exception:
            core = []
        work = list(dict.fromkeys(core + uni))[: args.refresh_cap]
        log.info("refreshing prices for %d names ...", len(work))
        price_cache.refresh_prices(work)
    ranked = build_liquid_ranking()
    log.info("liquid universe: %d names", len(ranked))
    symbols = (ranked or [])[: args.cap]
    if not symbols:
        log.error("no liquid symbols available; aborting")
        return 1

    # 3. Orchestrate — 'auto' means OROS chooses its own settings from the
    # scoreboard + regime (the go-live, no-human-in-the-loop path).
    llm_ok = budget_ok(args.monthly_cap)            # logs if the cap is hit
    mandate, extra_cfg = args.mandate, {}
    if args.mandate == "auto":
        from ml.auto_config import choose_auto_settings
        auto = choose_auto_settings()
        mandate = auto["mandate"]
        args.positions = auto["max_pos"]
        extra_cfg = dict(risk_per_trade=auto["risk_pct"],
                         min_dollar_vol=auto["min_liq_m"] * 1_000_000,
                         max_per_sector=auto["max_per_sector"])
        args.account = auto["account"]
        log.info("AUTO settings | regime=%s mandate=%s (%s) positions=%d",
                 auto["regime"], mandate, auto["mandate_reason"], args.positions)
    cfg = PortfolioConfig(account_size=args.account, max_positions=args.positions,
                          mandate=mandate, use_social=False,
                          use_av_news=False,          # LLM narrative off in unattended runs
                          **extra_cfg)
    res = run_orchestrated(symbols, cfg)
    plan = res.plan
    if plan is None:
        log.error("orchestrator produced no plan")
        return 1

    # 4. Journal (propose-and-journal-only; never trades)
    path = record_proposal(plan, args.account, trace=res.trace, note="nightly run")
    record_cost(0.0)  # quant engine is token-free

    # 5. Update outcomes on older proposals (forward track record)
    props = list_proposals()
    for p in props[-30:]:
        try:
            update_outcomes(p)
        except Exception:
            pass

    # 6. Changes + opportunities + alert (so nothing is left out)
    from ml.notifier import detect_changes, scan_opportunities, build_alert, notify
    prev_holdings = []
    if len(props) >= 2:
        try:
            prev_holdings = [h["ticker"] for h in json.loads(props[-2].read_text()).get("holdings", [])]
        except Exception:
            pass
    new_holdings = [h.ticker for h in plan.holdings]
    changes = detect_changes(new_holdings, prev_holdings)
    opps = scan_opportunities(symbols)
    alert = build_alert(changes, opps)
    if alert:
        notify(*alert)

    # 6b. Go-live emails: ALWAYS confirm the scan ran (even a quiet day), then
    # send the full daily report (PDF: market state, P&L, benchmark chart).
    if not args.no_email:
        from ml.notifier import send_email
        import os as _os
        report_to = (args.report_to or _os.environ.get("OROS_REPORT_TO", "").strip()
                     or "JPR20@fordham.edu")
        send_email(
            f"OROS: scan complete — {datetime.now():%b %d, %Y %H:%M}",
            (f"The automated OROS scan ran.\n\n"
             f"Universe scanned: {len(symbols)} names\n"
             f"Regime: {plan.regime.upper()} | mandate: {mandate}\n"
             f"Holdings proposed: {len(plan.holdings)}\n"
             f"Changes vs last run: +{changes['added'] or []} -{changes['removed'] or []}\n"
             f"IPO breakouts: {[o['ticker'] for o in opps['breakouts'][:6]] or 'none'}\n\n"
             f"Daily report with gains/losses follows as a PDF.\n"
             f"OROS proposes and journals only — it never trades."),
            to=report_to)
        try:
            from ml.daily_report import send_daily_report
            send_daily_report(to=report_to, opps=opps, changes=changes)
        except Exception as e:  # noqa: BLE001
            log.warning("daily report failed: %s", e)

    # 7. Collect the day's features into the training warehouse, and label old rows
    wh = {}
    try:
        from ml.data_collector import collect_snapshot, label_warehouse, warehouse_stats
        collect_snapshot(symbols)
        label_warehouse()
        wh = warehouse_stats()
        log.info("warehouse: %s", wh)
    except Exception as e:  # noqa: BLE001
        log.warning("data collection failed: %s", e)

    # brief
    print("\n" + "=" * 64)
    print(f"OROS BRIEF  {datetime.utcnow():%Y-%m-%d %H:%M}Z   regime={plan.regime.upper()}")
    print("=" * 64)
    for s in res.trace:
        print(f"  [{s.agent:18}] {s.summary}")
    print(f"\n  Holdings ({len(plan.holdings)}), invested {plan.invested/args.account:.0%}, "
          f"cash {plan.cash/args.account:.0%}:")
    for h in plan.holdings[:15]:
        print(f"    {h.ticker:6} {h.sector[:20]:20} wt {h.weight_pct:5.1f}%  stop {h.stop:.2f}")
    if changes["added"] or changes["removed"]:
        print(f"\n  Changes vs last run: +{changes['added']}  -{changes['removed']}")
    if opps["breakouts"]:
        print(f"  IPO breakouts (opportunity): {[o['ticker'] for o in opps['breakouts'][:6]]}")
    if opps["watch"]:
        print(f"  IPO watch ({len(opps['watch'])} young names, e.g. {[w['ticker'] for w in opps['watch'][:6]]})")
    print(f"\n  Journaled: {path.name}   (proposal only - no orders placed)")
    if wh:
        print(f"  Training warehouse: {wh.get('rows', 0)} feature rows, "
              f"{wh.get('labeled', 0)} labeled (grows daily for model training)")
    print(f"  Done in {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
