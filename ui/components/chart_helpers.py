"""
chart_helpers.py — Shared Plotly chart annotations for Weinstein charts.

Used by both Research Mode chat and Trader Mode detail view so the
visual language is consistent:

    ↑ (green) — Crossed above 30-week MA   (Stage 1→2 transition)
    ↓ (red)   — Crossed below 30-week MA   (Stage 2/3→4 transition)
    📍        — "YOU ARE HERE" marker at current price, colored by stage
    Entry / Stop / Target horizontal overlays (when trade setup applies)
"""

from __future__ import annotations

from typing import Optional

import plotly.graph_objects as go


STAGE_ICON = {1: "◯", 2: "✓", 3: "⚠", 4: "✗"}
STAGE_COLOR = {1: "#6c757d", 2: "#00c853", 3: "#ffc107", 4: "#ff1744"}


def detect_stage_transitions(hist, lookback_days: int = 252) -> list[dict]:
    """
    Scan price history for MA-cross transitions in the last N days.
    Returns dedupe'd list of {date, price, label, color} (max 4).

    Parameters
    ----------
    hist : DataFrame with columns 'Close' and 'MA30W'
    lookback_days : only consider transitions in the last N trading days
    """
    if "MA30W" not in hist.columns or hist["MA30W"].isna().all():
        return []

    close = hist["Close"]
    ma = hist["MA30W"]

    above = (close > ma).astype(int)
    cross = above.diff().fillna(0)
    cutoff = hist.index[-min(lookback_days, len(hist))]

    transitions: list[dict] = []
    for dt, val in cross.items():
        if dt < cutoff:
            continue
        if val == 1:
            transitions.append({
                "date": dt, "price": float(close.loc[dt]),
                "label": "↑ Crossed above MA",
                "color": "#00c853",
            })
        elif val == -1:
            transitions.append({
                "date": dt, "price": float(close.loc[dt]),
                "label": "↓ Crossed below MA",
                "color": "#ff1744",
            })

    # Dedupe close transitions (within 14 days of each other → keep first)
    deduped: list[dict] = []
    for t in transitions:
        if deduped and (t["date"] - deduped[-1]["date"]).days < 14:
            continue
        deduped.append(t)
    return deduped[-4:]


def add_transition_arrows(fig: go.Figure, transitions: list[dict]):
    """Add arrow annotations for each stage transition."""
    for t in transitions:
        fig.add_annotation(
            x=t["date"], y=t["price"],
            text=t["label"],
            showarrow=True,
            arrowhead=2, arrowsize=1.2, arrowwidth=2,
            arrowcolor=t["color"],
            ax=0, ay=-40,
            font=dict(color=t["color"], size=11, family="Arial"),
            bgcolor="rgba(14,17,23,0.8)",
            bordercolor=t["color"],
            borderwidth=1,
            borderpad=4,
        )


def add_you_are_here(fig: go.Figure, hist, stage: int, stage_name: str):
    """Add a 'YOU ARE HERE' arrow at the latest close, colored by stage."""
    if hist is None or hist.empty:
        return
    last_dt = hist.index[-1]
    last_close = float(hist["Close"].iloc[-1])
    color = STAGE_COLOR.get(stage, "#4a90e2")
    icon = STAGE_ICON.get(stage, "?")
    fig.add_annotation(
        x=last_dt, y=last_close,
        text=f"<b>📍 Stage {stage} {icon} {stage_name}</b><br>${last_close:,.2f}",
        showarrow=True,
        arrowhead=2, arrowsize=1.5, arrowwidth=3,
        arrowcolor=color,
        ax=-80, ay=-60,
        font=dict(color="#e6edf3", size=12),
        bgcolor=color,
        bordercolor="white", borderwidth=1, borderpad=6,
    )


def add_trade_setup_lines(fig: go.Figure, trade_setup):
    """Overlay Buy/Stop/Target horizontal lines from a TradeSetup."""
    if trade_setup is None or not getattr(trade_setup, "applicable", False):
        return
    ts = trade_setup
    fig.add_hline(
        y=ts.entry_price, line_dash="solid", line_color="#00c853", line_width=2,
        annotation_text=f"  {ts.entry_type} ${ts.entry_price:,.2f}",
        annotation_position="right",
        annotation=dict(font=dict(color="#00c853", size=11)),
    )
    fig.add_hline(
        y=ts.stop_loss, line_dash="dot", line_color="#ff1744", line_width=2,
        annotation_text=f"  Stop ${ts.stop_loss:,.2f}",
        annotation_position="right",
        annotation=dict(font=dict(color="#ff1744", size=11)),
    )
    fig.add_hline(
        y=ts.target_1, line_dash="dashdot", line_color="#00c853", line_width=1,
        annotation_text=f"  Target ${ts.target_1:,.2f}",
        annotation_position="right",
        annotation=dict(font=dict(color="#00c853", size=11)),
    )
