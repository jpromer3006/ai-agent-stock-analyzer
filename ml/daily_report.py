"""
daily_report.py - The daily OROS report: market state, portfolio gains/losses,
scan summary, and a portfolio-vs-index-funds comparison chart, rendered to a
PDF and emailed (default recipient JPR20@fordham.edu, override with
OROS_REPORT_TO in .env).

Design:
  - build_report_data()  gathers everything token-free from the price cache,
    the journal, and the notifier's opportunity scan.
  - render_pdf()         lays the report out with matplotlib PdfPages
    (no extra dependencies; matplotlib is already required).
  - send_daily_report()  builds, renders, emails, and drops a per-date marker
    so a launchd run and a cloud run on the same machine don't double-send.

OROS never trades. All figures are paper-trading / proposal bookkeeping.
Educational, not financial advice.

    PYTHONPATH=. python3 ml/daily_report.py --no-email     # dry run, PDF only
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_REPORT_DIR = _ROOT / "data" / "journal" / "reports"
_REPORT_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_RECIPIENT = "JPR20@fordham.edu"
_INDEXES = ["SPY", "QQQ", "DIA", "IWM"]
_BENCHMARKS = ["SPY", "QQQ", "VTI", "VTSAX", "FXAIX"]   # ETFs + mutual funds


# ---------------------------------------------------------------------------
# Data gathering (token-free)
# ---------------------------------------------------------------------------
def _index_snapshot() -> list[dict]:
    """Last close, day / 5-day moves, and position vs the 150-day (~30-week) MA
    for the major index ETFs — the Weinstein market-state read."""
    from data import price_cache
    rows = []
    for t in _INDEXES:
        df = price_cache.get_history(t)
        if df is None or len(df) < 160 or "Close" not in df.columns:
            continue
        c = df["Close"].dropna()
        last, prev = float(c.iloc[-1]), float(c.iloc[-2])
        wk = float(c.iloc[-6]) if len(c) > 6 else prev
        ma150 = c.rolling(150).mean()
        ma_now, ma_then = float(ma150.iloc[-1]), float(ma150.iloc[-11])
        rows.append({
            "ticker": t, "close": last,
            "day_pct": 100 * (last / prev - 1),
            "wk_pct": 100 * (last / wk - 1),
            "vs_ma_pct": 100 * (last / ma_now - 1) if ma_now else 0.0,
            "ma_trend": "rising" if ma_now > ma_then else "falling",
        })
    return rows


def _latest_proposal() -> dict | None:
    """The report's book of record. Prefers the latest AUTONOMOUS build
    (morning run) over in-app experiments — a 2 AM Custom Build must never
    become 'the portfolio' the report, video, and app narrate (JP's
    uniformity rule, 2026-07-28). Falls back to the absolute latest only
    when no autonomous record exists."""
    try:
        import json as _json
        from ml.portfolio_journal import list_proposals, load_proposal
        props = list_proposals()
        if not props:
            return None
        for path in reversed(props):
            try:
                rec = _json.loads(path.read_text())
            except Exception:  # noqa: BLE001
                continue
            if not str(rec.get("note") or "").startswith("UI build"):
                return rec
        return load_proposal(props[-1])
    except Exception as e:  # noqa: BLE001
        logger.warning("no proposal available: %s", e)
        return None


def _first_entries(rec: dict) -> dict:
    """For each CURRENT holding, the entry price and date from the earliest
    CONSECUTIVE autonomous journal it appears in. The book re-journals
    entries at each morning's price, which made 'Unreal' read $0 forever
    (JP, 2026-07-29) — real position P&L must measure from when the name
    first joined the book, exactly like a brokerage does."""
    import json as _json
    out = {h["ticker"]: {"entry": float(h.get("entry") or 0),
                         "date": rec.get("date", "")}
           for h in rec.get("holdings", []) if h.get("ticker")}
    try:
        from ml.portfolio_journal import list_proposals
        autos = []
        for path in list_proposals():
            try:
                r = _json.loads(path.read_text())
            except Exception:  # noqa: BLE001
                continue
            if str(r.get("note") or "").startswith("UI build"):
                continue
            autos.append(r)
        # newest-first, skipping today's own record(s)
        active = set(out)
        for r in reversed(autos):
            if r.get("date", "") >= rec.get("date", ""):
                continue
            held = {h.get("ticker"): h for h in r.get("holdings", [])}
            for t in list(active):
                if t in held:
                    out[t] = {"entry": float(held[t].get("entry") or out[t]["entry"]),
                              "date": r.get("date", out[t]["date"])}
                else:
                    active.discard(t)      # run broken — history stops here
            if not active:
                break
    except Exception:  # noqa: BLE001
        pass
    return out


def _holdings_pnl(rec: dict) -> dict:
    """Per-holding unrealized paper P&L since the journaled entry, plus the
    day's move. This is the 'watch portfolios and report gains/losses' core."""
    from data import price_cache
    firsts = _first_entries(rec)
    rows, tot_cost, tot_val, tot_day = [], 0.0, 0.0, 0.0
    for h in rec.get("holdings", []):
        df = price_cache.get_history(h["ticker"])
        if df is None or len(df) < 2 or "Close" not in df.columns:
            continue
        c = df["Close"].dropna()
        last, prev = float(c.iloc[-1]), float(c.iloc[-2])
        _f = firsts.get(h["ticker"]) or {}
        entry = float(_f.get("entry") or h.get("entry") or last)
        held_since = _f.get("date", rec.get("date", ""))
        shares = float(h.get("shares") or 0)
        cost, val = shares * entry, shares * last
        day_d = shares * (last - prev)
        rows.append({
            "ticker": h["ticker"], "sector": h.get("sector", "?"),
            "shares": shares, "entry": entry, "last": last,
            "stop": float(h.get("stop") or 0),
            "day_pct": 100 * (last / prev - 1), "day_usd": day_d,
            "unreal_pct": 100 * (last / entry - 1) if entry else 0.0,
            "unreal_usd": val - cost,
            "held_since": held_since,
            "below_stop": bool(h.get("stop")) and last < float(h["stop"]),
        })
        tot_cost += cost
        tot_val += val
        tot_day += day_d
    rows.sort(key=lambda r: -r["unreal_usd"])
    return {
        "rows": rows,
        "total_cost": tot_cost, "total_value": tot_val,
        "total_unreal_usd": tot_val - tot_cost,
        "total_unreal_pct": 100 * (tot_val / tot_cost - 1) if tot_cost else 0.0,
        "total_day_usd": tot_day,
        "stops_hit": [r["ticker"] for r in rows if r["below_stop"]],
    }


def _todays_actions(rec: dict | None) -> list[str]:
    """The Weinstein exit workflow, pre-chewed into a morning checklist.
    Stops only ratchet UP (under the rising 30-week MA); Stage 3 gets flagged
    to sell into strength; invalidated names get an exit call. An empty list
    means the honest answer is: nothing to do today."""
    if not rec:
        return []
    from data import price_cache
    from ml.stage_analyzer import analyze_stage
    acts: list[str] = []
    for h in rec.get("holdings", []):
        t = h["ticker"]
        df = price_cache.get_history(t)
        if df is None or len(df) < 160 or "Close" not in df.columns:
            continue
        last = float(df["Close"].dropna().iloc[-1])
        stop = float(h.get("stop") or 0.0)
        if stop and last < stop:
            acts.append(f"EXIT {t}: closed {last:.2f}, below the stop {stop:.2f}")
            continue
        try:
            r = analyze_stage(t, df=df, use_cache=True)
        except Exception:
            continue
        if getattr(r, "is_invalidated", False) or r.stage == 4:
            acts.append(f"EXIT {t}: weekly close below the 30-week MA "
                        f"(Stage {r.stage} — Weinstein invalidation)")
            continue
        if r.stage == 3:
            acts.append(f"REVIEW {t}: entered Stage 3 (topping) — "
                        "consider selling into strength, no new money")
            continue
        new_stop = round(float(getattr(r, "ma_30w", 0.0)), 2)
        if r.stage == 2 and stop and new_stop > stop * 1.01 and new_stop < last:
            acts.append(f"RAISE STOP {t}: {stop:.2f} -> {new_stop:.2f} "
                        "(ratchet under the rising 30-week MA; never lower it)")
    return acts


def build_report_data(opps: dict | None = None, changes: dict | None = None) -> dict:
    """Everything the report needs, in one dict."""
    try:
        from ml.portfolio_engine import detect_regime
        regime = detect_regime()
    except Exception:
        regime = "unknown"
    vix = {}
    try:
        from ml.portfolio_engine import vix_read
        vix = vix_read() or {}
    except Exception:
        pass
    rec = _latest_proposal()
    pnl = _holdings_pnl(rec) if rec else {"rows": [], "stops_hit": []}
    # Fallback: derive added/dropped from the last two proposals when the
    # runner didn't hand us changes (standalone report runs).
    # Uniformity rule (JP, 2026-07-28): if the journaled proposal carries its
    # own diff (written once by the runner), that IS the truth — the report,
    # digest, and broadcast all read it. Recompute only when it is absent.
    if rec and rec.get("changes"):
        changes = rec["changes"]
    if not changes and rec:
        try:
            # Mandate-aware baseline (phantom-drop fix, 2026-07-27): compare
            # only against the previous build of the SAME mandate, so in-app
            # experiments can never contaminate the emailed added/dropped list.
            from ml.portfolio_journal import previous_holdings_same_mandate
            prev = previous_holdings_same_mandate(rec.get("mandate", ""))
            if prev:
                from ml.notifier import detect_changes
                changes = detect_changes([h["ticker"] for h in rec.get("holdings", [])], prev)
        except Exception:
            pass
    scoreboard = {}
    try:
        from ml.portfolio_journal import compare_by_mandate
        scoreboard = compare_by_mandate()
    except Exception:
        pass
    if opps is None:
        try:
            from ml.notifier import scan_opportunities
            opps = scan_opportunities(max_check=200)
        except Exception:
            opps = {"breakouts": [], "watch": []}
    # Personal (Chase) book: OFF by default (JP, 2026-07-28) — the public
    # report shows only the simulated OROS books. Set OROS_PERSONAL_BOOK=1
    # to re-include the private section locally.
    my_book = None
    if os.environ.get("OROS_PERSONAL_BOOK", "0") == "1":
        try:
            from ml.my_portfolio import analyze_book
            my_book = analyze_book()
        except Exception as e:  # noqa: BLE001
            logger.warning("my_portfolio analysis failed: %s", e)
    # Capstone comparison (Fordham, due 7/29): frozen first portfolio vs the
    # evolving book vs SPY, all simulated $100k. Chain appends today's live
    # day-return first so the comparison includes today.
    capstone = None
    try:
        from ml.capstone_tracker import (baseline_status, update_live_chain,
                                         live_chain_status)
        acct = float((rec or {}).get("account") or 100_000.0)
        if rec and pnl.get("rows"):
            update_live_chain(float(pnl.get("total_day_usd") or 0.0) / acct)
        capstone = baseline_status()
        if capstone:
            capstone["live"] = live_chain_status(capstone["account"])
    except Exception as e:  # noqa: BLE001
        logger.warning("capstone tracker failed: %s", e)
    picks, etf_picks = [], []
    try:
        from ml.top_picks import weinstein_top_picks, weinstein_etf_picks
        acct = float((rec or {}).get("account") or 100_000.0)
        picks = weinstein_top_picks(account=acct)
        etf_picks = weinstein_etf_picks(account=acct)
    except Exception as e:  # noqa: BLE001
        logger.warning("top picks failed: %s", e)
    try:
        from zoneinfo import ZoneInfo
        _now = datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        _now = datetime.now()
    breadth, oros500 = None, None
    try:
        from ml.breadth import latest_breadth
        breadth = latest_breadth()
    except Exception:
        pass
    try:
        from ml.oros500 import load_constituents
        oros500 = load_constituents()
        if oros500:
            oros500 = {k: v for k, v in oros500.items() if k != "symbols"}
    except Exception:
        pass
    return {"date": f"{_now:%A, %B %d, %Y}", "regime": regime, "vix": vix,
            "indexes": _index_snapshot(), "proposal": rec, "pnl": pnl,
            "scoreboard": scoreboard, "opps": opps, "changes": changes or {},
            "top_picks": picks, "etf_picks": etf_picks,
            "actions": _todays_actions(rec), "my_book": my_book,
            "capstone": capstone, "breadth": breadth, "oros500": oros500}


# ---------------------------------------------------------------------------
# PDF rendering (matplotlib only)
# ---------------------------------------------------------------------------
def _esc(s: str) -> str:
    """Escape '$' so matplotlib doesn't parse dollar amounts as mathtext."""
    return str(s).replace("$", r"\$")


def _text_page(pdf, lines: list[tuple[str, dict]], title: str) -> None:
    import matplotlib.pyplot as plt
    fig = plt.figure(figsize=(8.5, 11))
    fig.text(0.07, 0.955, title, fontsize=16, fontweight="bold")
    y = 0.915
    for text, style in lines:
        text = _esc(text)
        fig.text(0.07, y, text, fontsize=style.get("size", 10),
                 fontweight=style.get("weight", "normal"),
                 color=style.get("color", "#111111"), family="monospace")
        y -= style.get("dy", 0.022)
        if y < 0.05:
            break
    fig.text(0.07, 0.025, "OROS proposes and journals only — it never trades. "
             "Educational, not financial advice.", fontsize=7, color="#888888")
    pdf.savefig(fig)
    plt.close(fig)


def render_pdf(data: dict, out_path: Path | None = None) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    try:
        from zoneinfo import ZoneInfo
        _today = datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        _today = datetime.now()
    out = out_path or _REPORT_DIR / f"oros_report_{_today:%Y-%m-%d}.pdf"
    reg_color = {"bull": "#1a7f37", "neutral": "#9a6700", "bear": "#cf222e"}
    with PdfPages(out) as pdf:
        # ---- Page 1: market state + portfolio summary -------------------
        lines: list[tuple[str, dict]] = []
        lines.append(("Signal analysis and trend/timing reads for the coming weeks — "
                      "every position with a defined exit —", {"size": 9, "dy": 0.018,
                                                               "color": "#57606a"}))
        lines.append(("graded by a self-reflection layer (OROS Agentic Intelligence) "
                      "that analyzes its own decisions.", {"size": 9, "dy": 0.024,
                                                           "color": "#57606a"}))
        lines.append((f"{data['date']}", {"size": 11, "dy": 0.03}))
        lines.append((f"MARKET REGIME: {data['regime'].upper()}",
                      {"size": 13, "weight": "bold", "dy": 0.03,
                       "color": reg_color.get(data["regime"], "#333333")}))
        if data.get("vix"):
            v = data["vix"]
            lines.append((f"VIX: {v.get('level', '?')}  ({v.get('hint', '')})",
                          {"dy": 0.028}))
        lines.append(("INDEX SNAPSHOT (150-day MA ~ Weinstein 30-week)",
                      {"weight": "bold", "dy": 0.026}))
        lines.append((f"{'':6}{'close':>10}{'day %':>8}{'5d %':>8}"
                      f"{'vs MA %':>9}  MA trend", {"dy": 0.022}))
        for r in data["indexes"]:
            lines.append((f"{r['ticker']:6}{r['close']:>10,.2f}{r['day_pct']:>8.2f}"
                          f"{r['wk_pct']:>8.2f}{r['vs_ma_pct']:>9.2f}  {r['ma_trend']}",
                          {"dy": 0.02}))
        lines.append(("", {"dy": 0.02}))

        rec, pnl = data.get("proposal"), data["pnl"]
        if rec:
            import textwrap as _tw
            acct = float(rec.get("account") or 0)
            inv = float(rec.get("invested") or 0)
            cash = float(rec.get("cash") or 0)
            lines.append(("PORTFOLIO HOLDINGS — the refined OROS portfolio "
                          f"(paper · {rec.get('date', '?')} · mandate {rec.get('mandate', '?')})",
                          {"weight": "bold", "dy": 0.026}))
            ch0 = data.get("changes") or {}
            added = set(ch0.get("added") or [])
            names = [h["ticker"] + ("*" if h["ticker"] in added else "")
                     for h in rec.get("holdings", [])]
            for ln in _tw.wrap(f"Positions ({len(names)}): " + ", ".join(names), 88)[:3]:
                lines.append((ln, {"dy": 0.021}))
            if added:
                lines.append(("  * added today", {"size": 8.5, "dy": 0.02, "color": "#1a7f37"}))
            if ch0.get("removed"):
                lines.append(("Dropped today: " + ", ".join(ch0["removed"]),
                              {"size": 9, "dy": 0.022, "color": "#cf222e"}))
            lines.append((f"Account (total to work with): ${acct:,.0f}", {"dy": 0.021}))
            lines.append((f"Deployed in positions: ${inv:,.0f} ({inv/max(acct,1):.0%})   "
                          f"Held as cash: ${cash:,.0f} ({cash/max(acct,1):.0%})", {"dy": 0.022}))
            lines.append(("The regime cap and risk gates decide the deploy/cash split — "
                          "cash is a decision, not leftovers.", {"size": 8.5, "dy": 0.024,
                                                                 "color": "#57606a"}))
            up = pnl.get("total_unreal_usd", 0.0)
            lines.append((f"UNREALIZED P&L: ${up:+,.0f} "
                          f"({pnl.get('total_unreal_pct', 0):+.2f}%)   "
                          f"Today: ${pnl.get('total_day_usd', 0):+,.0f}",
                          {"size": 12, "weight": "bold", "dy": 0.028,
                           "color": "#1a7f37" if up >= 0 else "#cf222e"}))
            if pnl.get("stops_hit"):
                lines.append(("⚠ BELOW STOP: " + ", ".join(pnl["stops_hit"]),
                              {"weight": "bold", "color": "#cf222e", "dy": 0.026}))
        # ---- Today's Actions (the exit workflow, pre-chewed) --------------
        acts = data.get("actions") or []
        lines.append(("TODAY'S ACTIONS", {"weight": "bold", "dy": 0.024}))
        if acts:
            for a in acts[:10]:
                color = "#cf222e" if a.startswith("EXIT") else \
                        "#9a6700" if a.startswith("REVIEW") else "#1a7f37"
                lines.append((f"  • {a}", {"dy": 0.021, "color": color, "size": 9}))
        else:
            lines.append(("  Nothing to do today. The book is behaving — "
                          "let the winners run.", {"dy": 0.022, "color": "#1a7f37"}))
        lines.append(("", {"dy": 0.006}))

        ch = data.get("changes") or {}
        if ch.get("added") or ch.get("removed"):
            lines.append(("CHANGES vs previous run:", {"weight": "bold", "dy": 0.024}))
            lines.append((f"  added:   {', '.join(ch.get('added') or []) or '—'}",
                          {"dy": 0.02}))
            lines.append((f"  dropped: {', '.join(ch.get('removed') or []) or '—'}",
                          {"dy": 0.024}))
        opps = data.get("opps") or {}
        lines.append(("SCAN SUMMARY", {"weight": "bold", "dy": 0.024}))
        bts = opps.get("breakouts") or []
        lines.append((f"  IPO breakouts: "
                      f"{', '.join(o['ticker'] for o in bts[:8]) or 'none'}", {"dy": 0.02}))
        lines.append((f"  Young names on watch: {len(opps.get('watch') or [])}",
                      {"dy": 0.024}))
        # The four experimental strategy books are research plumbing, not
        # the product (JP, 2026-07-28): off the report by default — the
        # journals keep grading them silently. OROS_SHOW_SCOREBOARD=1 to
        # re-include locally for research.
        if data.get("scoreboard") and os.environ.get(
                "OROS_SHOW_SCOREBOARD", "0") == "1":
            lines.append(("PAPER-TRADE SCOREBOARD (forward, stop-aware, "
                          "graded at each mandate's own horizon)",
                          {"weight": "bold", "dy": 0.024}))
            lines.append((f"  {'mandate':<16}{'graded':>7}{'hit':>6}{'avg win':>9}"
                          f"{'avg loss':>9}{'expect':>8}{'stopped':>8}", {"dy": 0.02}))
            def _f(x):
                return "—" if x is None else f"{x:+.2f}%"
            for m, v in data["scoreboard"].items():
                hr, st_ = v.get("hit_rate_pct"), v.get("stop_exit_pct")
                lines.append((f"  {m:<16}{v.get('positions_scored') or 0:>7}"
                              f"{'—' if hr is None else f'{hr:.0f}%':>6}"
                              f"{_f(v.get('avg_win_pct')):>9}{_f(v.get('avg_loss_pct')):>9}"
                              f"{_f(v.get('expectancy_pct')):>8}"
                              f"{'—' if st_ is None else f'{st_:.0f}%':>8}",
                              {"dy": 0.02}))
            lines.append(("  graded = positions with a final outcome (a count, not dollars). "
                          "stopped = share of those that", {"size": 8, "dy": 0.017}))
            lines.append(("  exited via their stop rather than reaching the time horizon. "
                          "Expectancy = hit*avg_win + (1-hit)*avg_loss;", {"size": 8, "dy": 0.017}))
            lines.append(("  positive means the approach makes money per position.",
                          {"size": 8, "dy": 0.022}))
        _text_page(pdf, lines, "OROS Daily Report")

        # ---- Plain-language guide (BUILT here, RENDERED at the end — JP's
        # readability pass: lead with signal, close with education) ----------
        g: list[tuple[str, dict]] = []
        _H = {"weight": "bold", "dy": 0.024}
        _B = {"dy": 0.0205, "size": 9}
        g.append(("WHAT OROS IS", _H))
        g.append(("  Software that studies the US stock market every day, proposes a", _B))
        g.append(("  practice portfolio, and emails this report. Its end goal: maximize", _B))
        g.append(("  risk-adjusted gains through market analysis by a team of AI agents,", _B))
        g.append(("  while publishing its honest numbers even when they argue against it.", _B))
        g.append(("  It never trades and never touches real money. Everything here is", _B))
        g.append(("  simulated practice.", _B))
        g.append(("MARKET REGIME (page 1)", _H))
        g.append(("  The overall health check. SPY is a fund that tracks the S&P 500,", _B))
        g.append(("  the 500 largest US companies - we use it as 'the market'. If SPY", _B))
        g.append(("  sits above its own 30-week average price and that average is rising,", _B))
        g.append(("  conditions are healthy (BULL). VIX is the market's fear gauge - it", _B))
        g.append(("  measures how much turbulence investors expect. When fear spikes,", _B))
        g.append(("  OROS gets more cautious even inside a healthy trend.", _B))
        g.append(("THE 30-WEEK MA (moving average)", _H))
        g.append(("  The average price over the past 30 weeks - a slow trend line.", _B))
        g.append(("  Above a rising line = uptrend. It also serves as the safety-net", _B))
        g.append(("  level: a weekly close below it is the signal to get out.", _B))
        g.append(("STOP", _H))
        g.append(("  A pre-chosen exit price under every position. It caps how much any", _B))
        g.append(("  single idea can lose. Stops only ever move UP, never down.", _B))
        g.append(("RISK WALLET", _H))
        g.append(("  The worst-case total loss if every position hit its exit at once.", _B))
        g.append(("  Hard-capped at 6% of the account: once the wallet is spent, OROS", _B))
        g.append(("  stops buying, no matter how good the next signal looks.", _B))
        g.append(("OROS AGENTIC INTELLIGENCE (the self-reflection layer)", _H))
        g.append(("  OROS grades its own past decisions and changes its behavior based on", _B))
        g.append(("  the result. It checks its recent report card before sizing new", _B))
        g.append(("  positions: when its picks have been losing money, it automatically", _B))
        g.append(("  bets smaller (three-quarter, half, then quarter size) and must win", _B))
        g.append(("  its way back to full size. The same layer picks which strategy to", _B))
        g.append(("  trust (only ones that have made money forward) and tilts strategy", _B))
        g.append(("  weights toward what worked in similar past market conditions.", _B))
        g.append(("EXPECTANCY (the scoreboard)", _H))
        g.append(("  The average money made or lost per position, counting wins AND", _B))
        g.append(("  losses. Positive = the approach makes money over time. We publish", _B))
        g.append(("  it even when it is negative - especially when it is negative.", _B))
        g.append(("MY HOLDINGS (your real book)", _H))
        g.append(("  The owner can register their actual brokerage positions and OROS", _B))
        g.append(("  watches them daily with the same discipline as its own paper book:", _B))
        g.append(("  every position gets a stop, stops only ratchet up, and the actions", _B))
        g.append(("  list covers the real holdings. Tracked only - OROS never trades or", _B))
        g.append(("  touches the account, and its readings are not personalized advice.", _B))
        g.append(("TOP PICKS", _H))
        g.append(("  Names that passed every test today, each with a full plan: an entry", _B))
        g.append(("  trigger, an exit price, and a target. The chart pages show the", _B))
        g.append(("  evidence behind each signal - nothing is a black box.", _B))
        g.append(("", {"dy": 0.012}))
        g.append(("DATA SOURCES & METHOD", _H))
        g.append(("  Prices: Yahoo Finance. Company filings: SEC EDGAR. Economic context", _B))
        g.append(("  (interest-rate spreads, credit conditions): US Federal Reserve (FRED).", _B))
        g.append(("  Method: trend/stage analysis after Stan Weinstein, blended with the", _B))
        g.append(("  frameworks of Minervini, O'Neil, Graham and Lynch. Educational only;", _B))
        g.append(("  not financial advice.", _B))
        # (guide renders at the end of the report)

        # ---- Agent roles page: who did what (built here, rendered at end) ---
        trace = (rec or {}).get("agent_trace") or []
        if trace:
            import textwrap
            a: list[tuple[str, dict]] = []
            a.append(("OROS is a team of specialist agents, not one program. Each has", _B))
            a.append(("one job and hands its output to the next - the same trace is", _B))
            a.append(("journaled with every build, so each decision is auditable.", _B))
            a.append(("", {"dy": 0.012}))
            for s in trace:
                a.append((f"{s.get('agent', '?')} — {s.get('role', '')}",
                          {"weight": "bold", "dy": 0.022}))
                for ln in textwrap.wrap(s.get("summary", ""), 84)[:3]:
                    a.append((f"  {ln}", _B))
                if s.get("agent") == "RouterAgent":
                    a.append(("  A retrieval (RAG) system over OROS's strategy library — the", _B))
                    a.append(("  published methods of Stan Weinstein, Mark Minervini, William", _B))
                    a.append(("  O'Neil, Benjamin Graham, and Peter Lynch — weighting each", _B))
                    a.append(("  author's playbook for the current market regime.", _B))
                a.append(("", {"dy": 0.006}))
            a.append(("", {"dy": 0.01}))
            a.append(("ResearchAgent — deep-dive on the #1 pick", {"weight": "bold", "dy": 0.022}))
            a.append(("  A classifier routes the day's top pick to one of 18 industry", _B))
            a.append(("  specialist agents (REIT, Bank, Biotech, Semiconductors, Software,", _B))
            a.append(("  AI Compute, Energy, Utilities, Insurance, Defense, Industrials,", _B))
            a.append(("  Pharma/MedDevice, Consumer, Healthcare, Infrastructure, Tech,", _B))
            a.append(("  Fund, Generic), supported by a Chartist and a Sentiment Analyst.", _B))
            a.append(("  Each specializes in abstracting what matters in its industry from", _B))
            a.append(("  the company's SEC filings, and writes the attached memo with", _B))
            a.append(("  every claim cited to its source.", _B))
            a.append(("", {"dy": 0.012}))
            a.append(("Guardrail: no agent can place a trade. The whole team proposes and", _B))
            a.append(("journals only.", _B))
            # (agents page renders near the end of the report)

        # ---- Page 3: Weinstein Top Picks (stocks + ETF track) -------------
        tp = data.get("top_picks") or []
        etp = data.get("etf_picks") or []
        if tp or etp:
            fig, ax = plt.subplots(figsize=(8.5, 11))
            ax.axis("off")
            ax.set_title("Weinstein Top Picks — stocks + ETF track",
                         fontsize=14, fontweight="bold", loc="left")
            cells = [["Stock" if not p.get("is_etf") else "ETF",
                      p["ticker"], p["setup"], f"{p['last_close']:.2f}",
                      f"{p['buy_stop']:.2f}", f"{p['stop_loss']:.2f}",
                      f"{p['target']:.2f}", f"{p['risk_reward']:.1f}",
                      f"{p['bull_prob']:.0%}", f"{p['pct_above_ma']:+.1f}%",
                      f"{p['shares_1pct']}", _esc(f"${p['risk_dollars']:,}")]
                     for p in (tp + etp)]
            tbl = ax.table(cellText=cells, colLabels=[
                "Type", "Ticker", "Setup", "Last", "Buy-stop", "Stop", "Target",
                "R/R", "Bull", "vs 30w MA", _esc("Sh @1%"), _esc("Risk $")],
                loc="upper center", cellLoc="right")
            tbl.auto_set_font_size(False)
            tbl.set_fontsize(8)
            tbl.scale(1, 1.4)
            fig.text(0.07, 0.60,
                     "Every pick passed the complete Weinstein gauntlet:\n"
                     "  1. Stage 2 (Advancing) — the only buying stage\n"
                     "  2. No completed weekly close back below the 30-week MA\n"
                     "  3. Breakout volume surge AND rising Mansfield RS vs SPY\n"
                     "  4. Not extended above the rising 30-week MA\n\n"
                     "Trade plan discipline: enter ONLY if price takes out the buy-stop.\n"
                     "Exit on a weekly close below the stop (the 30-week MA invalidator).\n"
                     "Sizes shown risk 1% of the account per name — an illustration of\n"
                     "disciplined sizing, not an instruction.",
                     fontsize=9, family="monospace", va="top")
            fig.text(0.07, 0.05, "OROS proposes and journals only — it never trades. "
                     "Educational, not financial advice.", fontsize=7, color="#888888")
            pdf.savefig(fig)
            plt.close(fig)

        # ---- Signal chart pages: one per Top Pick --------------------------
        # "Where did this signal come from?" Each pick is shown the way
        # Weinstein reads it: weekly price vs the 30-week MA with the anchored
        # breakout bar and the full trade plan drawn in, breakout volume, and
        # the Mansfield relative-strength line vs SPY.
        for p in (tp[:5] + etp[:3]):
            try:
                _pick_chart_page(pdf, plt, p)
            except Exception as e:  # noqa: BLE001
                logger.warning("chart page failed for %s: %s", p.get("ticker"), e)

        # ---- Page 3: holdings gains/losses table -------------------------
        rows = pnl.get("rows") or []
        if rows:
            fig, ax = plt.subplots(figsize=(8.5, 11))
            ax.axis("off")
            ax.set_title("Portfolio Holdings — gains & losses (paper)", fontsize=14,
                         fontweight="bold", loc="left")
            cells = [[r["ticker"], f"{r['shares']:.0f}", f"{r['entry']:.2f}",
                      f"{r['last']:.2f}", f"{r['stop']:.2f}",
                      f"{r['day_pct']:+.2f}%", _esc(f"${r['day_usd']:+,.0f}"),
                      f"{r['unreal_pct']:+.2f}%", _esc(f"${r['unreal_usd']:+,.0f}")]
                     for r in rows]
            tbl = ax.table(cellText=cells, colLabels=[
                "Ticker", "Shares", "Entry", "Last", "Stop",
                "Day %", _esc("Day $"), "Gain %", _esc("Gain $")],
                loc="upper center", cellLoc="right")
            tbl.auto_set_font_size(False)
            tbl.set_fontsize(8)
            tbl.scale(1, 1.35)
            # Highlight what readers want at a glance: the DAY move (JP,
            # 07/08). The paper book re-enters daily, so Unreal is ~$0 by
            # construction — keep it plain; the capstone page carries the
            # accruing-gains story.
            for j, r in enumerate(rows, start=1):
                color = "#1a7f37" if r["day_usd"] >= 0 else "#cf222e"
                tbl[j, 6].set_text_props(color=color, fontweight="bold")
                tbl[j, 5].set_text_props(color=color, fontweight="bold")
                if r["below_stop"]:
                    tbl[j, 0].set_text_props(color="#cf222e", fontweight="bold")
            pdf.savefig(fig)
            plt.close(fig)

        # ---- Capstone comparison page (frozen 07-06 book vs live vs SPY) ---
        cs = data.get("capstone")
        if cs and cs.get("rows"):
            fig, ax = plt.subplots(figsize=(8.5, 11))
            ax.axis("off")
            ax.set_title("Capstone Comparison — First Portfolio (frozen 07/06) "
                         "to 07/29", fontsize=14, fontweight="bold", loc="left")
            cells = [[r["ticker"], f"{r['shares']:.0f}", f"{r['entry']:.2f}",
                      f"{r['last']:.2f}", f"{r['day_pct']:+.2f}%",
                      _esc(f"${r['day_usd']:+,.0f}"),
                      f"{r['unreal_pct']:+.2f}%", _esc(f"${r['unreal_usd']:+,.0f}")]
                     for r in cs["rows"]]
            tbl = ax.table(cellText=cells, colLabels=[
                "Ticker", "Shares", "Entry 07/06", "Last", "Day %",
                _esc("Day $"), "Gain %", _esc("Gain $")],
                loc="upper center", cellLoc="right")
            tbl.auto_set_font_size(False)
            tbl.set_fontsize(8)
            tbl.scale(1, 1.35)
            for j, r in enumerate(cs["rows"], start=1):
                color = "#1a7f37" if r["unreal_usd"] >= 0 else "#cf222e"
                tbl[j, 7].set_text_props(color=color, fontweight="bold")
                tbl[j, 6].set_text_props(color=color)
            y0 = 0.55
            ret_c = "#1a7f37" if cs["return_pct"] >= 0 else "#cf222e"
            fig.text(0.07, y0, _esc(
                f"FIRST PORTFOLIO (frozen, buy-and-hold): ${cs['value']:,.0f}  "
                f"({cs['return_pct']:+.2f}% since {cs['start_date']})"),
                fontsize=11, fontweight="bold", color=ret_c)
            lv = cs.get("live") or {}
            if lv:
                lc = "#1a7f37" if lv["return_pct"] >= 0 else "#cf222e"
                fig.text(0.07, y0 - 0.03, _esc(
                    f"LIVE OROS BOOK (evolving, chained daily returns): "
                    f"${lv['value']:,.0f}  ({lv['return_pct']:+.2f}% over "
                    f"{lv['days']} trading day(s))"), fontsize=11,
                    fontweight="bold", color=lc)
            if cs.get("spy_value"):
                fig.text(0.07, y0 - 0.06, _esc(
                    f"SPY (same $100,000 from the 07/06 close): "
                    f"${cs['spy_value']:,.0f}  ({cs['spy_return_pct']:+.2f}%)"),
                    fontsize=11, fontweight="bold", color="#57606a")
            fig.text(0.07, y0 - 0.13,
                     "Method, for honest comparison: all three start at the same "
                     "$100,000 on 2026-07-06.\nThe First Portfolio is frozen — "
                     "buy-and-hold at the journaled entries, cash earns 0%.\n"
                     "The Live book rebuilds daily, so its cumulative value chains "
                     "each day's return of the\nbook actually held that day (the "
                     "chain only appends; nothing is restated).\nSimulated money "
                     "throughout. Educational, not financial advice.",
                     fontsize=9, color="#57606a", va="top")
            pdf.savefig(fig)
            plt.close(fig)

        # ---- Your real book (My Holdings, tracked only) --------------------
        mb = data.get("my_book")
        if mb and mb.get("rows"):
            fig, ax = plt.subplots(figsize=(8.5, 11))
            ax.axis("off")
            ax.set_title(_esc(f"{mb['name']} — your real holdings (tracked only)"),
                         fontsize=14, fontweight="bold", loc="left")
            cells = [[r["ticker"], f"{r['shares']:.0f}", f"{r['basis']:.2f}",
                      f"{r['last']:.2f}", f"{r['stop']:.2f}", f"{r['stage']}",
                      f"{r['day_pct']:+.2f}%", _esc(f"${r['day_usd']:+,.0f}"),
                      f"{r['pnl_pct']:+.1f}%", _esc(f"${r['pnl_usd']:+,.0f}")]
                     for r in mb["rows"]]
            tbl = ax.table(cellText=cells, colLabels=[
                "Ticker", "Shares", "Basis", "Last", "Stop", "Stage",
                "Day %", _esc("Day $"), _esc("P&L %"), _esc("P&L $")],
                loc="upper center", cellLoc="right")
            tbl.auto_set_font_size(False)
            tbl.set_fontsize(8)
            tbl.scale(1, 1.35)
            for j, r in enumerate(mb["rows"], start=1):
                color = "#1a7f37" if r["pnl_usd"] >= 0 else "#cf222e"
                tbl[j, 9].set_text_props(color=color, fontweight="bold")
                tbl[j, 8].set_text_props(color=color)
                if r["below_stop"] or r["stage"] in (3, 4):
                    tbl[j, 0].set_text_props(color="#cf222e", fontweight="bold")
            lev = mb["leverage"]
            y0 = 0.55
            fig.text(0.07, y0, _esc(
                f"Equity ${mb['equity']:,.0f}  =  positions ${mb['market_value']:,.0f} "
                f"minus margin ${mb['margin_debt']:,.0f}   ·   leverage {lev:.2f}x"),
                fontsize=10, fontweight="bold")
            fig.text(0.07, y0 - 0.03, _esc(
                f"Today {mb['day_usd']:+,.0f}   ·   vs cost basis "
                f"{mb['total_pnl_usd']:+,.0f} ({mb['total_pnl_pct']:+.1f}%)   ·   "
                f"if every stop were honored tomorrow: -${mb['loss_to_stops']:,.0f} "
                f"({mb['loss_to_stops_pct_equity']:.1f}% of equity)"), fontsize=9)
            if lev > 1.05:
                fig.text(0.07, y0 - 0.06, _esc(
                    f"Margin note: at {lev:.2f}x leverage a 10% market move swings equity "
                    f"~{10*lev:.0f}%, both directions, plus daily interest."),
                    fontsize=9, color="#cf222e")
            ya = y0 - 0.10
            fig.text(0.07, ya, "ACTIONS ON YOUR BOOK TODAY", fontsize=10, fontweight="bold")
            acts = mb.get("actions") or []
            if acts:
                for i, aline in enumerate(acts[:8]):
                    col = "#cf222e" if aline.startswith("EXIT") else \
                          "#9a6700" if aline.startswith("REVIEW") else "#1a7f37"
                    fig.text(0.07, ya - 0.025 * (i + 1), _esc("• " + aline),
                             fontsize=9, color=col)
            else:
                fig.text(0.07, ya - 0.025, "• Nothing needs your hand today.",
                         fontsize=9, color="#1a7f37")
            fig.text(0.07, 0.05, "House rule: every position has a stop; stops ratchet up "
                     "under the rising 30-week MA, never down. OROS tracks this book only — "
                     "it never trades or touches the account. System readings, not "
                     "personalized advice; the decisions stay yours.",
                     fontsize=7, color="#888888")
            pdf.savefig(fig)
            plt.close(fig)

        # ---- Page 3: portfolio vs index & mutual funds --------------------
        if rec and rows:
            try:
                from ml.benchmark_compare import build_comparison
                acct = max(float(rec.get("account") or 1.0), 1.0)
                holdings = [(h["ticker"], float(h.get("weight_pct") or 0) / 100.0)
                            for h in rec.get("holdings", [])]
                cash_w = max(0.0, float(rec.get("cash") or 0) / acct)
                res = build_comparison(holdings, _BENCHMARKS,
                                       lookback_days=252, cash_weight=cash_w)
                if not res.curves.empty:
                    fig, (ax1, ax2) = plt.subplots(
                        2, 1, figsize=(8.5, 11),
                        gridspec_kw={"height_ratios": [3, 1]})
                    for col in res.curves.columns:
                        ax1.plot(res.curves.index, res.curves[col],
                                 linewidth=2.6 if col == "Portfolio" else 1.2,
                                 linestyle="-" if col == "Portfolio" else "--",
                                 label=col,
                                 color="#00a344" if col == "Portfolio" else None)
                    ax1.set_title(_esc("Growth of $100,000 — this book vs index & mutual funds"),
                                  fontsize=13, fontweight="bold", loc="left")
                    ax1.legend(loc="upper left", fontsize=8)
                    ax1.grid(alpha=0.25)
                    ax1.yaxis.set_major_formatter(
                        plt.matplotlib.ticker.StrMethodFormatter(r"\${x:,.0f}"))
                    ax2.axis("off")
                    st = res.stats
                    tbl = ax2.table(cellText=[[_esc(v) for v in row]
                                              for row in st.values.astype(str).tolist()],
                                    colLabels=[_esc(c) for c in st.columns],
                                    loc="upper center", cellLoc="right")
                    tbl.auto_set_font_size(False)
                    tbl.set_fontsize(8)
                    tbl.scale(1, 1.4)
                    fig.text(0.07, 0.045, res.note + "  A backtest of today's weights, "
                             "not the forward record. If the index fund keeps winning, "
                             "that itself is a finding.\nETF track note: plain sector/index/"
                             "commodity funds are valid picks in their own right (often the "
                             "safer expression of a signal); leveraged and inverse products "
                             "are excluded because daily-reset\nleverage makes their charts "
                             "measure arithmetic, not accumulation.",
                             fontsize=7, color="#888888")
                    pdf.savefig(fig)
                    plt.close(fig)
            except Exception as e:  # noqa: BLE001
                logger.warning("benchmark page failed: %s", e)

        # ---- Closing pages: the team, then the how-to guide ----------------
        if trace:
            _text_page(pdf, a, "The agents behind this report")
        _text_page(pdf, g, "How to read this report")

    logger.info("report written: %s", out)
    return out


def _weekly(df):
    """Daily OHLCV -> weekly bars (close = Friday/last, volume = weekly sum)."""
    import pandas as pd
    d = df.copy()
    d.index = pd.to_datetime(d.index)
    try:
        d.index = d.index.tz_localize(None)
    except TypeError:
        pass
    agg = {"Close": "last"}
    if "Volume" in d.columns:
        agg["Volume"] = "sum"
    return d.resample("W-FRI").agg(agg).dropna(subset=["Close"])


def _pick_chart_page(pdf, plt, p: dict) -> None:
    """One full-page Weinstein view of a Top Pick (PDF wrapper)."""
    fig = pick_chart_fig(plt, p)
    if fig is not None:
        pdf.savefig(fig)
        plt.close(fig)


def pick_chart_fig(plt, p: dict):
    """Build the Weinstein chart figure for a pick: price + 30-week MA with
    the trade plan, breakout volume, and Mansfield RS vs SPY. Shared by the
    PDF report and the daily video brief."""
    import pandas as pd
    from data import price_cache

    df = price_cache.get_history(p["ticker"])
    if df is None or len(df) < 160:
        return None
    wk = _weekly(df).tail(110)                       # ~2 years of weekly bars
    ma30 = _weekly(df)["Close"].rolling(30).mean().reindex(wk.index)

    fig, (ax1, ax2, ax3) = plt.subplots(
        3, 1, figsize=(8.5, 11), sharex=True,
        gridspec_kw={"height_ratios": [3.2, 1.0, 1.0]})
    try:
        from data.universe import company_name
        _nm = company_name(p["ticker"])
    except Exception:  # noqa: BLE001
        _nm = ""
    _head = f"{_nm} ({p['ticker']})" if _nm else p["ticker"]
    fig.suptitle(f"{_head} — Weinstein weekly view ({p['setup']} Stage 2 setup)",
                 fontsize=14, fontweight="bold", x=0.07, ha="left")

    # -- Panel 1: price, 30-week MA, breakout anchor, trade plan ----------
    ax1.plot(wk.index, wk["Close"], color="#1f77b4", linewidth=1.6, label="Weekly close")
    ax1.plot(ma30.index, ma30, color="#e0a800", linewidth=2.0, label="30-week MA")
    if p.get("breakout_date"):
        try:
            bd = pd.to_datetime(p["breakout_date"])
            ax1.scatter([bd], [p["breakout_price"]], marker="^", s=130,
                        color="#1a7f37", zorder=5)
            ax1.annotate(f"Stage 2 breakout\n{p['breakout_date']}"
                         f" @ {p['breakout_price']:.2f}",
                         xy=(bd, p["breakout_price"]),
                         xytext=(10, -35), textcoords="offset points",
                         fontsize=8, color="#1a7f37", fontweight="bold")
        except Exception:
            pass
    for level, color, label in [
            (p["buy_stop"], "#1a7f37", f"buy-stop {p['buy_stop']:.2f}"),
            (p["stop_loss"], "#cf222e", f"stop {p['stop_loss']:.2f} (30w MA invalidator)"),
            (p["target"], "#6f42c1", f"target {p['target']:.2f}")]:
        if level and level > 0:
            ax1.axhline(level, color=color, linestyle="--", linewidth=1.1)
            ax1.text(0.99, level, label + " ", fontsize=7.5, color=color,
                     va="bottom", ha="right", fontweight="bold",
                     transform=ax1.get_yaxis_transform())
    ax1.legend(loc="upper left", fontsize=8)
    ax1.grid(alpha=0.2)
    ax1.set_ylabel("Price")

    # -- Panel 2: weekly volume with surge highlighted ---------------------
    if "Volume" in wk.columns:
        vavg = wk["Volume"].rolling(30).mean()
        colors = ["#1a7f37" if v >= 1.5 * a else "#b0b7c0"
                  for v, a in zip(wk["Volume"], vavg.fillna(wk["Volume"].mean()))]
        ax2.bar(wk.index, wk["Volume"], width=5.0, color=colors)
        ax2.plot(vavg.index, vavg, color="#e0a800", linewidth=1.2)
        ax2.set_ylabel("Volume")
        ax2.text(0.01, 0.85, f"green = >=1.5x 30w avg · latest surge {p['volume_surge']:.1f}x",
                 transform=ax2.transAxes, fontsize=7.5, color="#57606a")
        ax2.grid(alpha=0.15)

    # -- Panel 3: Mansfield RS vs SPY --------------------------------------
    spy = price_cache.get_history("SPY")
    if spy is not None and "Close" in spy.columns:
        sw = _weekly(spy)["Close"].reindex(wk.index).ffill()
        ratio = (wk["Close"] / sw).dropna()
        rma = ratio.rolling(52, min_periods=26).mean()
        mans = ((ratio / rma) - 1.0) * 100
        ax3.plot(mans.index, mans, color="#1f77b4", linewidth=1.4)
        ax3.axhline(0, color="#57606a", linewidth=0.9)
        ax3.fill_between(mans.index, mans, 0, where=(mans >= 0),
                         color="#1a7f37", alpha=0.25)
        ax3.fill_between(mans.index, mans, 0, where=(mans < 0),
                         color="#cf222e", alpha=0.2)
        ax3.set_ylabel("Mansfield RS")
        ax3.text(0.01, 0.85, f"above zero = outperforming SPY · now {p['mansfield_rs']:+.1f}",
                 transform=ax3.transAxes, fontsize=7.5, color="#57606a")
        ax3.grid(alpha=0.15)

    fig.text(0.07, 0.03,
             _esc(f"Signals passed: Stage 2 · rising 30w MA · volume {p['volume_surge']:.1f}x · "
                  f"RS {p['mansfield_rs']:+.1f} · {p['pct_above_ma']:+.1f}% vs MA (not extended) · "
                  f"R/R {p['risk_reward']:.1f}. Enter only on breakout through the buy-stop; "
                  f"exit on a weekly close below the stop. Educational, not financial advice."),
             fontsize=7, color="#888888", wrap=True)
    fig.subplots_adjust(top=0.94, bottom=0.09, left=0.09, right=0.97, hspace=0.12)
    return fig


def _research_memo_pdf(data: dict) -> tuple[Path | None, str]:
    """Research Memo of the Day (Nathaniel's ask): run the multi-specialist
    research agent on the single best pick and render its cited memo to PDF.

    Subject = the #1 stock Top Pick, else the #1 ETF pick (funds route to the
    fund specialist). Costs a few cents of LLM; skipped cleanly when disabled
    (OROS_MEMO=0), when no pick passed the gates, or when no API key exists
    in this environment. Returns (path or None, status line for the email)."""
    if os.environ.get("OROS_MEMO", "1") == "0":
        return None, "Research memo: disabled (OROS_MEMO=0)."
    picks = (data.get("top_picks") or []) + (data.get("etf_picks") or [])
    if not picks:
        return None, ("Research memo: skipped — no pick passed the gates today "
                      "(no setup means no trade, and nothing to research).")
    t = picks[0]["ticker"]
    try:
        from agents.orchestrator import analyze
        res = analyze(t)
        memo = (res.memo or "").strip()
        if not memo:
            return None, (f"Research memo: the research agent could not complete "
                          f"{t} today (environment or API issue — the rest of the "
                          "report is unaffected).")
        out = _REPORT_DIR / f"research_{t}_{datetime.now():%Y-%m-%d}.pdf"
        try:
            from ui.components.memo_pdf import memo_to_pdf_bytes
            pdf_bytes = memo_to_pdf_bytes(memo, t)
        except Exception:
            pdf_bytes = None
        if pdf_bytes:
            out.write_bytes(pdf_bytes)
        else:                                   # fall back to markdown attachment
            out = out.with_suffix(".md")
            out.write_text(memo)
        try:                                     # LLM spend into the budget ledger
            from scripts.oros_run import record_cost
            record_cost(0.10)
        except Exception:
            pass
        logger.info("research memo written: %s (%s)", out.name, res.agent_name)
        return out, (f"Research Memo of the Day attached: {t} — analyzed by "
                     f"{res.agent_name or 'the research specialist'} "
                     "with SEC-cited claims.")
    except Exception as e:  # noqa: BLE001
        logger.warning("research memo failed for %s: %s", t, e)
        return None, f"Research memo: unavailable today ({type(e).__name__})."


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
def _summary_body(data: dict) -> str:
    pnl, rec = data["pnl"], data.get("proposal") or {}
    up = pnl.get("total_unreal_usd", 0.0)
    lines = [
        f"OROS Daily Report — {data['date']}",
        f"Market regime: {data['regime'].upper()}",
        "",
        f"Portfolio (paper): {len(pnl.get('rows') or [])} positions, "
        f"mandate {rec.get('mandate', '?')}",
        f"Unrealized P&L: ${up:+,.0f} ({pnl.get('total_unreal_pct', 0):+.2f}%) | "
        f"Today: ${pnl.get('total_day_usd', 0):+,.0f}",
    ]
    if pnl.get("stops_hit"):
        lines.append("BELOW STOP: " + ", ".join(pnl["stops_hit"]))
    acts = data.get("actions") or []
    lines.append("")
    lines.append("TODAY'S ACTIONS:")
    if acts:
        lines += [f"  - {a}" for a in acts[:10]]
    else:
        lines.append("  Nothing to do today. The book is behaving — let the winners run.")
    cs = data.get("capstone")
    if cs and cs.get("rows"):
        lines.append("")
        lines.append("CAPSTONE COMPARISON (simulated $100k each, since 07/06):")
        lines.append(f"  First Portfolio (frozen): ${cs['value']:,.0f} ({cs['return_pct']:+.2f}%)")
        lv = cs.get("live") or {}
        if lv:
            lines.append(f"  Live OROS book (chained): ${lv['value']:,.0f} ({lv['return_pct']:+.2f}%)")
        if cs.get("spy_value"):
            lines.append(f"  SPY benchmark: ${cs['spy_value']:,.0f} ({cs['spy_return_pct']:+.2f}%)")
    mb = data.get("my_book")
    if mb and mb.get("rows"):
        lines.append("")
        lines.append(f"YOUR REAL BOOK — {mb['name']} (tracked only):")
        lines.append(f"  Equity ${mb['equity']:,.0f} (leverage {mb['leverage']:.2f}x) | "
                     f"Today {mb['day_usd']:+,.0f} | vs basis {mb['total_pnl_usd']:+,.0f} "
                     f"({mb['total_pnl_pct']:+.1f}%)")
        macts = mb.get("actions") or []
        if macts:
            lines += [f"  - {a}" for a in macts[:8]]
        else:
            lines.append("  - Nothing needs your hand today.")
    bts = (data.get("opps") or {}).get("breakouts") or []
    if bts:
        lines.append("IPO breakouts: " + ", ".join(o["ticker"] for o in bts[:8]))
    try:
        from ml.top_picks import format_picks_text
        lines += ["", format_picks_text(data.get("top_picks") or [])]
        lines += ["", format_picks_text(data.get("etf_picks") or [],
                                        title="WEINSTEIN ETF PICKS (sector-first track)")]
    except Exception:
        pass
    lines += ["", "Full report attached (PDF).",
              "OROS proposes and journals only — it never trades. "
              "Educational, not financial advice."]
    return "\n".join(lines)


def _send_glance_digest(data: dict, yt_url: str | None) -> None:
    """The advisor's morning glance (JP, 2026-07-18): 'added X, Y; dropped Z'
    with one plain-language reason per name and the episode link. Everything
    here is read from the journaled proposal — zero extra computation."""
    import os as _os
    digest_to = [a.strip() for a in
                 _os.environ.get("OROS_DIGEST_TO", "").split(",")
                 if a.strip()]
    if not digest_to:
        return
    ch = data.get("changes") or {}
    added, dropped = ch.get("added") or [], ch.get("removed") or []
    rec = data.get("proposal") or {}
    hold = {h["ticker"]: h for h in rec.get("holdings", [])}
    try:
        from data.universe import named
    except Exception:  # noqa: BLE001
        def named(t):  # noqa: ANN001
            return t
    try:
        from ml.strategy_registry import display_name
    except Exception:  # noqa: BLE001
        def display_name(m):  # noqa: ANN001
            return m
    lines = [("added " + ", ".join(added) if added else "no adds")
             + ("; dropped " + ", ".join(dropped) if dropped else "; no drops")]
    if added:
        lines.append("")
        lines.append("Why they made the book:")
        for t in added[:8]:
            h = hold.get(t) or {}
            led = display_name(h.get("led_by")) if h.get("led_by") else None
            bits = [b for b in (h.get("sector"),
                                f"led by the {led} strategy" if led else None,
                                f"conviction {h['conviction']:.2f}"
                                if h.get("conviction") is not None else None) if b]
            lines.append(f"  {named(t)} — " + (", ".join(bits) or "passed every gate"))
    if dropped:
        try:
            from ml.daily_video import _change_reason
            lines.append("")
            lines.append("Why they left:")
            for t in dropped[:8]:
                lines.append(f"  {named(t)} — {_change_reason(t)}")
        except Exception:  # noqa: BLE001
            pass
    bench = rec.get("bench") or []
    if bench:
        lines.append("")
        lines.append("Considered but excluded (the bench):")
        for b in bench[:5]:
            lines.append(f"  {named(b['ticker'])} — {b['reason']}")
    if yt_url:
        lines.append("")
        lines.append(f"Today's briefing: {yt_url}")
    lines += ["", "Simulated paper trading. OROS proposes and journals only; "
              "it never trades. Educational, not financial advice."]
    from ml.notifier import send_email
    for addr in digest_to:
        send_email("OROS: portfolio update", "\n".join(lines), to=addr)


def send_daily_report(to: str | None = None, opps: dict | None = None,
                      changes: dict | None = None, force: bool = False,
                      email: bool = True, research: bool = True) -> Path:
    """Build + render + email the daily report (and, when possible, the
    Research Memo of the Day as a second attachment). Returns the PDF path.
    A per-date marker prevents double-sends when both the local launchd job and
    the cloud runner fire on the same day (marker is per machine)."""
    to = to or os.environ.get("OROS_REPORT_TO", "").strip() or DEFAULT_RECIPIENT
    data = build_report_data(opps=opps, changes=changes)
    # Give the morning picks memory: the Closing Bell grades them tonight.
    try:
        from ml.picks_ledger import record_picks
        record_picks((data.get("top_picks") or []) + (data.get("etf_picks") or []))
    except Exception as e:  # noqa: BLE001
        logger.warning("picks ledger record failed: %s", e)
    pdf = render_pdf(data)
    memo_path, memo_note = (None, "")
    if research:
        memo_path, memo_note = _research_memo_pdf(data)
    # The narrated video brief (charts + voice); falls back to the 60-second
    # audio-only broadcast when ffmpeg isn't available in this environment.
    audio_path, audio_note = (None, "")
    try:
        from ml.daily_video import generate_video_brief
        audio_path, audio_note = generate_video_brief(data)
    except Exception as e:  # noqa: BLE001
        logger.warning("video brief failed: %s", e)
    if audio_path is None:
        try:
            from ml.daily_broadcast import generate_broadcast
            audio_path, fallback_note = generate_broadcast(data)
            audio_note = (audio_note + " " + fallback_note).strip()
        except Exception as e:  # noqa: BLE001
            logger.warning("broadcast failed: %s", e)
    # Publish the episode to the OROS Market Desk channel (unlisted; skips
    # cleanly until the YT_* credentials exist — docs/YOUTUBE_SETUP.md).
    yt_url = None
    if audio_path is not None and str(audio_path).endswith(".mp4"):
        try:
            from ml.publish_youtube import publish_episode, episode_title
            yt_url, yt_note = publish_episode(audio_path, episode_title(data))
            if yt_note and "not configured" not in yt_note:
                audio_note = (audio_note + "\n" + yt_note).strip()
            elif yt_url:
                audio_note = (audio_note + "\n" + yt_note).strip()
        except Exception as e:  # noqa: BLE001
            logger.warning("youtube publish failed: %s", e)
    # At-a-glance digest for advisors (JP, 2026-07-18): adds/drops in one
    # line, one honest reason each, the watch link — no attachments.
    # Recipients: OROS_DIGEST_TO (comma-separated); Nathan Lara by default.
    if email:
        try:
            _send_glance_digest(data, yt_url)
        except Exception as e:  # noqa: BLE001
            logger.warning("glance digest failed: %s", e)
    marker = pdf.with_suffix(".sent")
    if email:
        if marker.exists() and not force:
            logger.info("report already sent today (%s); skipping email", marker.name)
        else:
            from ml.notifier import send_email
            body = _summary_body(data)
            if memo_note:
                body += "\n\n" + memo_note
            if audio_note:
                body += "\n" + audio_note
            # Email tightening (JP, 2026-07-29): episodes are public on
            # YouTube, so the email carries the WATCH LINK, not the MP4.
            # PDF + memo stay attached; the video attaches only as a
            # fallback when publishing failed (no yt_url).
            attachments = [pdf] + ([memo_path] if memo_path else [])
            if audio_path and not yt_url:
                attachments.append(audio_path)
            # Subject matches the channel's title style: date · regime · hook.
            try:
                from ml.publish_youtube import episode_title
                subject = episode_title(data).replace(
                    "OROS Market Desk", "OROS Daily", 1)
            except Exception:  # noqa: BLE001
                subject = (f"OROS Daily — {datetime.now():%b %d, %Y} · "
                           f"{data['regime'].upper()}")
            ok = send_email(subject, body, to=to, attachments=attachments)
            if ok:
                marker.write_text(datetime.utcnow().isoformat() + "Z")
    return pdf


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-email", action="store_true", help="render PDF only")
    ap.add_argument("--force", action="store_true", help="resend even if marked sent")
    ap.add_argument("--to", default=None)
    a = ap.parse_args()
    p = send_daily_report(to=a.to, force=a.force, email=not a.no_email)
    print("report:", p)
