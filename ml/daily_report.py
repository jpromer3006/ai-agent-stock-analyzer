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
    try:
        from ml.portfolio_journal import list_proposals, load_proposal
        props = list_proposals()
        return load_proposal(props[-1]) if props else None
    except Exception as e:  # noqa: BLE001
        logger.warning("no proposal available: %s", e)
        return None


def _holdings_pnl(rec: dict) -> dict:
    """Per-holding unrealized paper P&L since the journaled entry, plus the
    day's move. This is the 'watch portfolios and report gains/losses' core."""
    from data import price_cache
    rows, tot_cost, tot_val, tot_day = [], 0.0, 0.0, 0.0
    for h in rec.get("holdings", []):
        df = price_cache.get_history(h["ticker"])
        if df is None or len(df) < 2 or "Close" not in df.columns:
            continue
        c = df["Close"].dropna()
        last, prev = float(c.iloc[-1]), float(c.iloc[-2])
        entry = float(h.get("entry") or last)
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
    return {"date": f"{datetime.now():%A, %B %d, %Y}", "regime": regime, "vix": vix,
            "indexes": _index_snapshot(), "proposal": rec, "pnl": pnl,
            "scoreboard": scoreboard, "opps": opps, "changes": changes or {}}


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

    out = out_path or _REPORT_DIR / f"oros_report_{datetime.now():%Y-%m-%d}.pdf"
    reg_color = {"bull": "#1a7f37", "neutral": "#9a6700", "bear": "#cf222e"}
    with PdfPages(out) as pdf:
        # ---- Page 1: market state + portfolio summary -------------------
        lines: list[tuple[str, dict]] = []
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
            acct = float(rec.get("account") or 0)
            lines.append(("PORTFOLIO (paper, latest proposal "
                          f"{rec.get('date', '?')} · mandate {rec.get('mandate', '?')})",
                          {"weight": "bold", "dy": 0.026}))
            lines.append((f"Positions: {len(pnl['rows'])}   "
                          f"Invested: ${rec.get('invested', 0):,.0f}   "
                          f"Cash: ${rec.get('cash', 0):,.0f}   "
                          f"Account: ${acct:,.0f}", {"dy": 0.024}))
            up = pnl.get("total_unreal_usd", 0.0)
            lines.append((f"UNREALIZED P&L: ${up:+,.0f} "
                          f"({pnl.get('total_unreal_pct', 0):+.2f}%)   "
                          f"Today: ${pnl.get('total_day_usd', 0):+,.0f}",
                          {"size": 12, "weight": "bold", "dy": 0.028,
                           "color": "#1a7f37" if up >= 0 else "#cf222e"}))
            if pnl.get("stops_hit"):
                lines.append(("⚠ BELOW STOP: " + ", ".join(pnl["stops_hit"]),
                              {"weight": "bold", "color": "#cf222e", "dy": 0.026}))
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
        if data.get("scoreboard"):
            lines.append(("PAPER-TRADE SCOREBOARD (forward, out-of-sample)",
                          {"weight": "bold", "dy": 0.024}))
            for m, v in data["scoreboard"].items():
                ar = v.get("avg_realized_pct")
                hr = v.get("avg_hit_rate_pct")
                lines.append((f"  {m:<18} proposals {v.get('proposals', 0):>3}  "
                              f"avg {'—' if ar is None else f'{ar:+.2f}%':>8}  "
                              f"hit {'—' if hr is None else f'{hr:.0f}%':>5}", {"dy": 0.02}))
        _text_page(pdf, lines, "OROS Daily Report")

        # ---- Page 2: holdings gains/losses table -------------------------
        rows = pnl.get("rows") or []
        if rows:
            fig, ax = plt.subplots(figsize=(8.5, 11))
            ax.axis("off")
            ax.set_title("Holdings — gains & losses (paper)", fontsize=14,
                         fontweight="bold", loc="left")
            cells = [[r["ticker"], f"{r['shares']:.0f}", f"{r['entry']:.2f}",
                      f"{r['last']:.2f}", f"{r['stop']:.2f}",
                      f"{r['day_pct']:+.2f}%", _esc(f"${r['day_usd']:+,.0f}"),
                      f"{r['unreal_pct']:+.2f}%", _esc(f"${r['unreal_usd']:+,.0f}")]
                     for r in rows]
            tbl = ax.table(cellText=cells, colLabels=[
                "Ticker", "Shares", "Entry", "Last", "Stop",
                "Day %", _esc("Day $"), "Unreal %", _esc("Unreal $")],
                loc="upper center", cellLoc="right")
            tbl.auto_set_font_size(False)
            tbl.set_fontsize(8)
            tbl.scale(1, 1.35)
            for j, r in enumerate(rows, start=1):
                color = "#1a7f37" if r["unreal_usd"] >= 0 else "#cf222e"
                tbl[j, 8].set_text_props(color=color, fontweight="bold")
                tbl[j, 7].set_text_props(color=color)
                if r["below_stop"]:
                    tbl[j, 0].set_text_props(color="#cf222e", fontweight="bold")
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
                    ax1.set_title(_esc("Growth of $10,000 — this book vs index & mutual funds"),
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
                    fig.text(0.07, 0.02, res.note + "  A backtest of today's weights, "
                             "not the forward record. If the index fund keeps winning, "
                             "that itself is a finding.", fontsize=7, color="#888888")
                    pdf.savefig(fig)
                    plt.close(fig)
            except Exception as e:  # noqa: BLE001
                logger.warning("benchmark page failed: %s", e)

    logger.info("report written: %s", out)
    return out


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
    bts = (data.get("opps") or {}).get("breakouts") or []
    if bts:
        lines.append("IPO breakouts: " + ", ".join(o["ticker"] for o in bts[:8]))
    lines += ["", "Full report attached (PDF).",
              "OROS proposes and journals only — it never trades. "
              "Educational, not financial advice."]
    return "\n".join(lines)


def send_daily_report(to: str | None = None, opps: dict | None = None,
                      changes: dict | None = None, force: bool = False,
                      email: bool = True) -> Path:
    """Build + render + email the daily report. Returns the PDF path.
    A per-date marker prevents double-sends when both the local launchd job and
    the cloud runner fire on the same day (marker is per machine)."""
    to = to or os.environ.get("OROS_REPORT_TO", "").strip() or DEFAULT_RECIPIENT
    data = build_report_data(opps=opps, changes=changes)
    pdf = render_pdf(data)
    marker = pdf.with_suffix(".sent")
    if email:
        if marker.exists() and not force:
            logger.info("report already sent today (%s); skipping email", marker.name)
        else:
            from ml.notifier import send_email
            ok = send_email(f"OROS Daily Report — {datetime.now():%b %d, %Y} — "
                            f"{data['regime'].upper()}",
                            _summary_body(data), to=to, attachments=[pdf])
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
