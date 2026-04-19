"""
chart_helpers.py — Shared Plotly chart annotations for Weinstein charts.

Design: **clean baseline visual, rich details on hover.** Avoids
stacked text annotations that clutter the chart when multiple
transitions occur close together.

    ▲ green triangle  — Stage 1→2 transition (crossed above 30W MA)
    ▼ red triangle    — Stage 2/3→4 transition (crossed below 30W MA)
    ● colored circle  — "YOU ARE HERE" at current close (color = stage)
    ─── green         — Buy-Stop / Sell-Stop entry
    ··· red           — Stop-Loss at 30W MA
    ─·─ green         — Target 1 (measured move)

All detail text is delivered through hovertemplate rather than
permanent annotations. Hover any marker to see the full Weinstein
interpretation, date, and price.
"""

from __future__ import annotations

from typing import Optional

import plotly.graph_objects as go


STAGE_ICON = {1: "◯", 2: "✓", 3: "⚠", 4: "✗"}
STAGE_COLOR = {1: "#6c757d", 2: "#00c853", 3: "#ffc107", 4: "#ff1744"}
STAGE_NAME = {1: "Basing", 2: "Advancing", 3: "Topping", 4: "Declining"}


# ---------------------------------------------------------------------------
# Transition detection
# ---------------------------------------------------------------------------

def detect_stage_transitions(hist, lookback_days: int = 252) -> list[dict]:
    """
    Scan price history for MA-cross transitions in the last N days.
    Returns dedupe'd list of {date, price, label, color, direction} (max 6).
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
                "label": "Crossed ABOVE 30W MA",
                "meaning": "Stage 1 → Stage 2 transition. Weinstein BUY zone begins.",
                "color": "#00c853",
                "direction": "up",
            })
        elif val == -1:
            transitions.append({
                "date": dt, "price": float(close.loc[dt]),
                "label": "Crossed BELOW 30W MA",
                "meaning": "Stage 2/3 → Stage 4 transition. Weinstein SELL zone begins.",
                "color": "#ff1744",
                "direction": "down",
            })

    # Dedupe close transitions (within 14 days of each other → keep first)
    deduped: list[dict] = []
    for t in transitions:
        if deduped and (t["date"] - deduped[-1]["date"]).days < 14:
            continue
        deduped.append(t)
    return deduped[-6:]


# ---------------------------------------------------------------------------
# Transition markers (hover-only, no stacking)
# ---------------------------------------------------------------------------

def add_transition_markers(fig: go.Figure, transitions: list[dict]):
    """
    Add triangle markers for each stage transition.
    Details (date, price, meaning) appear only on hover — no permanent text.
    """
    if not transitions:
        return

    # Split by direction so we can give each a legend entry + icon
    ups = [t for t in transitions if t["direction"] == "up"]
    downs = [t for t in transitions if t["direction"] == "down"]

    if ups:
        fig.add_trace(go.Scatter(
            x=[t["date"] for t in ups],
            y=[t["price"] for t in ups],
            mode="markers",
            name="▲ Crossed above MA",
            marker=dict(
                symbol="triangle-up", size=14,
                color="#00c853",
                line=dict(color="white", width=1.5),
            ),
            customdata=[[t["label"], t["meaning"]] for t in ups],
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "%{x|%b %d, %Y}<br>"
                "Price: $%{y:,.2f}<br>"
                "<i>%{customdata[1]}</i><extra></extra>"
            ),
        ))

    if downs:
        fig.add_trace(go.Scatter(
            x=[t["date"] for t in downs],
            y=[t["price"] for t in downs],
            mode="markers",
            name="▼ Crossed below MA",
            marker=dict(
                symbol="triangle-down", size=14,
                color="#ff1744",
                line=dict(color="white", width=1.5),
            ),
            customdata=[[t["label"], t["meaning"]] for t in downs],
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "%{x|%b %d, %Y}<br>"
                "Price: $%{y:,.2f}<br>"
                "<i>%{customdata[1]}</i><extra></extra>"
            ),
        ))


# Backwards-compat alias — callers may still import add_transition_arrows
def add_transition_arrows(fig: go.Figure, transitions: list[dict]):
    add_transition_markers(fig, transitions)


# ---------------------------------------------------------------------------
# "YOU ARE HERE" marker (single circle, hover details)
# ---------------------------------------------------------------------------

def add_you_are_here(fig: go.Figure, hist, stage: int, stage_name: str):
    """
    Add a single colored circle at the latest close marking the current
    stage. Details revealed on hover.
    """
    if hist is None or hist.empty:
        return
    last_dt = hist.index[-1]
    last_close = float(hist["Close"].iloc[-1])
    color = STAGE_COLOR.get(stage, "#4a90e2")
    icon = STAGE_ICON.get(stage, "?")

    fig.add_trace(go.Scatter(
        x=[last_dt], y=[last_close],
        mode="markers+text",
        name=f"📍 Stage {stage} · {stage_name}",
        marker=dict(
            symbol="circle",
            size=18,
            color=color,
            line=dict(color="white", width=2.5),
        ),
        text=[f"📍"],
        textposition="middle center",
        textfont=dict(size=14),
        hovertemplate=(
            f"<b>📍 Current: Stage {stage} {icon} {stage_name}</b><br>"
            f"%{{x|%b %d, %Y}}<br>"
            f"Price: $%{{y:,.2f}}<br>"
            f"<i>This is where the stock sits today.</i><extra></extra>"
        ),
    ))


# ---------------------------------------------------------------------------
# Trade setup lines — cleaner labels (single right-edge label per line)
# ---------------------------------------------------------------------------

def add_trade_setup_lines(fig: go.Figure, trade_setup):
    """
    Overlay Buy/Stop/Target horizontal lines from a TradeSetup.
    Labels are rendered as Scatter markers at the far right edge with
    hover details rather than stacked text annotations.
    """
    if trade_setup is None or not getattr(trade_setup, "applicable", False):
        return
    ts = trade_setup

    # Horizontal reference lines (no text annotations — cleaner)
    fig.add_hline(y=ts.entry_price, line_dash="solid",
                  line_color="#00c853", line_width=1.8, opacity=0.75)
    fig.add_hline(y=ts.stop_loss, line_dash="dot",
                  line_color="#ff1744", line_width=1.8, opacity=0.75)
    fig.add_hline(y=ts.target_1, line_dash="dashdot",
                  line_color="#00c853", line_width=1.2, opacity=0.6)


# ---------------------------------------------------------------------------
# Trade-setup hover legend (standalone pill outside the chart)
# ---------------------------------------------------------------------------

def trade_setup_legend_html(trade_setup) -> Optional[str]:
    """Return a small HTML pill summarizing the setup (rendered below chart)."""
    if trade_setup is None or not getattr(trade_setup, "applicable", False):
        return None
    ts = trade_setup
    return (
        f"<div style='display:flex;gap:8px;flex-wrap:wrap;margin-top:4px'>"
        f"<span style='background:rgba(0,200,83,0.12);color:#00c853;"
        f"padding:4px 10px;border-radius:6px;font-size:12px;font-weight:600'>"
        f"━━ {ts.entry_type} ${ts.entry_price:,.2f}</span>"
        f"<span style='background:rgba(255,23,68,0.12);color:#ff1744;"
        f"padding:4px 10px;border-radius:6px;font-size:12px;font-weight:600'>"
        f"···· Stop ${ts.stop_loss:,.2f}</span>"
        f"<span style='background:rgba(0,200,83,0.08);color:#00c853;"
        f"padding:4px 10px;border-radius:6px;font-size:12px;font-weight:600'>"
        f"─·─ Target ${ts.target_1:,.2f}</span>"
        f"<span style='background:#21262d;color:#e6edf3;"
        f"padding:4px 10px;border-radius:6px;font-size:12px'>"
        f"R/R <b>{ts.risk_reward_ratio:.2f}:1</b></span>"
        f"</div>"
    )
