"""
trader_mode.py — Streamlit UI for Weinstein Stage Analysis.

Layout:
    - Top: watchlist picker + "Scan Now" button
    - Leaderboard: Stage 2 breakouts + Stage 4 breakdowns side-by-side
    - Full table with all tickers sortable
    - Detail view: click a ticker to see price chart + 30-week MA + signal panel
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
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
from ml.batch_scanner import ScanReport, scan_universe
from ml.stage_analyzer import StageResult, analyze_stage


# ---------------------------------------------------------------------------
# Stage colors (used everywhere)
# ---------------------------------------------------------------------------
STAGE_COLOR = {1: "#6c757d", 2: "#00c853", 3: "#ffc107", 4: "#ff1744"}
STAGE_ICON = {1: "◯", 2: "✓", 3: "⚠", 4: "✗"}


# ---------------------------------------------------------------------------
# Watchlist management sidebar
# ---------------------------------------------------------------------------

def render_watchlist_sidebar():
    """Watchlist picker + ticker add/remove in the sidebar. Returns selected tickers."""
    st.sidebar.markdown("### 📈 Trader Mode")
    st.sidebar.caption("Weinstein Stage Analysis · daily scan")

    watchlists = list_watchlists()

    # Watchlist selector
    if "trader_watchlist" not in st.session_state:
        st.session_state.trader_watchlist = DEFAULT_NAME
    if st.session_state.trader_watchlist not in watchlists:
        st.session_state.trader_watchlist = watchlists[0] if watchlists else DEFAULT_NAME

    selected = st.sidebar.selectbox(
        "Watchlist",
        options=watchlists,
        index=watchlists.index(st.session_state.trader_watchlist)
        if st.session_state.trader_watchlist in watchlists else 0,
        key="watchlist_selector",
    )
    st.session_state.trader_watchlist = selected

    tickers = load_watchlist(selected)
    st.sidebar.caption(f"{len(tickers)} tickers")

    # New watchlist creator
    with st.sidebar.expander("➕ New watchlist"):
        new_name = st.text_input("Name", key="new_wl_name")
        new_tickers_raw = st.text_area(
            "Tickers (one per line or comma-separated)",
            placeholder="AAPL\nMSFT\nNVDA",
            key="new_wl_tickers",
        )
        if st.button("Create", key="new_wl_btn"):
            if new_name and new_tickers_raw:
                parsed = []
                for chunk in new_tickers_raw.replace(",", "\n").split("\n"):
                    c = chunk.strip().upper()
                    if c:
                        parsed.append(c)
                if parsed:
                    save_watchlist(new_name, parsed)
                    st.session_state.trader_watchlist = new_name
                    st.rerun()

    # Edit current watchlist
    with st.sidebar.expander(f"✏️ Edit '{selected}'"):
        add_t = st.text_input("Add ticker", key="add_ticker_input", placeholder="e.g. TSLA")
        c1, c2 = st.columns(2)
        if c1.button("Add", key="add_btn"):
            if add_t:
                _add_ticker(selected, add_t)
                st.rerun()
        if c2.button("🗑 Delete watchlist", key="del_wl",
                     disabled=(selected == DEFAULT_NAME)):
            if delete_watchlist(selected):
                st.session_state.trader_watchlist = DEFAULT_NAME
                st.rerun()

        if tickers:
            rm = st.selectbox("Remove ticker", options=tickers, key="rm_selector")
            if st.button("Remove", key="rm_btn"):
                _remove_ticker(selected, rm)
                st.rerun()

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

    # Cache key changes when watchlist changes
    cache_key = f"scan_{st.session_state.trader_watchlist}_{len(tickers)}"

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

    # Two side-by-side leaderboards
    left, right = st.columns(2)

    with left:
        st.markdown("#### 🟢 Stage 2 Breakouts (BUY zone)")
        top = report.stage2_breakouts[:10]
        if top:
            _render_leader_rows(top, direction="bull")
        else:
            st.info("No Stage 2 candidates in this watchlist right now.")

    with right:
        st.markdown("#### 🔴 Stage 4 Breakdowns (SELL/SHORT zone)")
        top = report.stage4_breakdowns[:10]
        if top:
            _render_leader_rows(top, direction="bear")
        else:
            st.info("No Stage 4 candidates in this watchlist right now.")


def _render_leader_rows(results: list[StageResult], direction: str = "bull"):
    for r in results:
        color = STAGE_COLOR[r.stage]
        # Click-to-detail — store selected ticker in session state
        ticker_button = f"**{r.ticker}**"
        cols = st.columns([2, 2, 3, 2])
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
        cols[3].markdown(
            f"<div style='padding-top:6px;font-size:13px;text-align:right'>"
            f"<span style='background:{color};color:white;padding:2px 8px;"
            f"border-radius:4px;font-weight:600'>{r.action}</span></div>",
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Full sortable table
# ---------------------------------------------------------------------------

def _render_full_table(report: ScanReport):
    rows = []
    for r in report.results:
        if r.error:
            continue
        rows.append({
            "Ticker": r.ticker,
            "Stage": f"{r.stage} {STAGE_ICON[r.stage]} {r.stage_name}",
            "Bull Prob": r.bull_probability,
            "Action": r.action,
            "Price": r.last_close,
            "vs 30W MA": r.pct_above_ma,
            "MA Slope (4w)": r.ma_slope_pct,
            "RS vs SPY": r.mansfield_rs,
            "Volume x": r.volume_surge,
            "52W High": r.pct_from_52w_high,
        })

    if not rows:
        st.info("No successful scans to display.")
        return

    df = pd.DataFrame(rows)
    # Sort by bull prob descending by default
    df = df.sort_values("Bull Prob", ascending=False).reset_index(drop=True)

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

    # Reasoning
    st.markdown("**Weinstein reasoning:**")
    for line in r.explanation:
        st.caption(f"  · {line}")

    # Chart: price + 30-week MA
    _render_price_chart(r.ticker)


def _render_price_chart(ticker: str):
    """Plot price + 30-week MA for a ticker."""
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="2y", auto_adjust=True)
        if hist is None or hist.empty:
            return
    except Exception:
        return

    from ml.stage_analyzer import MA_WINDOW_DAYS
    hist["MA30W"] = hist["Close"].rolling(MA_WINDOW_DAYS, min_periods=MA_WINDOW_DAYS).mean()

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=hist.index, y=hist["Close"], mode="lines",
        name="Close", line=dict(color="#4a90e2", width=2),
    ))
    fig.add_trace(go.Scatter(
        x=hist.index, y=hist["MA30W"], mode="lines",
        name="30-week MA", line=dict(color="#ff9800", width=2, dash="dash"),
    ))
    fig.update_layout(
        title=f"{ticker} — Price vs 30-week Moving Average (Weinstein's key indicator)",
        xaxis_title=None,
        yaxis_title="Price ($)",
        height=400,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#e6edf3",
        xaxis=dict(showgrid=False, fixedrange=True),
        yaxis=dict(showgrid=True, gridcolor="#30363d", fixedrange=True),
        dragmode=False,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig, use_container_width=True,
                    config={"scrollZoom": False, "displayModeBar": False})


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def render_trader_mode():
    """Top-level Trader Mode renderer. Call from app.py."""
    tickers = render_watchlist_sidebar()

    st.markdown("## 📈 Trader Mode — Weinstein Stage Analysis")
    st.caption(
        "Pure methodology from Stan Weinstein's *Secrets for Profiting in Bull and Bear Markets*. "
        "Scans your watchlist for Stage 2 breakouts (BUY) and Stage 4 breakdowns (SELL/SHORT). "
        "Runs in ~0.5s per ticker via yfinance. No LLM."
    )

    render_scan_panel(tickers)
