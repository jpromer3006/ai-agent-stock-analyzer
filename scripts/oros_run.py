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
    PYTHONPATH=. python3 scripts/oros_run.py                  # caps come from ml/oros_config.py
    PYTHONPATH=. python3 scripts/oros_run.py --light          # quick run, no full refresh
"""

from __future__ import annotations

import argparse
import json
import logging
import os
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
    from ml.oros_config import OROS_CAP, OROS_REFRESH_CAP
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh-cap", type=int, default=OROS_REFRESH_CAP,
                    help="names to refresh prices for")
    ap.add_argument("--cap", type=int, default=OROS_CAP,
                    help="liquid names to scan (the OROS 500); the default "
                         "is the single source of truth in ml/oros_config.py")
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
    ap.add_argument("--edition", default="auto",
                    choices=("auto", "morning", "evening"),
                    help="morning = full scan + newscast; evening = the "
                         "Closing Bell (grade the morning, tally the day). "
                         "auto resolves by Eastern clock (>=14:00 ET = evening)")
    args = ap.parse_args()

    # The Closing Bell: the 16:50 ET cron slot grades instead of re-scanning.
    edition = args.edition
    if edition == "auto":
        try:
            from zoneinfo import ZoneInfo
            edition = ("evening"
                       if datetime.now(ZoneInfo("America/New_York")).hour >= 14
                       else "morning")
        except Exception:
            edition = "morning"
    if edition == "evening":
        log.info("OROS Closing Bell run (evening edition)")
        from ml.evening_report import send_evening_report
        send_evening_report(to=args.report_to)
        return 0

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

    # 2b. THE SCAN (JP, 2026-09-04). Grade the universe ONCE, here, before any
    # book exists, and let everything downstream read the artifact. The old
    # order built the portfolio first (stages computed privately inside
    # scoring) and tallied the "scan" afterward in a second pass, so the
    # narration — scanner grades every chart, then the book is built from what
    # it found — described an order the code never ran in.
    #
    # build_scan removes benchmarks BEFORE the cap (they used to be stripped
    # after, which is why the desk said "495 candidate names" every single
    # day) and substitutes down the ranking until the full cap is CONFIRMED.
    from ml.scan_report import build_scan, save_scan
    scan = build_scan(ranked or [], cap=args.cap)
    if not scan.rows:
        log.error("scan produced no graded names; aborting")
        return 1
    scan_path = save_scan(scan)
    symbols = [r["ticker"] for r in scan.rows]
    log.info("SCAN: %d confirmed of %d requested | %d substitutions | "
             "%d skipped | %d graded on a stale bar | hash=%s -> %s",
             scan.confirmed, args.cap, scan.substitutions, len(scan.skipped),
             scan.stale_rows, scan.scan_hash, scan_path.name)
    if scan.confirmed < args.cap:
        log.warning("SCAN SHORT: only %d confirmed — the broadcast must not "
                    "claim %d tonight", scan.confirmed, args.cap)

    # 2c. The regime is now a function of the scan, not of SPY alone. SPY's
    # own stage can only downgrade the breadth read, never promote it.
    regime_read = None
    try:
        from ml.market_context import regime_from_scan
        from ml.stage_analyzer import analyze_stage
        _spy_stage = None
        try:
            _spy = analyze_stage("SPY", df=price_cache.get_history("SPY"),
                                 use_cache=True)
            _spy_stage = int(getattr(_spy, "stage", 0)) or None
        except Exception:  # noqa: BLE001
            pass
        regime_read = regime_from_scan(scan.to_dict(), spy_stage=_spy_stage)
        log.info("REGIME: %s", regime_read["headline"])
        log.info("REGIME (spoken): %s", regime_read["spoken"])
    except Exception as e:  # noqa: BLE001
        log.warning("regime read failed: %s", e)

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

    # Invariant (JP, 2026-08-06): the book must be a SUBSET of the scan.
    # On 8/05 a fossil 800-cap watchdog run journaled MTCH/HST/GSAT — names
    # outside the OROS 500 — and the app's Stage Detail couldn't find its
    # own holdings. Announce any break loudly at write time.
    _scan_set = set(symbols)
    _outside = [getattr(h, "ticker", None) for h in (plan.holdings or [])
                if getattr(h, "ticker", None) not in _scan_set]
    if _outside:
        log.warning("UNIVERSE BREAK: holdings outside the scanned %d: %s "
                    "(source=%s)", len(symbols), _outside,
                    os.environ.get("OROS_RUN_SOURCE", "local"))

    # 4. Journal (propose-and-journal-only; never trades). The note carries
    # provenance so same-day records from different runners are tellable
    # apart (cloud vs watchdog vs cron).
    _src = os.environ.get("OROS_RUN_SOURCE", "local")
    path = record_proposal(plan, args.account, trace=res.trace,
                           note=f"nightly run ({_src})")
    record_cost(0.0)  # quant engine is token-free

    # 4b. The OROS 500: journal constituents + stage breadth so the broadcast's
    # claims ("the OROS 500", "52% Advancing") are true by construction. Runs
    # off the stage cache the orchestrator just warmed.
    try:
        from ml.oros500 import record_constituents
        from ml.breadth import record_breadth_from_scan
        c500 = record_constituents(symbols)
        # ONE SCAN, ONE GRADE: the panel is a tally of the same rows the book
        # ranked, not a second walk over the price cache.
        br = record_breadth_from_scan(scan)
        log.info("OROS 500 journaled: %d names (+%d/-%d) | breadth %s",
                 c500["count"], len(c500["entered"]), len(c500["left"]),
                 br["counts"])
    except Exception as e:  # noqa: BLE001
        log.warning("OROS 500 / breadth journaling failed: %s", e)

    # 5. Update outcomes on older proposals (forward track record).
    #
    # Was `props[-30:]` until 2026-08-31. That window is shorter than every
    # mandate horizon except fast_momentum, so stop-outs (which finalise in
    # days) were always re-graded while winners (which need the full 20/60/120
    # days) fell out of the window before they could ever be marked "horizon".
    # The scoreboard therefore collected losers only, and the cold-streak
    # throttle sized the book off it. regrade_open() walks the whole journal
    # but skips proposals whose holdings are all final, so the cost is bounded
    # by the number of still-open positions, not by journal length.
    from ml.portfolio_journal import regrade_open
    try:
        _rg = regrade_open()
        log.info("outcomes re-graded: %s", _rg)
    except Exception as e:  # noqa: BLE001
        log.warning("regrade_open failed: %s", e)

    # 6. Changes + opportunities + alert (so nothing is left out)
    from ml.notifier import detect_changes, scan_opportunities, build_alert, notify
    # Mandate-aware baseline (phantom-drop fix, 2026-07-27): diff ONLY
    # against the previous build of the SAME mandate — never against an
    # in-app experiment or another mandate's book.
    from ml.portfolio_journal import previous_holdings_same_mandate
    prev_holdings = previous_holdings_same_mandate(getattr(plan, "mandate", ""))
    new_holdings = [h.ticker for h in plan.holdings]
    changes = detect_changes(new_holdings, prev_holdings)
    # Uniformity rule (JP, 2026-07-28): the diff is computed ONCE, here, and
    # written INTO the journaled proposal. The report, the glance digest, and
    # the broadcast all read that one record — no second computation may ever
    # disagree with the emailed report again.
    try:
        rec_j = json.loads(props[-1].read_text())
        rec_j["changes"] = changes
        props[-1].write_text(json.dumps(rec_j, indent=1))
    except Exception:
        pass
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
        # Timestamps in Eastern Time regardless of where the runner lives
        # (GitHub's servers are UTC — "scan complete 14:56" confused JP).
        try:
            from zoneinfo import ZoneInfo
            _now_et = datetime.now(ZoneInfo("America/New_York"))
        except Exception:
            _now_et = datetime.now()
        # One morning email (JP, 2026-07-29): the daily report IS the
        # heartbeat; the separate scan-complete email is opt-in only
        # (OROS_SCAN_EMAIL=1). Failures still alert loudly below.
        if _os.environ.get("OROS_SCAN_EMAIL", "0") == "1":
            send_email(
                f"OROS: scan complete — {_now_et:%b %d, %Y %H:%M %Z}",
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
            # Research Memo of the Day rides along only while the LLM budget
            # cap has room (budget_ok above) — a few cents per weekday.
            send_daily_report(to=report_to, opps=opps, changes=changes,
                              research=llm_ok)
        except Exception as e:  # noqa: BLE001
            # A missing report must never be a whisper in a log (the 07-07
            # lesson). Alert loudly — SMTP itself clearly works if we got
            # this far, since the scan-complete email just went out.
            log.warning("daily report failed: %s", e)
            send_email("OROS ALERT: daily report FAILED to generate",
                       f"The scan completed but the report step raised:\n\n"
                       f"{type(e).__name__}: {e}\n\n"
                       "The scan-complete email is unaffected. Check the run "
                       "log for the traceback.", to=report_to)

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
