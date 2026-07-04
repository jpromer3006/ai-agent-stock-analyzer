"""
calendar_heatmap.py — Streamlit widget for the Chapter 3 calendar context.

Renders three signals in a single compact panel that the Research dossier
drops into Section 3:

    1. Monthly seasonality grid (12 months in a 3 by 4 calendar layout,
       each month colored by historical positivity rate).
    2. Day-of-week strip (5 weekdays in a column, colored similarly).
    3. Holiday adjacency badge and a plain-language verdict line.

Today's cell in both grids is outlined in white so the user's eye goes
straight to it.

Designed for sub-second render. No external assets, no network calls
(the underlying tables in ml/calendar_context.py are precomputed and
cached on disk).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import streamlit as st

from ml.calendar_context import get_calendar_context


# ---------------------------------------------------------------------------
# Local label tables (kept private to this module for visual independence)
# ---------------------------------------------------------------------------

_MONTH_SHORT = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr",
    5: "May", 6: "Jun", 7: "Jul", 8: "Aug",
    9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec",
}

_DOW_SHORT = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"}


# ---------------------------------------------------------------------------
# Color mapping — heatmap palette
# ---------------------------------------------------------------------------

def _pct_to_color(pct: float) -> str:
    """Map a positivity rate (0.0-1.0) to a heatmap hex color.

    The dividing line is 0.55 (slightly above 50 percent random, since
    long-run equities have a positive drift). Below 0.50 trends red,
    above 0.60 trends green, between is neutral gray.
    """
    p = max(0.40, min(0.78, pct))  # clamp into the displayed band
    if p < 0.50:
        # Cool red, intensity scales with how far below 0.50
        t = (0.50 - p) / 0.10  # 0..1
        r = 248
        g = int(110 - t * 30)
        b = int(110 - t * 40)
        return f"rgb({r},{g},{b})"
    elif p > 0.60:
        # Green, intensity scales with how far above 0.60
        t = min(1.0, (p - 0.60) / 0.18)  # 0..1
        r = int(63 - t * 30)
        g = int(170 + t * 80)
        b = int(80 - t * 20)
        return f"rgb({r},{g},{b})"
    else:
        # Neutral gray band 0.50-0.60
        return "rgb(120, 130, 145)"


# ---------------------------------------------------------------------------
# Public widget
# ---------------------------------------------------------------------------

def render_calendar_heatmap(date: Optional[datetime] = None,
                            section_header: bool = True) -> None:
    """Render the Chapter 3 calendar context into the current Streamlit container.

    Parameters
    ----------
    date : optional datetime to render context for (defaults to today)
    section_header : if True (default), render a Markdown header above.
        Set to False when the caller already prints a header.
    """
    ctx = get_calendar_context(date=date)
    if ctx.error:
        st.warning(f"Calendar context unavailable: {ctx.error}")
        return

    if section_header:
        st.markdown(
            "#### 📅 Calendar Context "
            "<span style='color:#8b949e;font-weight:400;font-size:14px'>"
            f"— Chapter 3 timing window for {ctx.date}</span>",
            unsafe_allow_html=True,
        )

    # ── Presidential-cycle banner (Weinstein Ch.3, pp.69-70) ────────────
    if ctx.cycle_book_note:
        st.markdown(
            f"<div style='background:#161b22;border:1px solid #f0a93b;"
            f"border-radius:8px;padding:10px 12px;margin-bottom:10px'>"
            f"<span style='font-size:11px;color:#f0a93b;letter-spacing:1px;"
            f"font-weight:700'>🗳 PRESIDENTIAL CYCLE · WEINSTEIN CH.3</span><br>"
            f"<span style='color:#c9d1d9;font-size:13px'>{ctx.cycle_book_note}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )

    # Seasonality lens toggle: all years vs the current cycle phase only.
    # This is what "changes the chart" — the grid recolors to show, e.g.,
    # June in midterm years rather than June across every year.
    has_cycle = bool(ctx.cycle_monthly_positivity)
    use_cycle = False
    if has_cycle:
        lens = st.radio(
            "Seasonality lens",
            options=["All years", f"{ctx.cycle_phase_label} only"],
            horizontal=True,
            label_visibility="collapsed",
            key=f"cal_cycle_lens_{ctx.date}",
        )
        use_cycle = lens != "All years"
    monthly_src = ctx.cycle_monthly_positivity if use_cycle else ctx.monthly_positivity

    # ── Top row: monthly grid (3/4 width) + day-of-week strip (1/4 width) ──
    grid_col, dow_col = st.columns([3, 1])

    with grid_col:
        if use_cycle:
            st.caption(
                f"**Monthly seasonality — {ctx.cycle_phase_label} only** "
                f"({ctx.cycle_sample_years} comparable years since "
                f"{ctx.history_start_year}). Weinstein Ch.3 cycle lens."
            )
        else:
            st.caption(
                "**Monthly seasonality** — percent of years each month closed "
                f"net positive ({ctx.history_start_year} to {ctx.history_end_year})"
            )
        # 3 columns by 4 rows calendar layout
        rows_html = []
        for row_start in (1, 4, 7, 10):
            cells = []
            for m in range(row_start, row_start + 3):
                pct = monthly_src.get(m, 0.5)
                color = _pct_to_color(pct)
                is_current = m == ctx.current_month
                if is_current:
                    border = "3px solid #ffffff"
                    extra_shadow = "box-shadow:0 0 8px rgba(255,255,255,0.4);"
                else:
                    border = "1px solid #30363d"
                    extra_shadow = ""
                rank_chip = ""
                rank_str = (
                    f"#{ctx.current_month_rank}/12"
                    if is_current and ctx.current_month_rank and not use_cycle else ""
                )
                if rank_str:
                    rank_chip = (
                        f"<div style='font-size:9px;opacity:0.85;"
                        f"margin-top:2px'>rank {rank_str}</div>"
                    )
                cells.append(
                    f"<div style='background:{color};color:white;"
                    f"padding:10px 6px;text-align:center;border:{border};"
                    f"border-radius:6px;flex:1;{extra_shadow}'>"
                    f"<div style='font-size:11px;opacity:0.9;"
                    f"letter-spacing:0.5px'>{_MONTH_SHORT[m]}</div>"
                    f"<div style='font-size:18px;font-weight:700;"
                    f"margin-top:2px'>{pct:.0%}</div>"
                    f"{rank_chip}"
                    f"</div>"
                )
            rows_html.append(
                f"<div style='display:flex;gap:6px;margin-bottom:6px'>"
                f"{''.join(cells)}</div>"
            )
        st.markdown("".join(rows_html), unsafe_allow_html=True)

    with dow_col:
        # Day-of-week strip honors the same cycle lens as the monthly grid.
        dow_on_cycle = use_cycle and bool(ctx.cycle_dow_positivity)
        dow_src = ctx.cycle_dow_positivity if dow_on_cycle else ctx.dow_positivity
        if dow_on_cycle:
            st.caption(
                f"**Day of week — {ctx.cycle_phase_label} only** "
                f"({ctx.cycle_sample_years} comparable years)"
            )
        else:
            st.caption("**Day of week**")
        for d in range(5):
            pct = dow_src.get(d, 0.5)
            color = _pct_to_color(pct)
            is_today = d == ctx.current_dow
            if is_today:
                border = "3px solid #ffffff"
                shadow = "box-shadow:0 0 8px rgba(255,255,255,0.4);"
            else:
                border = "1px solid #30363d"
                shadow = ""
            rank_chip = ""
            if is_today and ctx.current_dow_rank and not use_cycle:
                rank_chip = (
                    f"<div style='font-size:9px;opacity:0.85;margin-top:2px'>"
                    f"rank #{ctx.current_dow_rank}/5</div>"
                )
            st.markdown(
                f"<div style='background:{color};color:white;padding:8px;"
                f"text-align:center;border:{border};border-radius:6px;"
                f"margin-bottom:4px;{shadow}'>"
                f"<div style='font-size:10px;opacity:0.9'>{_DOW_SHORT[d]}</div>"
                f"<div style='font-size:14px;font-weight:700;margin-top:1px'>"
                f"{pct:.0%}</div>"
                f"{rank_chip}"
                f"</div>",
                unsafe_allow_html=True,
            )

    # ── Bottom strip: holiday adjacency badge + verdict ──────────────────
    if ctx.holiday_adjacency:
        emoji = "🎉" if ctx.holiday_position == "before" else "🔁"
        bg = "#1a3d2e" if ctx.holiday_position == "before" else "#2a3140"
        border_c = "#3fb950" if ctx.holiday_position == "before" else "#8b949e"
        st.markdown(
            f"<div style='background:{bg};border:1px solid {border_c};"
            f"border-radius:6px;padding:10px;margin-top:10px'>"
            f"<strong>{emoji} Holiday adjacency:</strong> Today is the "
            f"trading day {ctx.holiday_position} {ctx.holiday_name}. "
            f"Hirsch's day-of-week study found this position carries a "
            f"small seasonal effect."
            f"</div>",
            unsafe_allow_html=True,
        )

    st.markdown("")
    st.markdown(f"**📝 Calendar verdict.** {ctx.verdict_line}")
    if ctx.party_note:
        st.caption(f"🗳 {ctx.party_note}")
    st.caption(
        f"Tables computed from S&P 500 daily history, "
        f"{ctx.history_start_year} through {ctx.history_end_year}. "
        f"Replicates the spirit of Merrill's monthly study and Hirsch's "
        f"day-of-week findings using extended modern data. Descriptive, "
        f"not deterministic — Weinstein's discipline always overrides "
        f"calendar context."
    )


# ---------------------------------------------------------------------------
# Lightweight inline variant — for use inside other components
# ---------------------------------------------------------------------------

def render_calendar_summary(date: Optional[datetime] = None) -> None:
    """A one-line calendar caption — for use in compact spots.

    Doesn't draw the grid, just renders the verdict line.
    """
    ctx = get_calendar_context(date=date)
    if ctx.error:
        return
    st.caption(f"📅 {ctx.verdict_line}")
