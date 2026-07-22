"""
trader_mode.py — Streamlit UI for Weinstein Stage Analysis.

Layout:
    - Top: watchlist picker + "Scan Now" button
    - Leaderboard: Stage 2 breakouts + Stage 4 breakdowns side-by-side
    - Full table with all tickers sortable
    - Detail view: click a ticker to see price chart + 30-week MA + signal panel
"""

from __future__ import annotations

import hashlib

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from data.watchlists import (
    DEFAULT_NAME,
    add_ticker as _add_ticker,
    delete_watchlist,
    list_watchlists,
    load_watchlist,
    remove_ticker as _remove_ticker,
    save_watchlist,
)
from ui.components.disclaimer import render_disclaimer
from ml.batch_scanner import ScanReport, scan_universe
from ml.stage_analyzer import StageResult, analyze_stage


# ---------------------------------------------------------------------------
# Stage colors (used everywhere)
# ---------------------------------------------------------------------------
STAGE_COLOR = {1: "#6c757d", 2: "#00c853", 3: "#ffc107", 4: "#ff1744"}
STAGE_ICON = {1: "◯", 2: "✓", 3: "⚠", 4: "✗"}

# Short labels for the Compare-mode consensus column (set in session_state by
# the multi-strategy view when it hands a universe over).
_MANDATE_SHORT = {
    "fast_momentum": "fast", "swing_momentum": "swing",
    "growth_hold": "growth", "value_hold": "value",
}

# ---------------------------------------------------------------------------
# Chapter 4 — overhead resistance grade visual mapping
# ---------------------------------------------------------------------------
# Grade A+ (virgin territory) gets the brightest green; C gets red.
# n/a (Stage 1/4 or compute failed) gets muted gray.
RESIST_COLOR = {
    "A+": "#3fb950",
    "A":  "#56d364",
    "B":  "#ffc107",
    "C":  "#ff7b1a",
    "n/a": "#6c757d",
}
# Sort rank so confirmed_buys panel surfaces A+ first.
# Lower number = earlier in sort.
RESIST_RANK = {"A+": 0, "A": 1, "B": 2, "C": 3, "n/a": 4}


def _resist_chip_html(r) -> str:
    """Inline resistance grade pill for the gate-badge column.

    Returns an HTML snippet that renders the grade with a tooltip showing
    the underlying detail (nearest resistance price + percent above price,
    or 'virgin territory' for A+).
    """
    grade = getattr(r, "resistance_grade", "n/a")
    color = RESIST_COLOR.get(grade, "#6c757d")
    if grade == "n/a":
        tip = "Resistance not computed for this stage"
    elif getattr(r, "virgin_territory", False):
        tip = (
            f"Virgin territory — price at/above 10y high "
            f"${(r.ten_year_high or 0):.2f}"
        )
    elif r.nearest_resistance:
        tip = (
            f"Nearest resistance ${r.nearest_resistance:.2f} "
            f"({r.nearest_resistance_pct:+.1%} above current)"
        )
    else:
        tip = "No measurable overhead resistance"
    return (
        f"<span style='color:{color};font-weight:600' title='{tip}'>"
        f"R: {grade}</span>"
    )


# ---------------------------------------------------------------------------
# Cross-tab handoff into Trader Mode
# ---------------------------------------------------------------------------

def clear_scan_cache() -> None:
    """Drop every cached ScanReport from session_state.

    The scan cache is keyed by ticker membership, so a changed universe
    already misses the old key. This is a belt-and-suspenders purge for an
    explicit handoff so no stale report can linger in memory.
    """
    for k in [k for k in list(st.session_state.keys()) if str(k).startswith("scan_")]:
        del st.session_state[k]


def route_to_trader(watchlist_name: str) -> None:
    """Point Trader Mode at ``watchlist_name`` and invalidate stale scans.

    Call this from another tab (e.g. the multi-strategy comparison) AFTER
    ``save_watchlist()`` has written the universe to disk. It sets the
    selectbox's own key so the switch survives the next rerun, mirrors
    ``trader_watchlist`` for downstream readers, and purges cached reports.
    Must run before the sidebar widget is instantiated, so queue an
    ``st.rerun()`` after calling it.
    """
    st.session_state.watchlist_selector = watchlist_name
    st.session_state.trader_watchlist = watchlist_name
    clear_scan_cache()


# ---------------------------------------------------------------------------
# Watchlist management sidebar
# ---------------------------------------------------------------------------

def render_watchlist_sidebar():
    """Scan-list picker + ticker add/remove in the sidebar. Returns selected tickers."""
    st.sidebar.markdown("### 📈 Trader Mode")
    st.sidebar.caption("Weinstein Stage Analysis · daily scan")

    watchlists = list_watchlists()

    # Scan list selector.
    # The selectbox's own key ("watchlist_selector") is the SINGLE source of
    # truth for the current selection. Do not also pass index= and then
    # reassign trader_watchlist afterwards: once the widget is instantiated
    # its retained value wins on the next rerun, so a programmatic switch
    # (e.g. a handoff that points Trader at a freshly built universe) would be
    # silently reverted. Other tabs route here via route_to_trader(), which
    # sets this key BEFORE the widget is instantiated on the next rerun.
    # Scan list is limited to the portfolio books built in the Portfolio tab.
    portfolio_lists = [w for w in watchlists if w.startswith("Portfolio Holdings")]
    options = portfolio_lists or watchlists or [DEFAULT_NAME]
    if not portfolio_lists:
        st.sidebar.info(
            "Build portfolios in the Portfolio tab first — the scan list is your "
            "portfolio books (combined and per mandate)."
        )

    if "watchlist_selector" not in st.session_state:
        st.session_state.watchlist_selector = (
            st.session_state.get("trader_watchlist", options[0])
        )
    if st.session_state.watchlist_selector not in options:
        st.session_state.watchlist_selector = options[0]

    selected = st.sidebar.selectbox(
        "📋 Scan list (your portfolio books)",
        options=options,
        key="watchlist_selector",
        help=(
            "Each option is a portfolio you built: the combined book "
            "(Portfolio Holdings All) or a single mandate. Weinstein stages every "
            "ticker in it when you click Scan Now."
        ),
    )
    # Mirror to trader_watchlist for downstream readers (cache key, captions).
    st.session_state.trader_watchlist = selected

    tickers = load_watchlist(selected)

    # Manual ad hoc check: type any tickers to stage them alongside the book,
    # without saving them. This is the "is COIN / HOOD / META / ASTS in Stage 2?"
    # check, so a name the portfolio missed can still be graded against Weinstein.
    adhoc_raw = st.sidebar.text_input(
        "🔎 Also check tickers (ad hoc)",
        key="adhoc_tickers",
        placeholder="COIN, HOOD, META, JPM, ASTS",
        help="Comma or space separated. Scanned for their stage this run only; "
             "not saved to the list.",
    )
    adhoc = [t.strip().upper() for t in adhoc_raw.replace(",", " ").split() if t.strip()]
    if adhoc:
        tickers = list(dict.fromkeys(list(tickers) + adhoc))

    st.sidebar.caption(
        f"📊 **{len(tickers)} tickers** in this scan"
        + (f" (+{len(adhoc)} ad hoc)" if adhoc else "")
        + ". This only changes what gets scanned; nothing is bought or sold."
    )

    # Trading-bias UI removed to reduce noise — always show the full Weinstein
    # picture (all four stages). Downstream panels read this default.
    st.session_state.setdefault("trader_bias", "All directions")

    # Position sizing (Step 3c). Three inputs drive the per-card sizer
    # in the confirmed buys list. Weinstein Ch. 7: never risk more than
    # 1 to 2% of capital on one trade. The position cap stops one bet
    # from quietly becoming a concentration risk.
    with st.sidebar.expander("💰 Position sizing", expanded=False):
        st.caption(
            "Used on confirmed buy cards. Sizing reads each trade's "
            "entry and stop, then picks the smaller of three caps: "
            "dollar risk, position cap, capital available."
        )
        st.number_input(
            "Portfolio capital ($)",
            min_value=1000.0, value=100000.0, step=1000.0,
            key="trader_capital",
            help="Total trading capital you're sizing against.",
        )
        st.number_input(
            "Max risk per trade (%)",
            min_value=0.10, max_value=5.0, value=1.0, step=0.10,
            key="trader_risk_pct",
            help="Weinstein's rule of thumb is 1% to 2% of capital "
                 "per trade. Risk = shares × (entry - stop).",
        )
        st.number_input(
            "Max position size (%)",
            min_value=1.0, max_value=25.0, value=5.0, step=0.50,
            key="trader_max_position_pct",
            help="Hard cap on dollar exposure to any single name.",
        )

    # Discover Momentum Leaders, New scan list, and Edit list were removed to
    # keep the sidebar focused: the scan list is the portfolio books, and the
    # ad hoc box above covers checking any extra ticker's stage.

    # ------------------------------------------------------------------
    # Demo QR code — lets an audience scan and open the app on their phone.
    # Prefers the public Cloudflare tunnel URL (any network); falls back to
    # the Mac's LAN IP (same WiFi) so it still works on plain localhost.
    # ------------------------------------------------------------------
    with st.sidebar.expander("📲 Demo QR code", expanded=False):
        from ui.components.demo_qr import get_demo_url, make_qr_png_bytes

        url, scope = get_demo_url(port=8501)
        if url is None:
            st.caption(
                "No public or LAN address detected. Run `Launch.command` to "
                "start the Cloudflare tunnel, or connect this Mac to WiFi."
            )
        else:
            try:
                png = make_qr_png_bytes(url, box_size=8)
                st.image(png, use_container_width=True)
            except Exception as exc:
                st.caption(f"QR generation unavailable: {exc}")
            if scope == "public":
                st.caption(f"🌍 Public link — any phone, any network.\n\n{url}")
            else:
                st.caption(
                    f"📶 Same WiFi only.\n\n{url}\n\n"
                    "Run `Launch.command` for a public link that works anywhere."
                )

    return tickers


# ---------------------------------------------------------------------------
# Main scan panel
# ---------------------------------------------------------------------------

def render_scan_panel(tickers: list[str]):
    """Top scan button + leaderboards + full table."""
    col1, col2, col3 = st.columns([2, 1, 2])
    with col1:
        st.markdown(f"### Scanning **{len(tickers)}** tickers")
    with col2:
        scan_clicked = st.button("🔍 Scan Now", type="primary", use_container_width=True)
    with col3:
        if "last_scan_time" in st.session_state:
            st.caption(f"Last scan: {st.session_state.last_scan_time}")

    # Cache key is content-addressable: it hashes the actual ticker
    # membership, not the list name + length. The old name+len key collided
    # whenever a rebuilt universe kept the same count, so the Trader tab kept
    # serving a stale ScanReport (the "still 78" bug). Hashing membership means
    # any add/remove/rebuild busts the cache, and re-selecting the same list
    # reuses it.
    _members = ",".join(sorted(set(tickers)))
    _sig = hashlib.md5(_members.encode()).hexdigest()[:10]
    cache_key = f"scan_{_sig}"

    if scan_clicked or cache_key not in st.session_state:
        if not tickers:
            st.warning("Watchlist is empty — add tickers via the sidebar.")
            return
        with st.spinner(f"Running Weinstein Stage Analysis on {len(tickers)} tickers..."):
            progress = st.progress(0.0, text="Starting...")

            def on_progress(done, total, t):
                progress.progress(done / total, text=f"Analyzed {done}/{total}: {t}")

            report = scan_universe(tickers, progress_callback=on_progress)
            progress.empty()
        st.session_state[cache_key] = report
        from datetime import datetime
        st.session_state.last_scan_time = datetime.now().strftime("%H:%M:%S")
        st.rerun()

    report: ScanReport = st.session_state[cache_key]

    # Notify user about tickers we couldn't analyze (data-quality filter)
    _render_excluded_tickers_panel(report)

    # Leaderboards
    _render_leaderboards(report)

    # Full sortable table
    st.markdown("---")
    st.markdown("### 📋 Full Universe Scan")
    _render_full_table(report)

    # Detail view
    st.markdown("---")
    _render_detail_view(report)


# ---------------------------------------------------------------------------
# Excluded tickers notification (#4)
# ---------------------------------------------------------------------------

def _render_excluded_tickers_panel(report: ScanReport):
    """Surface tickers that did not reach the leaderboards, split by kind.

    - crashed: analysis raised an exception. A code fault, surfaced
      loudly because it means a bug, not a bad ticker.
    - excluded: dropped by the data-quality filter (delisted, illiquid,
      sparse data). Benign — offered with a 1-click watchlist remove.
    """
    crashed = getattr(report, "crashed", [])
    if crashed:
        st.error(
            f"🐞 {len(crashed)} ticker(s) hit a code error during analysis: "
            + ", ".join(r.ticker for r in crashed)
            + ". This is a bug, not a data problem — the full traceback is "
            "in the server log. These tickers are missing from the scan."
        )
        with st.expander("View error detail", expanded=False):
            for r in crashed:
                st.markdown(f"**{r.ticker}** — `{r.error or 'unknown error'}`")

    # getattr keeps this resilient if an older ScanReport (without the
    # computed `excluded` property) is still cached in the running process.
    excluded = getattr(report, "excluded", [])
    if not excluded:
        return

    with st.expander(
        f"⚠️ {len(excluded)} ticker(s) excluded from this scan due to "
        f"data-quality issues (click to view)",
        expanded=False,
    ):
        st.caption(
            "These tickers were dropped because their price/volume data is "
            "unreliable (delisted, illiquid, or sparse). They can't be "
            "evaluated against Weinstein's gates. Consider removing them "
            "from your watchlist — the scan will be cleaner and faster."
        )

        from data.watchlists import remove_ticker as _remove, load_watchlist as _load_wl
        wl_name = st.session_state.get("trader_watchlist", "Top Picks")

        # Render a row per excluded ticker with reason + remove button
        for r in excluded:
            cols = st.columns([2, 6, 2])
            cols[0].markdown(f"**{r.ticker}**")
            cols[1].caption(r.error or "unknown reason")
            with cols[2]:
                btn_key = f"rm_excluded_{r.ticker}"
                if r.ticker in _load_wl(wl_name):
                    if st.button("🗑 Remove", key=btn_key,
                                 use_container_width=True):
                        _remove(wl_name, r.ticker)
                        st.success(f"Removed {r.ticker} from '{wl_name}'.")
                        # Clear scan cache so next scan reflects the change
                        for k in list(st.session_state.keys()):
                            if k.startswith("scan_"):
                                del st.session_state[k]
                        st.rerun()
                else:
                    st.caption("not in list")

        # Bulk-remove button — one click clears all excluded tickers
        if any(r.ticker in _load_wl(wl_name) for r in excluded):
            st.markdown("")
            if st.button(
                f"🗑 Remove all {len(excluded)} excluded tickers from '{wl_name}'",
                key="rm_all_excluded",
                type="secondary",
            ):
                for r in excluded:
                    _remove(wl_name, r.ticker)
                # Clear scan cache so next scan reflects the change
                for k in list(st.session_state.keys()):
                    if k.startswith("scan_"):
                        del st.session_state[k]
                st.success(f"Removed {len(excluded)} tickers from '{wl_name}'.")
                st.rerun()


# ---------------------------------------------------------------------------
# Leaderboards
# ---------------------------------------------------------------------------

def _render_leaderboards(report: ScanReport):
    buckets = report.stage_buckets
    # Top row: stage distribution badges
    c1, c2, c3, c4 = st.columns(4)
    for col, stage in zip((c1, c2, c3, c4), (1, 2, 3, 4)):
        name = {1: "Basing", 2: "Advancing", 3: "Topping", 4: "Declining"}[stage]
        count = len(buckets[stage])
        color = STAGE_COLOR[stage]
        col.markdown(
            f"<div style='background:{color};color:white;padding:12px;"
            f"border-radius:8px;text-align:center'>"
            f"<div style='font-size:11px;text-transform:uppercase;opacity:0.9'>"
            f"{STAGE_ICON[stage]} Stage {stage}</div>"
            f"<div style='font-size:28px;font-weight:700;margin-top:2px'>{count}</div>"
            f"<div style='font-size:13px;opacity:0.9'>{name}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    st.markdown("")

    # Stage 2 since-breakout scorecard. Judges live Stage 2 calls by
    # return since each name reclaimed its 30-week MA (the breakout bar),
    # measured over weeks. This is the system validating itself. Today's
    # candle is noise and is deliberately excluded from this number.
    s2_scored = [r for r in buckets.get(2, [])
                 if getattr(r, "stage2_entry_date", None)]
    if s2_scored:
        rets = [r.return_since_breakout for r in s2_scored]
        avg_ret = sum(rets) / len(rets)
        win_rate = sum(1 for x in rets if x > 0) / len(rets)
        avg_wks = sum(getattr(r, "weeks_held", 0) for r in s2_scored) / len(s2_scored)
        avg_color = "#1a7f37" if avg_ret >= 0 else "#cf222e"
        st.markdown(
            f"<div style='background:#f6f8fa;border:1px solid #d0d7de;"
            f"border-radius:8px;padding:10px 14px;font-size:13px'>"
            f"<b>&#128202; Stage 2 scorecard.</b> "
            f"<span style='color:#57606a'>Return since each name reclaimed "
            f"its 30-week MA (the breakout anchor), measured over weeks. "
            f"Today's move is noise and is excluded.</span><br>"
            f"<span style='color:#57606a'>Live Stage 2 calls scored:</span> "
            f"<b>{len(s2_scored)}</b> &nbsp;&nbsp;&nbsp; "
            f"<span style='color:#57606a'>Avg return since breakout:</span> "
            f"<b style='color:{avg_color}'>{avg_ret:+.1%}</b> &nbsp;&nbsp;&nbsp; "
            f"<span style='color:#57606a'>Positive:</span> "
            f"<b>{win_rate:.0%}</b> &nbsp;&nbsp;&nbsp; "
            f"<span style='color:#57606a'>Avg hold:</span> "
            f"<b>{avg_wks:.0f} wks</b>"
            f"</div>",
            unsafe_allow_html=True,
        )
        st.markdown("")

    # Four leaderboards reflecting Weinstein's discipline:
    #   1. Confirmed BUYs    — Stage 2, both gates passing (actionable now)
    #   2. Watching (forming)— Stage 2 near a breakout, one+ gate failing
    #   3. Extended (avoid)  — Stage 2 ran too far on weak volume — rolling to S3
    #   4. Stage 4 Breakdowns — short/avoid
    # Reorder Confirmed BUYs by Chapter 4 Resistance Grade first, then by
    # bull probability. Weinstein Ch. 4 explicitly tells us: among candidates
    # that have already cleared volume + RS gates, pick the one with the
    # cleanest path higher (A+ virgin > A > B > C). bull_prob is the
    # secondary tiebreaker within a grade tier.
    confirmed_ranked = sorted(
        getattr(report, "confirmed_buys", []),
        key=lambda x: (
            RESIST_RANK.get(getattr(x, "resistance_grade", "n/a"), 4),
            -x.bull_probability,
        ),
    )
    # Entry risk overlay split: a regime-blocked pick is technically a
    # Stage 2 confirmed buy, but the market tape says do not take new
    # longs, so it leaves the actionable list for its own panel below.
    regime_blocked = [r for r in confirmed_ranked
                      if getattr(r, "entry_gate_status", "pass") == "blocked"]
    actionable = [r for r in confirmed_ranked
                  if getattr(r, "entry_gate_status", "pass") != "blocked"]
    confirmed = actionable[:10]
    watching = getattr(report, "watching_stage2", [])[:10]
    # Stage-faithful split: Late Stage 2 (technically still Stage 2,
    # MA still rising) and Stage 3 Topping (MA flattened/rolled over).
    # Each gets its own panel — the user sees the precise stage, not a
    # prescriptive "don't buy" label.
    late_stage2 = getattr(report, "late_stage_2", [])[:10]
    stage3 = getattr(report, "stage3_topping", [])[:10]
    breakdowns = getattr(report, "stage4_breakdowns", [])[:10]

    # Trading bias filter (#2): long-only hides bear panels, short-only hides bull
    bias = st.session_state.get("trader_bias", "All directions")
    show_bull = bias != "Short only"
    show_bear = bias != "Long only"

    if show_bull:
        st.markdown(
            f"#### 🟢 Confirmed BUYs "
            f"<span style='color:#8b949e;font-weight:400;font-size:14px'>"
            f"— Stage 2 with volume + RS gates passing ({len(actionable)})</span>",
            unsafe_allow_html=True,
        )
        st.caption(
            "**Action: BUY now.** These are textbook Weinstein Stage 2 setups: "
            "price above a rising 30-week MA, recent volume expansion (≥1.5× "
            "the 50-day baseline), and Mansfield RS above zero (outperforming "
            "SPY). Trade the GTC stop-limit shown in the detail view."
        )
        if confirmed:
            _render_leader_rows(confirmed, direction="bull")
        else:
            st.info(
                "No confirmed Stage 2 BUYs in this watchlist right now. Per Weinstein, "
                "wait for volume expansion and a positive Mansfield RS line before "
                "initiating any long position."
            )

        st.markdown("")

        # Regime-gated buys: cleared every stock-level Weinstein gate but
        # held back because the broad market tape is topping or declining.
        if regime_blocked:
            st.markdown(
                f"#### 🚫 Gated by market regime "
                f"<span style='color:#8b949e;font-weight:400;font-size:14px'>"
                f"— Stage 2 buys held back by a weak market tape "
                f"({len(regime_blocked)})</span>",
                unsafe_allow_html=True,
            )
            st.caption(
                "These cleared every Weinstein stock-level gate, but the "
                "broad market is topping or declining. Per Weinstein Ch. 8, "
                "about 75% of stocks follow the tape, so new longs are held "
                "back until the market regime improves."
            )
            _render_leader_rows(regime_blocked[:10], direction="bull")
            st.markdown("")

    if show_bull:
        left, right = st.columns(2)
        with left:
            st.markdown(
                f"#### 🟡 Watching "
                f"<span style='color:#8b949e;font-weight:400;font-size:14px'>"
                f"— Stage 2 forming, gates not yet confirmed "
                f"({len(report.watching_stage2)})</span>",
                unsafe_allow_html=True,
            )
            st.caption(
                "**Action: MONITOR.** These are real Stage 2 setups (price "
                "above a rising 30-week MA) but the **volume has not expanded** "
                "(<1.5×) and/or **Mansfield RS is still negative**. Could "
                "become BUYs — track them daily for breakout confirmation. "
                "Tickers with sparse/unreliable data are excluded automatically."
            )
            if watching:
                _render_leader_rows(watching, direction="watch")
            else:
                st.info("No Stage 2 candidates pending confirmation.")

        with right:
            st.markdown(
                f"#### 🟠 Late Stage 2 "
                f"<span style='color:#8b949e;font-weight:400;font-size:14px'>"
                f"— Stage 2 extended, optimal entry has passed "
                f"({len(report.late_stage_2)})</span>",
                unsafe_allow_html=True,
            )
            st.caption(
                "These are Stage 2 stocks that have run >25–40% above the "
                "30-week MA. The MA is still rising — they're **technically "
                "still in Stage 2** — but the high-probability entry has "
                "passed. If the stock pulls back, it can re-enter the BUY or "
                "Watching panel on the next scan."
            )
            if late_stage2:
                _render_leader_rows(late_stage2, direction="late2")
            else:
                st.info("No late Stage 2 stocks — only fresh setups.")

        st.markdown("")

        # Stage 3 — its own panel, descriptive (not prescriptive)
        st.markdown(
            f"#### ⚠️ Stage 3 — Topping "
            f"<span style='color:#8b949e;font-weight:400;font-size:14px'>"
            f"— 30-week MA flattening or rolling over "
            f"({len(report.stage3_topping)})</span>",
            unsafe_allow_html=True,
        )
        st.caption(
            "Stage 3 stocks: the 30-week MA has flattened or started to roll "
            "over after a Stage 2 advance. Volume is often erratic and the "
            "stock churns sideways at the highs. If a future scan shows the "
            "MA turning back up, the stock re-enters Stage 2 and will appear "
            "in BUY or Watching automatically."
        )
        if stage3:
            _render_leader_rows(stage3, direction="stage3")
        else:
            st.info("No Stage 3 stocks in this scan list.")

        st.markdown("")

    # Stage 1 Transition Watch — Stage 4 stocks that have completed a
    # base and started a fresh rally. Shown to BOTH long-bias and
    # short-bias users because long bias users want to watch for
    # emerging Stage 2 candidates, and short bias users want to know
    # which Stage 4 shorts are starting to repair (cover risk).
    _transitions_all = getattr(report, "stage1_transitions", [])
    transitions = _transitions_all[:10]
    st.markdown(
        f"#### 🔵 Stage 1 Transition Watch "
        f"<span style='color:#8b949e;font-weight:400;font-size:14px'>"
        f"— Stage 4 stocks repairing, possible early Stage 2 setups "
        f"({len(_transitions_all)})</span>",
        unsafe_allow_html=True,
    )
    st.caption(
        "**Action: WATCH only, do not buy yet.** These are technically "
        "still Stage 4 by the 30-week MA rule, but four early-recovery "
        "signals are firing: rally off the 90-day low, price above the "
        "50-day MA, expanding volume, and positive 4-week momentum. "
        "The 30-week MA is a lagging indicator; by the time it confirms "
        "Stage 2, much of the easy gain is in. This panel surfaces the "
        "candidates BEFORE the official Stage 2 promotion. Watch for "
        "confirmation; do not initiate a long until the stock graduates "
        "to a Confirmed BUY in the panels above."
    )
    if transitions:
        _render_leader_rows(transitions, direction="transition")
    else:
        st.info(
            "No Stage 1 Transition candidates in this scan list right now. "
            "These appear when a Stage 4 stock starts to mend on real "
            "volume."
        )

    st.markdown("")

    if show_bear:
        st.markdown(
            f"#### 🔴 Stage 4 Breakdowns "
            f"<span style='color:#8b949e;font-weight:400;font-size:14px'>"
            f"— SELL/SHORT zone ({len(report.stage4_breakdowns)})</span>",
            unsafe_allow_html=True,
        )
        st.caption(
            "**Action: SELL existing holdings · consider SHORT.** Price below "
            "a declining 30-week MA. Weinstein's most emphatic rule: never "
            "buy a Stage 4 stock 'no matter how cheap it appears.' For traders "
            "with shorting experience, mirror the Stage 2 setup: enter on a "
            "sell-stop just below the recent pivot low, cover at the 30-week MA."
        )
        if breakdowns:
            _render_leader_rows(breakdowns, direction="bear")
        else:
            st.info("No Stage 4 candidates in this watchlist right now.")


def _render_leader_rows(results: list[StageResult], direction: str = "bull"):
    for r in results:
        color = STAGE_COLOR[r.stage]
        # Click-to-detail — store selected ticker in session state
        ticker_button = f"**{r.ticker}**"
        cols = st.columns([2, 2, 3, 2, 2])
        with cols[0]:
            if st.button(ticker_button, key=f"lb_{direction}_{r.ticker}",
                          use_container_width=True):
                st.session_state.trader_detail_ticker = r.ticker
                st.rerun()
        cols[1].markdown(
            f"<div style='padding-top:6px'>Bull "
            f"<span style='color:{color};font-weight:600'>"
            f"{r.bull_probability:.0%}</span></div>",
            unsafe_allow_html=True,
        )
        cols[2].markdown(
            f"<div style='padding-top:6px;font-size:13px'>"
            f"vs MA {r.pct_above_ma:+.1%} · slope {r.ma_slope_pct:+.1%}"
            f"</div>",
            unsafe_allow_html=True,
        )

        # Weinstein gate badges — vol + RS + Chapter 4 resistance grade
        # at a glance. The resist chip surfaces the path-clarity verdict
        # right next to the volume and RS gates so the user can rank
        # Confirmed BUYs by overhead supply without opening detail view.
        vol_ok = getattr(r, "volume_confirmed", False)
        rs_ok = getattr(r, "rs_confirmed", False)
        vol_color = "#3fb950" if vol_ok else "#f85149"
        rs_color = "#3fb950" if rs_ok else "#f85149"
        vol_icon = "✓" if vol_ok else "✗"
        rs_icon = "✓" if rs_ok else "✗"
        resist_chip = _resist_chip_html(r)
        # Entry quality grade (Step 3a) — an A to D blend of extension,
        # volume, breakout freshness, RS, and (Step 3b) news sentiment.
        # Only set for Stage 2 names.
        q_grade = getattr(r, "entry_quality_grade", "")
        q_score = getattr(r, "entry_quality_score", 0.0)
        quality_chip = ""
        if q_grade:
            q_color = {"A": "#3fb950", "B": "#3fb950",
                       "C": "#d29922", "D": "#f85149"}.get(q_grade, "#8b949e")
            quality_chip = (
                f"<span style='color:#8b949e;margin:0 4px'>·</span>"
                f"<span style='color:{q_color};font-weight:600' "
                f"title='Entry quality {q_score:.0f}/100 from extension, "
                f"volume, breakout freshness, RS, and news sentiment'>"
                f"Q: {q_grade}</span>"
            )

        # Earnings + news chips (Step 3b). Populated only on confirmed
        # buys; both stay hidden on watchlist names so the row keeps a
        # consistent density.
        earn_chip = ""
        d2e = getattr(r, "days_to_earnings", None)
        if d2e is not None:
            if 0 <= d2e <= 5:
                earn_color, earn_emoji = "#f85149", "&#128276; "
            elif d2e <= 14:
                earn_color, earn_emoji = "#d29922", ""
            else:
                earn_color, earn_emoji = "#8b949e", ""
            earn_date = getattr(r, "next_earnings_date", None) or "?"
            earn_chip = (
                f"<span style='color:#8b949e;margin:0 4px'>·</span>"
                f"<span style='color:{earn_color};font-weight:600' "
                f"title='Next earnings {earn_date}. Inside 5 trading day "
                f"blackout means avoid new entries.'>"
                f"{earn_emoji}Earnings {d2e}d</span>"
            )

        sent_chip = ""
        s_compound = getattr(r, "sentiment_score", None)
        if s_compound is not None:
            if s_compound <= -0.30:
                s_color = "#f85149"
            elif s_compound <= -0.10:
                s_color = "#d29922"
            elif s_compound >= 0.10:
                s_color = "#3fb950"
            else:
                s_color = "#8b949e"
            n_head = getattr(r, "sentiment_headline_count", 0)
            sent_chip = (
                f"<span style='color:#8b949e;margin:0 4px'>·</span>"
                f"<span style='color:{s_color};font-weight:600' "
                f"title='VADER compound on {n_head} latest headlines. "
                f"Below -0.30 triggers a caution.'>"
                f"News {s_compound:+.2f}</span>"
            )

        cols[3].markdown(
            f"<div style='padding-top:6px;font-size:12px;text-align:center;"
            f"font-family:ui-monospace,Menlo,monospace'>"
            f"<span style='color:{vol_color};font-weight:600' "
            f"title='Volume gate ({r.volume_surge:.2f}x)'>{vol_icon} VOL</span>"
            f"<span style='color:#8b949e;margin:0 4px'>·</span>"
            f"<span style='color:{rs_color};font-weight:600' "
            f"title='Mansfield RS gate ({r.mansfield_rs:+.1f})'>{rs_icon} RS</span>"
            f"<span style='color:#8b949e;margin:0 4px'>·</span>"
            f"{resist_chip}"
            f"{quality_chip}"
            f"{earn_chip}"
            f"{sent_chip}"
            f"</div>",
            unsafe_allow_html=True,
        )

        # Invalidation chip — shows ONLY when the trade's Weinstein line
        # has actually broken (red) or has been crossed intraweek but is
        # not yet confirmed by a weekly close (amber). It sits under the
        # action badge so an INVALIDATED state can never hide behind a
        # BUY label. "INVALIDATED" is now an earned state, not a label
        # that prints on every card.
        chip_html = ""
        if getattr(r, "is_invalidated", False):
            chip_html = (
                "<div style='margin-top:3px'><span style='background:#f85149;"
                "color:white;padding:1px 6px;border-radius:4px;font-size:10px;"
                "font-weight:700'>&#9888; INVALIDATED</span></div>"
            )
        elif "AT RISK" in getattr(r, "invalidation_note", ""):
            chip_html = (
                "<div style='margin-top:3px'><span style='background:#d29922;"
                "color:white;padding:1px 6px;border-radius:4px;font-size:10px;"
                "font-weight:700'>&#9888; AT RISK</span></div>"
            )
        cols[4].markdown(
            f"<div style='padding-top:6px;font-size:13px;text-align:right'>"
            f"<span style='background:{color};color:white;padding:2px 8px;"
            f"border-radius:4px;font-weight:600'>{r.action}</span>"
            f"{chip_html}</div>",
            unsafe_allow_html=True,
        )

        # Today vs Trend strip — sits right under the row. Three facts:
        #   1. Today's one-day move. This is daily noise, NOT a stage
        #      signal; the tooltip says so. A red day is not a regime
        #      change and must not read like one.
        #   2. The trade's STATE vs its 30-week MA: "Valid while above/
        #      below $X" with a colored cushion, or AT RISK / INVALIDATED.
        #      Phrased as the state the trade is in, so the user never has
        #      to mentally invert a conditional ("Invalidates below ...").
        #   3. Since-breakout return (Stage 2) — the honest scorecard,
        #      measured in weeks from the 30-week MA reclaim.
        today_pct = getattr(r, "today_change_pct", 0.0)
        today_color = "#3fb950" if today_pct >= 0 else "#f85149"
        today_str = (
            f"<span style='color:{today_color};font-weight:600' "
            f"title='One-day price move. Daily noise, not a Weinstein stage "
            f"signal. The stage is a weekly framework off the 30-week MA.'>"
            f"Today {today_pct:+.2%}</span>"
        )

        # State of the trade vs its 30-week MA line.
        invalidator_str = ""
        if r.stage in (2, 4) and r.ma_30w:
            if r.stage == 2:
                cushion = (r.last_close - r.ma_30w) / r.last_close if r.last_close else 0
                side = "above"
            else:
                cushion = (r.ma_30w - r.last_close) / r.last_close if r.last_close else 0
                side = "below"
            if getattr(r, "is_invalidated", False):
                invalidator_str = (
                    f"<span style='color:#f85149;font-weight:600'>"
                    f" &nbsp;·&nbsp; INVALIDATED &nbsp;·&nbsp; weekly close "
                    f"broke ${r.ma_30w:,.2f}</span>"
                )
            elif "AT RISK" in getattr(r, "invalidation_note", ""):
                invalidator_str = (
                    f"<span style='color:#d29922;font-weight:600'>"
                    f" &nbsp;·&nbsp; AT RISK &nbsp;·&nbsp; price crossed "
                    f"${r.ma_30w:,.2f} intraweek</span>"
                )
            else:
                # Cushion color: green healthy, amber thin, red breached.
                if cushion >= 0.05:
                    cush_color = "#3fb950"
                elif cushion >= 0.0:
                    cush_color = "#d29922"
                else:
                    cush_color = "#f85149"
                invalidator_str = (
                    f"<span style='color:#8b949e'> &nbsp;·&nbsp; Valid while "
                    f"{side} <b>${r.ma_30w:,.2f}</b> &nbsp;·&nbsp; </span>"
                    f"<span style='color:{cush_color};font-weight:600'>"
                    f"{cushion:+.1%} cushion</span>"
                )

        # Since-breakout return — the Stage 2 honest scorecard.
        breakout_str = ""
        if r.stage == 2 and getattr(r, "stage2_entry_date", None):
            ret = getattr(r, "return_since_breakout", 0.0)
            wks = getattr(r, "weeks_held", 0)
            ret_color = "#3fb950" if ret >= 0 else "#f85149"
            breakout_str = (
                f"<span style='color:#8b949e'> &nbsp;·&nbsp; Since breakout "
                f"</span>"
                f"<span style='color:{ret_color};font-weight:600'>"
                f"{ret:+.1%}</span>"
                f"<span style='color:#8b949e'> &nbsp;·&nbsp; {wks} "
                f"wk{'' if wks == 1 else 's'}</span>"
            )

        st.markdown(
            f"<div style='font-size:11px;margin-top:2px;margin-bottom:6px;"
            f"padding-left:6px;font-family:ui-monospace,Menlo,monospace'>"
            f"{today_str}{invalidator_str}{breakout_str}"
            f"</div>",
            unsafe_allow_html=True,
        )

        # Position sizing strip (Step 3c). Reads the three sidebar
        # inputs and surfaces shares + dollar risk + the binding cap so
        # the user sees exactly how big to take this trade. Only shown
        # for bull (long) confirmed buys with a TradeSetup attached.
        if direction == "bull":
            setup = getattr(r, "trade_setup", None)
            if setup is not None and getattr(setup, "applicable", False):
                try:
                    from ml.position_sizer import size_from_result
                    cap = float(st.session_state.get("trader_capital", 100000.0))
                    rpct = float(
                        st.session_state.get("trader_risk_pct", 1.0)) / 100.0
                    mpct = float(
                        st.session_state.get(
                            "trader_max_position_pct", 5.0)) / 100.0
                    sized = size_from_result(r, cap, rpct, mpct)
                except Exception:
                    sized = None
                if sized is not None and sized.shares > 0:
                    bound_color = {
                        "risk":              "#3fb950",
                        "position cap":      "#d29922",
                        "capital available": "#f85149",
                    }.get(sized.bound_by, "#8b949e")
                    sizing_html = (
                        f"<div style='font-size:11px;margin-top:0px;"
                        f"margin-bottom:6px;padding-left:6px;"
                        f"font-family:ui-monospace,Menlo,monospace;"
                        f"color:#8b949e'>"
                        f"Size <span style='color:#c9d1d9;font-weight:600'>"
                        f"{sized.shares:,} sh</span>"
                        f"<span> &nbsp;·&nbsp; exposure </span>"
                        f"<span style='color:#c9d1d9;font-weight:600'>"
                        f"${sized.dollar_exposure:,.0f}</span>"
                        f"<span> &nbsp;·&nbsp; at risk </span>"
                        f"<span style='color:#c9d1d9;font-weight:600'>"
                        f"${sized.dollar_risk:,.0f}</span>"
                        f"<span> &nbsp;·&nbsp; entry </span>"
                        f"<span style='color:#c9d1d9'>"
                        f"${sized.entry_price:.2f}</span>"
                        f"<span> stop </span>"
                        f"<span style='color:#c9d1d9'>"
                        f"${sized.stop_loss:.2f}</span>"
                        f"<span> &nbsp;·&nbsp; bound by </span>"
                        f"<span style='color:{bound_color};font-weight:600' "
                        f"title='Which limit constrained the size: dollar "
                        f"risk, position cap, or capital available.'>"
                        f"{sized.bound_by}</span>"
                        f"</div>"
                    )
                    st.markdown(sizing_html, unsafe_allow_html=True)

        # Confirmed BUYs get a compact "Open Research dossier" button as
        # their second row. The Research tab (Phase 2) will pick up the
        # ticker from st.session_state.research_ticker on render. For
        # Phase 1 we set the ticker and flash a hint — the dossier UI
        # itself ships in Phase 2.
        if direction == "bull":
            sub_cols = st.columns([5, 2])
            with sub_cols[1]:
                if st.button(
                    "📖 Open Research dossier",
                    key=f"dossier_{r.ticker}",
                    use_container_width=True,
                    help=(
                        "Switch to the Research tab and run the full "
                        "Weinstein dossier on this ticker (Chapter 3 "
                        "calendar context, 10-year resistance chart, "
                        "Chartist Strategist verdict, Setup Confidence "
                        "Score)."
                    ),
                ):
                    # Phase 2 wiring: switch app mode AND set the dossier
                    # ticker + pending flag. The Research tab picks all
                    # three up on the next rerun and lands on the dossier.
                    st.session_state.research_ticker = r.ticker
                    st.session_state.research_open_pending = True
                    st.session_state.research_view = "dossier"
                    st.session_state.app_mode = "research"
                    st.rerun()


# ---------------------------------------------------------------------------
# Full sortable table
# ---------------------------------------------------------------------------

def _render_quick_remove_panel(report: ScanReport):
    """Inline ticker remove control above the Full Universe Scan table.

    Lets the user drop one or more tickers from the current scan list
    without opening the sidebar Edit panel. After removal, the scan
    cache is cleared and the page reruns so the next scan reflects
    the change immediately.

    Excluded (errored) tickers already have their own per-row remove
    button in the data-quality panel up top; this control covers the
    healthy tickers visible in the full table.
    """
    wl_name = st.session_state.get("trader_watchlist", DEFAULT_NAME)
    available = sorted(r.ticker for r in report.results if not r.error)
    if not available:
        return

    cols = st.columns([5, 2])
    with cols[0]:
        to_remove = st.multiselect(
            "Quick remove from this scan list",
            options=available,
            placeholder="Pick one or more tickers to drop from this scan list",
            key=f"quick_remove_{wl_name}",
            label_visibility="collapsed",
            help=(
                "Removes the selected tickers from the current scan list "
                "only. Doesn't sell anything, doesn't affect other scan "
                "lists. The Edit panel in the sidebar does the same thing "
                "one ticker at a time."
            ),
        )
    with cols[1]:
        clicked = st.button(
            f"🗑 Remove {len(to_remove)} from '{wl_name}'"
            if to_remove else "🗑 Remove from scan list",
            key=f"quick_remove_btn_{wl_name}",
            use_container_width=True,
            disabled=not to_remove,
        )
    if clicked and to_remove:
        for t in to_remove:
            _remove_ticker(wl_name, t)
        # Drop any cached scans so the next scan reflects the change.
        for k in list(st.session_state.keys()):
            if k.startswith("scan_"):
                del st.session_state[k]
        st.success(
            f"Removed {len(to_remove)} ticker(s) from '{wl_name}': "
            f"{', '.join(to_remove)}. Click Scan Now to refresh."
        )
        st.rerun()


def _render_full_table(report: ScanReport):
    # Inline remove control — sits between the section header and the
    # table so the user can drop tickers without opening the sidebar.
    _render_quick_remove_panel(report)

    # Compare-mode provenance: how many books surfaced each ticker, and which.
    # Present only when the user arrived here via the Compare handoff.
    prov = st.session_state.get("multi_provenance") or {}

    rows = []
    for r in report.results:
        if r.error:
            continue
        # Weinstein BUY confirmation badge — most important signal
        setup_type = getattr(r, "setup_type", "n/a")
        if r.stage == 2:
            if setup_type == "fresh":
                buy_status = "✓ Confirmed"
            elif setup_type == "extended":
                buy_status = "⚠ Extended"
            else:
                buy_status = "○ Watch"
        elif r.stage == 4:
            buy_status = "✗ Avoid"
        else:
            buy_status = "—"
        # Chapter 4 resistance grade — only populated for Stage 2/3 in the
        # scanner. Shown for every row so the table reads consistently;
        # blank for Stage 1/4 (no path-higher question to answer).
        resist_label = getattr(r, "resistance_grade", "n/a")
        if resist_label == "n/a":
            resist_display = ""  # blank for stages where it doesn't apply
        elif getattr(r, "virgin_territory", False):
            resist_display = f"{resist_label} · virgin"
        elif r.nearest_resistance:
            resist_display = (
                f"{resist_label} · {r.nearest_resistance_pct:+.0%}"
            )
        else:
            resist_display = resist_label
        try:
            from data.universe import company_name as _cname
            _co = _cname(r.ticker) or ""
        except Exception:  # noqa: BLE001
            _co = ""
        row = {
            "Ticker": r.ticker,
            "Company": _co,
            "Stage": f"{r.stage} {STAGE_ICON[r.stage]} {r.stage_name}",
            "Buy Gates": buy_status,
            "Resist": resist_display,
            "Bull Prob": r.bull_probability,
            "Action": r.action,
            "Price": r.last_close,
            "vs 30W MA": r.pct_above_ma,
            "MA Slope (4w)": r.ma_slope_pct,
            "RS vs SPY": r.mansfield_rs,
            "Volume x": r.volume_surge,
            "52W High": r.pct_from_52w_high,
        }
        if prov:
            pinfo = prov.get(r.ticker)
            if pinfo:
                books = ", ".join(_MANDATE_SHORT.get(b, b) for b in pinfo.get("books", []))
                row["Consensus"] = f"{pinfo.get('count', 0)}× {books}"
            else:
                row["Consensus"] = ""
        rows.append(row)

    if not rows:
        st.info("No successful scans to display.")
        return

    df = pd.DataFrame(rows)
    # Sort: Weinstein-confirmed buys first, then by bull prob descending.
    # This way "✓ Confirmed" rows always sit above "○ Watch" within Stage 2.
    df["_sort_buy"] = df["Buy Gates"].map(
        {"✓ Confirmed": 0, "○ Watch": 1, "⚠ Extended": 2, "✗ Avoid": 3, "—": 4}
    ).fillna(5)
    df = df.sort_values(
        ["_sort_buy", "Bull Prob"], ascending=[True, False]
    ).drop(columns=["_sort_buy"]).reset_index(drop=True)

    st.dataframe(
        df,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Bull Prob": st.column_config.ProgressColumn(
                "Bull Prob", format="%.0f%%", min_value=0, max_value=1,
            ),
            "Price": st.column_config.NumberColumn("Price", format="$%.2f"),
            "vs 30W MA": st.column_config.NumberColumn("vs 30W MA", format="%.1f%%"),
            "MA Slope (4w)": st.column_config.NumberColumn("MA Slope (4w)", format="%.1f%%"),
            "RS vs SPY": st.column_config.NumberColumn("RS vs SPY", format="%.1f"),
            "Volume x": st.column_config.NumberColumn("Volume x", format="%.2fx"),
            "52W High": st.column_config.NumberColumn("52W High", format="%.1f%%"),
            "Consensus": st.column_config.TextColumn(
                "Consensus",
                help="How many Compare-mode books surfaced this name, and which. "
                     "More books = stronger cross-strategy agreement.",
            ),
        },
    )


# ---------------------------------------------------------------------------
# Detail view (click a ticker)
# ---------------------------------------------------------------------------

def _render_detail_view(report: ScanReport):
    ticker_options = [r.ticker for r in report.results if not r.error]
    if not ticker_options:
        return

    default_idx = 0
    if "trader_detail_ticker" in st.session_state:
        if st.session_state.trader_detail_ticker in ticker_options:
            default_idx = ticker_options.index(st.session_state.trader_detail_ticker)

    st.markdown("### 🔬 Stage Detail — click a ticker above or select here")
    selected_ticker = st.selectbox(
        "Ticker", options=ticker_options, index=default_idx, key="detail_selector",
    )
    st.session_state.trader_detail_ticker = selected_ticker

    # Find the result
    result = next((r for r in report.results if r.ticker == selected_ticker), None)
    if result is None or result.error:
        st.warning("No data for this ticker.")
        return

    _render_single_detail(result)


def _render_single_detail(r: StageResult):
    color = STAGE_COLOR[r.stage]

    # Header row: stage badge + action + bull prob
    c1, c2, c3 = st.columns([2, 2, 3])
    with c1:
        st.markdown(
            f"<div style='background:{color};color:white;padding:16px;"
            f"border-radius:8px;text-align:center'>"
            f"<div style='font-size:13px;opacity:0.9'>STAGE</div>"
            f"<div style='font-size:36px;font-weight:700'>{r.stage} {STAGE_ICON[r.stage]}</div>"
            f"<div style='font-size:15px'>{r.stage_name}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            f"<div style='padding:16px;border:1px solid #30363d;"
            f"border-radius:8px;text-align:center'>"
            f"<div style='font-size:13px;color:#8b949e'>BULL PROBABILITY</div>"
            f"<div style='font-size:36px;font-weight:700;color:{color}'>"
            f"{r.bull_probability:.0%}</div>"
            f"<div style='font-size:13px;color:#8b949e'>confidence {r.confidence:.0%}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            f"<div style='padding:16px;border:1px solid {color};"
            f"border-radius:8px;text-align:center'>"
            f"<div style='font-size:13px;color:#8b949e'>WEINSTEIN SIGNAL</div>"
            f"<div style='font-size:36px;font-weight:700;color:{color}'>{r.action}</div>"
            f"<div style='font-size:13px;color:#8b949e'>as of {r.as_of_date}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    # Metrics row
    st.markdown("")
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Price", f"${r.last_close:,.2f}")
    m2.metric("30W MA", f"${r.ma_30w:,.2f}")
    m3.metric("vs 30W MA", f"{r.pct_above_ma:+.1%}")
    m4.metric("MA Slope (4w)", f"{r.ma_slope_pct:+.1%}")
    m5.metric("Mansfield RS", f"{r.mansfield_rs:+.1f}")

    # Weinstein BUY confirmation gates — Ch. 1 + Ch. 2
    _render_buy_gates_panel(r)

    # If this is a Confirmed BUY, show "why this is on the list" justification
    if r.weinstein_buy_confirmed and r.action in ("BUY", "STRONG BUY"):
        _render_buy_justification(r)

    # Reasoning
    st.markdown("**Weinstein reasoning:**")
    for line in r.explanation:
        st.caption(f"  · {line}")

    # Trade Setup card (Weinstein classic)
    _render_trade_setup_card(r)

    # Chart: price + 30-week MA + trade levels
    _render_price_chart(r)

    # Position calculator — Weinstein-style P&L projection (next to chart)
    _render_position_calculator(r)


def _render_buy_gates_panel(r: StageResult):
    """
    Visualize Weinstein's two BUY confirmation gates (Ch. 1 + Ch. 2):
      1. Volume gate — recent 5-day max ≥ 1.5× 50-day avg
      2. Mansfield RS gate — RS line vs SPY > 0

    A Stage 2 stock that fails either gate is WATCH, not BUY. This panel
    makes that gate decision visible and audit-able for the demo.
    """
    vol_ok = r.volume_confirmed
    rs_ok = r.rs_confirmed
    gates_ok = r.weinstein_buy_confirmed
    # Extended Stage 2 (>40% above 30W MA, or other run-too-far rules) is NOT
    # a buy even if both gates pass — Weinstein Ch. 5: "Don't chase stocks
    # that have already had a major move." Wait for a basing pattern.
    extended = getattr(r, "setup_type", "n/a") == "extended"
    confirmed = gates_ok and not extended

    def gate_html(passed: bool, label: str, value: str, threshold: str, ref: str) -> str:
        bg = "#1a3d2e" if passed else "#3d1a1a"
        bd = "#3fb950" if passed else "#f85149"
        icon = "✓" if passed else "✗"
        verdict = "PASS" if passed else "FAIL"
        return (
            f"<div style='background:{bg};border:1px solid {bd};"
            f"border-radius:8px;padding:12px;height:100%;'>"
            f"<div style='font-size:11px;color:#8b949e;letter-spacing:1px'>"
            f"{label}</div>"
            f"<div style='font-size:22px;font-weight:700;color:{bd};margin-top:2px'>"
            f"{icon} {verdict}</div>"
            f"<div style='font-size:13px;color:#c9d1d9;margin-top:4px'>"
            f"{value} <span style='color:#8b949e'>(threshold: {threshold})</span></div>"
            f"<div style='font-size:11px;color:#8b949e;font-style:italic;margin-top:6px'>"
            f"{ref}</div>"
            f"</div>"
        )

    overall_bg = "#1a3d2e" if confirmed else "#3d2a1a"
    overall_bd = "#3fb950" if confirmed else "#d29922"
    if confirmed:
        overall_text = "✓ Weinstein BUY CONFIRMED"
        overall_subtext = (
            "Both gates pass and the run is not extended. A Stage 2 "
            "trade-trigger order is justified per Weinstein Ch. 1: volume "
            "must expand AND relative strength must be in positive territory."
        )
    elif extended and gates_ok:
        overall_text = "⚠ BUY NOT CONFIRMED — RUN IS EXTENDED"
        overall_subtext = (
            f"Volume and RS gates both pass, but {r.ticker} is "
            f"{r.pct_above_ma:+.1%} above its 30-week MA — the move is "
            "mature. Weinstein Ch. 5: 'Don't chase stocks that have already "
            "had a major move.' Wait for a basing pattern, or watch for "
            "a Stage 2 continuation breakout from a fresh consolidation."
        )
    else:
        overall_text = "⚠ Buy not confirmed — both gates must pass"
        overall_subtext = (
            "Stan Weinstein, Ch. 1: 'Never buy a stock when its "
            "relative-strength line is in negative territory, no matter "
            "how cheap it appears.' Wait for both gates to pass."
        )

    st.markdown("")
    st.markdown(
        f"<div style='background:{overall_bg};border:2px solid {overall_bd};"
        f"border-radius:10px;padding:14px;margin-bottom:8px'>"
        f"<div style='font-size:18px;font-weight:700;color:{overall_bd}'>"
        f"{overall_text}</div>"
        f"<div style='font-size:12px;color:#c9d1d9;margin-top:4px'>"
        f"{overall_subtext}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )

    g1, g2 = st.columns(2)
    with g1:
        st.markdown(
            gate_html(
                vol_ok,
                "GATE 1 · VOLUME EXPANSION",
                f"{r.volume_surge:.2f}× (5d max / 50d avg)",
                "≥ 1.5×",
                "Weinstein Ch. 1: 'buy on volume expansion at breakout'",
            ),
            unsafe_allow_html=True,
        )
    with g2:
        st.markdown(
            gate_html(
                rs_ok,
                "GATE 2 · MANSFIELD RELATIVE STRENGTH",
                f"{r.mansfield_rs:+.1f} vs SPY",
                "> 0",
                "Weinstein Ch. 1: RS line must be in positive territory",
            ),
            unsafe_allow_html=True,
        )


def _render_buy_justification(r: StageResult):
    """
    Show "why this BUY is on the list" for a Confirmed Stage 2 setup.

    Includes:
      - When the stock entered Stage 2 (Stage 1→2 cross date)
      - How long it's been in Stage 2 (weeks)
      - Mansfield RS persistence over last 12 weeks
      - Date the volume gate was confirmed
    """
    from ml.stage_analyzer import build_buy_justification

    with st.spinner(f"Building justification for {r.ticker}..."):
        j = build_buy_justification(r.ticker, r)

    st.markdown("")
    st.markdown(
        f"<div style='background:#0d2818;border:1px solid #3fb950;"
        f"border-radius:8px;padding:14px;margin-bottom:8px'>"
        f"<div style='font-size:13px;color:#3fb950;letter-spacing:1px;"
        f"font-weight:600'>"
        f"📊 WHY {r.ticker} EARNED ITS PLACE</div>"
        f"<div style='font-size:14px;color:#c9d1d9;margin-top:6px;line-height:1.6'>"
        f"{j['narrative']}"
        f"</div>"
        f"</div>",
        unsafe_allow_html=True,
    )

    # Compact metric row for the audit trail
    if any([j["stage_2_since"], j["rs_persistence_pct"] is not None,
            j["volume_breakout_date"]]):
        c1, c2, c3 = st.columns(3)
        if j["stage_2_since"]:
            c1.metric(
                "Stage 2 since",
                j["stage_2_since"],
                f"{j['weeks_in_stage_2']} weeks",
            )
        if j["rs_persistence_pct"] is not None:
            weeks_pos = int(round(j["rs_persistence_pct"] * 12))
            c2.metric("RS persistence (12w)", f"{weeks_pos}/12 weeks")
        if j["volume_breakout_date"]:
            c3.metric("Volume gate confirmed", j["volume_breakout_date"])


def _render_position_calculator(r: StageResult):
    """
    Position-sizing calculator with Weinstein-style P&L projections.

    User enters either a share count OR a dollar budget, then clicks the
    Calculate button to run the projection. Result is cached in session
    state so it persists until the user runs another calculation.

    Refuses to project on Late Stage 2 (optimal entry passed) or non-
    Stage-2 stocks — both produce descriptive (not prescriptive) info
    boxes that just state the stage and explain why the long-side
    measured-move math doesn't apply.
    """
    from ml.stage_analyzer import compute_position_projection

    st.markdown("---")
    st.markdown("### 🧮 Position Calculator — projected gains")
    st.caption(
        "Enter your budget or share count, then click **Calculate**. The "
        "calculator projects two targets: short-term swing (~4–8 weeks) and "
        "long-term measured move (~4–12 months). Time windows adjust to the "
        "current 30-week MA slope. Targets follow Weinstein Ch. 3 (% of "
        "52-week base depth)."
    )

    # Inputs — share count or dollar amount toggle
    input_mode = st.radio(
        "Size by",
        options=["💵 Dollar amount", "📊 Share count"],
        horizontal=True,
        key=f"calc_mode_{r.ticker}",
        label_visibility="collapsed",
    )

    col_in1, col_in2 = st.columns([1, 1])
    with col_in1:
        if input_mode.startswith("💵"):
            dollars = st.number_input(
                "💵 Dollars to invest",
                min_value=0.0,
                value=10000.0,
                step=500.0,
                key=f"calc_dollars_{r.ticker}",
                help="The calculator will compute the maximum whole shares "
                     "you can buy at the Weinstein entry price.",
            )
            shares = 0
        else:
            shares = st.number_input(
                "📊 Shares to buy",
                min_value=0,
                value=100,
                step=10,
                key=f"calc_shares_{r.ticker}",
            )
            dollars = 0.0
    with col_in2:
        st.markdown("")  # spacer to align button vertically
        st.markdown("")
        run_clicked = st.button(
            "🧮  Calculate projection",
            key=f"calc_run_{r.ticker}",
            type="primary",
            use_container_width=True,
        )

    # Cache result in session_state so it persists across reruns triggered
    # by other widgets on the page
    cache_key = f"calc_result_{r.ticker}"
    if run_clicked:
        st.session_state[cache_key] = compute_position_projection(
            r, shares=int(shares), dollars=float(dollars),
        )

    proj = st.session_state.get(cache_key)
    if proj is None:
        st.info(
            "💡 Set your budget or share count above, then click "
            "**🧮 Calculate projection** to see the Weinstein-style "
            "P&L estimate."
        )
        return

    if not proj.get("applicable"):
        # Descriptive (not prescriptive) — just describes the stage.
        st.info(
            f"ℹ {proj.get('error', 'Calculator not applicable for this ticker.')}"
        )
        return

    # ── Top row: position summary metrics ─────────────────────────────
    st.markdown("")
    s1, s2, s3, s4 = st.columns(4)
    s1.metric(
        "💼 Position size",
        f"{proj['shares']:,} sh",
        f"${proj['capital']:,.2f} capital",
    )
    s2.metric(
        "🎯 Entry price",
        f"${proj['entry']:,.2f}",
        f"stop ${proj['stop_loss']:,.2f}",
        delta_color="off",
    )
    s3.metric(
        "🛡 Max loss (if stopped)",
        f"−${proj['max_loss']:,.2f}",
        f"{proj['max_loss_pct']:.1%} of capital",
        delta_color="inverse",
    )
    s4.metric(
        "💰 Risk per share",
        f"${proj['risk_per_share']:,.2f}",
        "entry − 30W MA",
        delta_color="off",
    )

    # ── Projection cards: short-term + long-term, side by side ────────
    st.markdown("")
    st.markdown("**📈 Projected outcomes** *(technical signal, not prediction)*")
    short_col, long_col = st.columns(2)

    with short_col:
        st.markdown(
            f"<div style='background:#0d2818;border:2px solid #3fb950;"
            f"border-radius:10px;padding:16px;height:100%'>"
            f"<div style='font-size:12px;color:#3fb950;letter-spacing:1.5px;"
            f"font-weight:700'>🟢 SHORT-TERM SWING</div>"
            f"<div style='font-size:32px;font-weight:700;color:#fff;margin-top:8px'>"
            f"+${proj['short_profit']:,.2f}</div>"
            f"<div style='font-size:14px;color:#c9d1d9;margin-top:4px'>"
            f"<b>{proj['short_pct']:+.1%}</b> · target ${proj['short_target']:,.2f}</div>"
            f"<div style='font-size:13px;color:#8b949e;margin-top:8px'>"
            f"⏱ {proj['short_days_low']}–{proj['short_days_high']} trading days</div>"
            f"<div style='font-size:13px;color:#8b949e;margin-top:2px'>"
            f"⚖️ R/R {proj['short_rr']:.2f}:1</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    with long_col:
        st.markdown(
            f"<div style='background:#0d2138;border:2px solid #4a90e2;"
            f"border-radius:10px;padding:16px;height:100%'>"
            f"<div style='font-size:12px;color:#4a90e2;letter-spacing:1.5px;"
            f"font-weight:700'>🔵 LONG-TERM MEASURED MOVE</div>"
            f"<div style='font-size:32px;font-weight:700;color:#fff;margin-top:8px'>"
            f"+${proj['long_profit']:,.2f}</div>"
            f"<div style='font-size:14px;color:#c9d1d9;margin-top:4px'>"
            f"<b>{proj['long_pct']:+.1%}</b> · target ${proj['long_target']:,.2f}</div>"
            f"<div style='font-size:13px;color:#8b949e;margin-top:8px'>"
            f"⏱ {proj['long_days_low']}–{proj['long_days_high']} trading days</div>"
            f"<div style='font-size:13px;color:#8b949e;margin-top:2px'>"
            f"⚖️ R/R {proj['long_rr']:.2f}:1</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    # ── Reasoning expander ────────────────────────────────────────────
    st.markdown("")
    with st.expander("🔎 Reasoning — how these numbers were derived"):
        for line in proj["reasoning"]:
            st.markdown(f"- {line}")
        st.markdown("")
        st.caption(
            "**Short-term target** = entry + 25% of 52-week base depth · "
            "**Long-term target** = entry + 100% of base depth (full measured "
            "move). Per Stan Weinstein, *Secrets for Profiting in Bull and "
            "Bear Markets*, Chapter 3 (\"The Ideal Time to Buy\")."
        )


def _render_trade_setup_card(r: StageResult):
    """Weinstein trade setup card with Entry / Stop / Target."""
    ts = r.trade_setup
    if ts is None or not ts.applicable:
        st.info(
            f"📭 **No Weinstein setup right now.** "
            f"{ts.narrative if ts else 'Wait for a Stage 2 breakout.'}"
        )
        return

    st.markdown("---")
    st.markdown("### 🎯 Weinstein Trade Setup")

    # Color by direction
    direction_color = STAGE_COLOR[2] if ts.direction == "LONG" else STAGE_COLOR[4]

    # Three-card display: Entry / Stop / Target
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(
            f"<div style='padding:16px;border:2px solid {direction_color};"
            f"border-radius:8px;text-align:center;background:#0e1117'>"
            f"<div style='font-size:11px;color:#8b949e;text-transform:uppercase'>"
            f"{ts.entry_type}</div>"
            f"<div style='font-size:26px;font-weight:700;color:{direction_color}'>"
            f"${ts.entry_price:,.2f}</div>"
            f"<div style='font-size:11px;color:#8b949e'>entry trigger</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            f"<div style='padding:16px;border:2px solid #ff1744;"
            f"border-radius:8px;text-align:center;background:#0e1117'>"
            f"<div style='font-size:11px;color:#8b949e;text-transform:uppercase'>"
            f"Stop-Loss</div>"
            f"<div style='font-size:26px;font-weight:700;color:#ff1744'>"
            f"${ts.stop_loss:,.2f}</div>"
            f"<div style='font-size:11px;color:#8b949e'>30-week MA invalidator</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            f"<div style='padding:16px;border:2px solid #00c853;"
            f"border-radius:8px;text-align:center;background:#0e1117'>"
            f"<div style='font-size:11px;color:#8b949e;text-transform:uppercase'>"
            f"Target 1</div>"
            f"<div style='font-size:26px;font-weight:700;color:#00c853'>"
            f"${ts.target_1:,.2f}</div>"
            f"<div style='font-size:11px;color:#8b949e'>measured move</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
    with c4:
        rr_color = "#00c853" if ts.risk_reward_ratio >= 2.0 else "#ffc107"
        if ts.risk_reward_ratio < 1.0:
            rr_color = "#ff9800"
        st.markdown(
            f"<div style='padding:16px;border:2px solid {rr_color};"
            f"border-radius:8px;text-align:center;background:#0e1117'>"
            f"<div style='font-size:11px;color:#8b949e;text-transform:uppercase'>"
            f"Risk / Reward</div>"
            f"<div style='font-size:26px;font-weight:700;color:{rr_color}'>"
            f"{ts.risk_reward_ratio:.2f}:1</div>"
            f"<div style='font-size:11px;color:#8b949e'>"
            f"risk ${ts.risk_per_share:.2f} · reward ${ts.reward_per_share:.2f}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    # Narrative
    st.markdown("")
    st.markdown(ts.narrative)
    st.caption(
        "⚠ This is a technical signal, not a prediction. Always size positions "
        "to the stop-loss distance, not to the entry price."
    )


def _render_price_chart(r: StageResult):
    """
    Plot price + 30-week MA + Weinstein trade levels + stage transition
    arrows + YOU ARE HERE marker for a ticker.
    """
    ticker = r.ticker
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="2y", auto_adjust=True)
        if hist is None or hist.empty:
            return
    except Exception:
        return

    from ml.stage_analyzer import MA_WINDOW_DAYS, VOLUME_AVG_WINDOW
    from ui.components.chart_helpers import (
        add_trade_setup_lines,
        add_transition_markers,
        add_you_are_here,
        detect_stage_transitions,
        trade_setup_legend_html,
    )
    hist["MA30W"] = hist["Close"].rolling(MA_WINDOW_DAYS, min_periods=MA_WINDOW_DAYS).mean()
    # 50-day volume baseline (Weinstein gate reference)
    hist["VolBase"] = hist["Volume"].rolling(VOLUME_AVG_WINDOW, min_periods=10).mean()
    # Color volume bars green on up days, red on down days (Mansfield convention)
    up_day = hist["Close"] >= hist["Close"].shift(1)
    bar_colors = ["#3fb950" if u else "#f85149" for u in up_day]

    # Two-row subplot: price (75%) + volume (25%) — exactly the Mansfield chart layout
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        row_heights=[0.75, 0.25],
        subplot_titles=("", ""),
    )

    # ── Row 1: Price + 30-week MA ──────────────────────────────────────────
    fig.add_trace(go.Scatter(
        x=hist.index, y=hist["Close"], mode="lines",
        name="Close", line=dict(color="#4a90e2", width=2),
        hovertemplate="%{x|%b %d, %Y}<br>Price $%{y:,.2f}<extra></extra>",
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=hist.index, y=hist["MA30W"], mode="lines",
        name="30-week MA", line=dict(color="#ff9800", width=2, dash="dash"),
        hovertemplate="%{x|%b %d, %Y}<br>30W MA $%{y:,.2f}<extra></extra>",
    ), row=1, col=1)

    # Trade setup: horizontal lines only (no text annotations)
    add_trade_setup_lines(fig, r.trade_setup)

    # Stage transition markers — triangles, hover for details
    add_transition_markers(fig, detect_stage_transitions(hist))

    # YOU ARE HERE — single circle marker, hover for details
    add_you_are_here(fig, hist, r.stage, r.stage_name)

    # ── Row 2: Volume bars + 50-day baseline ───────────────────────────────
    fig.add_trace(go.Bar(
        x=hist.index, y=hist["Volume"],
        name="Volume",
        marker_color=bar_colors,
        marker_line_width=0,
        opacity=0.85,
        hovertemplate="%{x|%b %d, %Y}<br>Vol %{y:,.0f}<extra></extra>",
        showlegend=False,
    ), row=2, col=1)
    fig.add_trace(go.Scatter(
        x=hist.index, y=hist["VolBase"], mode="lines",
        name="50d avg vol",
        line=dict(color="#8b949e", width=1.5, dash="dot"),
        hovertemplate="%{x|%b %d, %Y}<br>50d avg %{y:,.0f}<extra></extra>",
    ), row=2, col=1)
    # Highlight the 5-day window the volume gate measures
    if len(hist) >= 5:
        last5 = hist.iloc[-5:]
        fig.add_vrect(
            x0=last5.index[0], x1=last5.index[-1],
            fillcolor="#ffc107", opacity=0.10,
            line_width=0, layer="below",
            row=2, col=1,
        )

    title = f"{ticker} — Weinstein chart  ·  hover any marker for details"
    fig.update_layout(
        title=title,
        height=600,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#e6edf3",
        dragmode=False,
        hovermode="closest",
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
            bgcolor="rgba(0,0,0,0)",
        ),
        margin=dict(r=30, t=60, b=30),
        bargap=0.0,
    )
    fig.update_xaxes(showgrid=False, fixedrange=True, row=1, col=1)
    fig.update_xaxes(showgrid=False, fixedrange=True, row=2, col=1)
    fig.update_yaxes(
        title_text="Price ($)",
        showgrid=True, gridcolor="#30363d", fixedrange=True, row=1, col=1,
    )
    fig.update_yaxes(
        title_text="Volume",
        showgrid=True, gridcolor="#30363d", fixedrange=True,
        row=2, col=1,
    )
    st.plotly_chart(fig, use_container_width=True,
                    config={"scrollZoom": False, "displayModeBar": False})

    # Trade setup pill legend below the chart (clean, non-stacking)
    legend_html = trade_setup_legend_html(r.trade_setup)
    if legend_html:
        st.markdown(legend_html, unsafe_allow_html=True)
    st.caption(
        "💡 Hover any triangle or circle on the chart for the full Weinstein "
        "interpretation (date, price, meaning)."
    )


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def render_trader_mode():
    """Top-level Trader Mode renderer. Call from app.py."""
    tickers = render_watchlist_sidebar()

    st.markdown("## 📈 Trader Mode — Weinstein Stage Analysis")
    st.caption(
        "Pure methodology from Stan Weinstein's *Secrets for Profiting in Bull and Bear Markets*. "
        "Scans your scan list for Stage 2 breakouts (BUY) and Stage 4 breakdowns (SELL/SHORT). "
        "Runs in ~0.5s per ticker via yfinance. No LLM."
    )

    # Market Regime banner — Weinstein Ch. 8 "No Isolationism" rule
    _render_market_regime_banner()

    render_scan_panel(tickers)

    # ⚠️ Full disclaimer — collapsible, at bottom of Trader Mode
    st.divider()
    render_disclaimer(compact=False)
    render_disclaimer(compact=True)


def _render_market_regime_banner():
    """Show current market regime (SPY stage + momentum + breadth)."""
    from ml.market_context import compute_market_regime

    # Use the most recent scan for breadth if available
    latest_scan = None
    for k, v in st.session_state.items():
        if k.startswith("scan_") and hasattr(v, "stage_buckets"):
            latest_scan = v
            break

    with st.spinner("Reading market regime..."):
        regime = compute_market_regime(latest_scan)

    if regime.error:
        st.caption(f"Market regime unavailable: {regime.error}")
        return

    # Banner
    c1, c2 = st.columns([2, 5])
    with c1:
        st.markdown(
            f"<div style='background:{regime.regime_color};color:white;"
            f"padding:14px;border-radius:8px;text-align:center'>"
            f"<div style='font-size:11px;opacity:0.9;letter-spacing:1px'>"
            f"WEINSTEIN CH. 8 — MARKET REGIME</div>"
            f"<div style='font-size:22px;font-weight:700;margin-top:4px'>"
            f"{regime.regime_emoji} {regime.regime}</div>"
            f"<div style='font-size:12px;opacity:0.9;margin-top:2px'>"
            f"SPY Stage {regime.spy_stage} ({regime.spy_stage_name})</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
    with c2:
        momentum_note = (
            "✓ momentum tailwind (SMA50 > SMA200, rising)"
            if regime.momentum_tailwind else
            "✗ no momentum tailwind"
        )
        breadth_note = ""
        if regime.breadth_pct is not None:
            breadth_note = (
                f" · Breadth **{regime.breadth_pct:.0%}** "
                f"({regime.breadth_stage2} Stage 2 / "
                f"{regime.breadth_stage4} Stage 4)"
            )
        st.markdown(
            f"**{regime.regime_headline}**  \n"
            f"📏 SPY ${regime.spy_price:,.2f} vs 30W MA ${regime.spy_ma_30w:,.2f} "
            f"({regime.spy_pct_above_ma:+.1%}, slope {regime.spy_ma_slope_pct:+.1%}/mo) · "
            f"{momentum_note}{breadth_note}  \n"
            f"💡 **Playbook:** {regime.regime_action}  \n"
            f"_Weinstein Ch. 8: 'No stock is an island. Know the market's stage first.'_"
        )
