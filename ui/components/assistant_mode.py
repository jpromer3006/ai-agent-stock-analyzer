"""
assistant_mode.py — Conversational chatbot entry point.

Opens with the greeting the user specified:
    "Hello, how are you doing today. How many excellent stocks can I bring you?"

While the greeting renders, a brief market summary is displayed alongside
(latest scan timestamp, stage distribution, Stage 2 leaderboard preview).

Chat engine is **hybrid**:
    - Template intents (fast, free, honest): "find N stocks", "show TICKER",
      "explain stage", "what's the market doing", etc.
    - Claude fallback (for open-ended or novel questions)

Ethical guardrails:
    - Never invent tickers
    - If no Stage 2 stocks exist, say so with light humor:
      "The ship is hitting some turbulence — only X Stage 2 setups today."
    - Always cite data timestamp
    - Refuse to give price targets without also showing the stop-loss
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

import plotly.graph_objects as go
import streamlit as st

from data.watchlists import DEFAULT_NAME, list_watchlists, load_watchlist
from ml.batch_scanner import ScanReport, scan_universe
from ml.stage_analyzer import MA_WINDOW_DAYS, StageResult, analyze_stage

_PROJECT_ROOT = Path(__file__).parent.parent.parent


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
GREETING = (
    "Hello, how are you doing today. "
    "How many excellent stocks can I bring you?"
)

NO_RESULTS_ACKNOWLEDGEMENTS = [
    "You know something, the ship is hitting some turbulence right now — ",
    "Honest answer: ",
    "I'd rather tell you the truth than make something up — ",
]

STAGE_ICON = {1: "◯", 2: "✓", 3: "⚠", 4: "✗"}
STAGE_COLOR = {1: "#6c757d", 2: "#00c853", 3: "#ffc107", 4: "#ff1744"}


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

def _init_session():
    if "assistant_messages" not in st.session_state:
        st.session_state.assistant_messages = [
            {"role": "assistant", "content": GREETING}
        ]
    if "assistant_scan" not in st.session_state:
        st.session_state.assistant_scan = None


# ---------------------------------------------------------------------------
# Market brief (templated, always honest)
# ---------------------------------------------------------------------------

def _run_market_scan() -> ScanReport:
    """Run a scan on the default watchlist. Cached in session."""
    if st.session_state.assistant_scan is not None:
        return st.session_state.assistant_scan

    tickers = load_watchlist(DEFAULT_NAME)
    report = scan_universe(tickers, max_workers=10)
    st.session_state.assistant_scan = report
    return report


def _render_market_brief(report: ScanReport):
    """Compact summary of current market stage distribution."""
    buckets = report.stage_buckets
    stage2 = len(buckets[2])
    stage4 = len(buckets[4])
    total = sum(len(b) for b in buckets.values())
    as_of = datetime.fromisoformat(report.as_of.replace("Z", "")).strftime("%b %d, %Y · %H:%M UTC")

    st.markdown(
        f"<div style='padding:12px;background:#16213e;border-radius:8px;"
        f"border-left:4px solid #4a90e2'>"
        f"<div style='font-size:12px;color:#8b949e'>📊 MARKET BRIEF  "
        f"· as of {as_of} · {total} tickers scanned</div>"
        f"<div style='margin-top:8px;font-size:14px;color:#e6edf3'>"
        f"<span style='color:{STAGE_COLOR[2]};font-weight:600'>"
        f"{stage2} Stage 2 (BUY zone)</span>  ·  "
        f"<span style='color:{STAGE_COLOR[3]};font-weight:600'>"
        f"{len(buckets[3])} Stage 3 (topping)</span>  ·  "
        f"<span style='color:{STAGE_COLOR[4]};font-weight:600'>"
        f"{stage4} Stage 4 (SELL zone)</span>"
        f"</div></div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Intent detection (templates first)
# ---------------------------------------------------------------------------

def _detect_intent(message: str) -> tuple[str, dict]:
    """
    Return (intent, params) for a user message.
    Intents:
        "find_n"        : find N stocks  (params: {"n": int, "direction": "buy"/"sell"})
        "show_ticker"   : show / analyze ticker  (params: {"ticker": str})
        "market_status" : how's the market / what's going on
        "explain_stage" : what is Stage N
        "greet"         : hello / hi / thanks
        "unknown"       : fallback to LLM
    """
    msg = message.strip().lower()

    # Greeting
    if re.match(r"^(hi|hello|hey|thanks|thank you|good (morning|afternoon|evening))\b", msg):
        return "greet", {}

    # Market status
    if re.search(r"\b(market|how('?s| is) (it|the market)|what('?s| is) going on|overview)\b", msg):
        return "market_status", {}

    # "Find me N stocks / strongest / best buys"
    m = re.search(r"\b(?:find|show|give|get|bring)\s*(?:me|us)?\s*(\d+)\b", msg)
    if m and re.search(r"\b(stock|tick|buy|bull|strong|best|top|sell|bear|short|weak|excellent)", msg):
        n = int(m.group(1))
        direction = "sell" if re.search(r"\b(sell|bear|short|weak)", msg) else "buy"
        return "find_n", {"n": n, "direction": direction}

    # "Show me NVDA" or "analyze NVDA"
    m = re.search(r"\b(?:show|analyze|check|look at|whats?|tell me about)\s+(?:me\s+)?([A-Z]{1,5})\b", message)
    if m:
        return "show_ticker", {"ticker": m.group(1).upper()}
    # Bare ticker
    m = re.match(r"^([A-Z]{1,5})(\s+\?|\?|$)", message.strip())
    if m:
        return "show_ticker", {"ticker": m.group(1).upper()}

    # Explain stage
    m = re.search(r"\bstage\s*([1-4])\b", msg)
    if m:
        return "explain_stage", {"stage": int(m.group(1))}

    return "unknown", {}


# ---------------------------------------------------------------------------
# Intent handlers (templated, honest, timestamped)
# ---------------------------------------------------------------------------

def _handle_greet() -> str:
    return (
        "Great to see you. Tell me what you're looking for — "
        "I can find Stage 2 breakouts, explain a ticker, or give you a "
        "read on the overall market. What'll it be?"
    )


def _handle_market_status(report: ScanReport) -> str:
    buckets = report.stage_buckets
    s2 = len(buckets[2])
    s4 = len(buckets[4])
    total = sum(len(b) for b in buckets.values())
    as_of = datetime.fromisoformat(report.as_of.replace("Z", "")).strftime("%b %d %Y · %H:%M UTC")

    lines = [
        f"**Market Brief** (as of {as_of}):",
        f"- Scanned **{total}** tickers from the default watchlist",
        f"- **{s2}** in Stage 2 (Advancing — BUY zone) → "
        f"{', '.join(r.ticker for r in buckets[2][:5])}"
        + (" ..." if s2 > 5 else ""),
        f"- **{s4}** in Stage 4 (Declining — SELL zone) → "
        f"{', '.join(r.ticker for r in buckets[4][:5])}"
        + (" ..." if s4 > 5 else ""),
        f"- **{len(buckets[3])}** in Stage 3 (topping) — watch for transitions",
        f"- **{len(buckets[1])}** in Stage 1 (basing) — watchlist candidates",
        "",
        "Want me to pull up the top Stage 2 setups with entry/stop/target levels?",
    ]
    return "\n".join(lines)


def _handle_find_n(report: ScanReport, n: int, direction: str) -> str:
    """Find N best buys (or sells). Humorous if none exist."""
    if direction == "buy":
        pool = report.stage2_breakouts
        zone = "Stage 2 BUY"
    else:
        pool = report.stage4_breakdowns
        zone = "Stage 4 SELL/SHORT"

    if n > 50:
        return (
            f"Easy there — even the market doesn't have 50+ clean setups most days. "
            f"How about I show you the top 10 {zone} setups and you pick from there?"
        )

    if not pool:
        return (
            f"You know something, the ship is hitting some turbulence right now — "
            f"there are **zero** {zone} setups in your watchlist as of this scan. "
            f"That's a signal in itself: it means the market isn't giving you the "
            f"Weinstein-clean setup you want. "
            f"\n\nOptions:\n"
            f"- Widen the net: add more tickers to the watchlist\n"
            f"- Wait for a transition (check back after the next trading day)\n"
            f"- Review Stage 1 basing candidates — they could become Stage 2 soon"
        )

    actual = min(n, len(pool))
    if actual < n:
        prefix = (
            f"Honest answer: your watchlist only has **{actual}** clean {zone} "
            f"setups right now (you asked for {n}). Here's what I've got:\n\n"
        )
    else:
        prefix = f"Here are the top **{actual}** {zone} setups:\n\n"

    lines = [prefix]
    for i, r in enumerate(pool[:actual], 1):
        ts = r.trade_setup
        if ts and ts.applicable:
            lines.append(
                f"**{i}. {r.ticker}** · Bull Prob {r.bull_probability:.0%} · "
                f"{r.action}  \n"
                f"  Entry ${ts.entry_price:,.2f} · Stop ${ts.stop_loss:,.2f} · "
                f"Target ${ts.target_1:,.2f} · R/R {ts.risk_reward_ratio:.2f}:1  "
            )
        else:
            lines.append(
                f"**{i}. {r.ticker}** · Bull Prob {r.bull_probability:.0%} · "
                f"{r.action} (no clean setup yet)  "
            )

    lines.append(
        "\nClick any ticker or type `show TICKER` for the full chart with "
        "entry/stop/target overlaid."
    )
    return "\n".join(lines)


def _generate_why(r: StageResult) -> list[str]:
    """
    Plain-English reasoning for the stage classification.
    Three to four 'because' bullets a non-expert can follow.
    """
    reasons: list[str] = []
    pct_above = r.pct_above_ma
    slope = r.ma_slope_pct
    rs = r.mansfield_rs

    # Reason 1: price vs MA
    if pct_above > 0.05:
        reasons.append(
            f"**Price is {pct_above:+.0%} above the 30-week moving average** "
            f"— Weinstein's long-term trend line. When price is well above this "
            f"line, the big-picture trend is up."
        )
    elif pct_above > 0:
        reasons.append(
            f"**Price is just {pct_above:+.1%} above the 30-week MA** — barely "
            f"hanging onto the long-term trend. This is a neutral-to-cautious zone."
        )
    elif pct_above > -0.05:
        reasons.append(
            f"**Price is {abs(pct_above):.1%} below the 30-week MA** — the stock "
            f"has lost its long-term uptrend. Weinstein calls this a warning sign."
        )
    else:
        reasons.append(
            f"**Price is {abs(pct_above):.0%} below the 30-week MA** — the long-term "
            f"trend is firmly down. Weinstein's rule: never hold a stock this "
            f"far below its 30-week line, no matter how good the fundamentals look."
        )

    # Reason 2: MA slope
    if slope > 0.02:
        reasons.append(
            f"**The 30-week MA is rising steadily** ({slope:+.1%} over the last "
            f"month). This is what Weinstein calls a healthy uptrend — the trend "
            f"line itself is sloping up."
        )
    elif slope > 0:
        reasons.append(
            f"**The 30-week MA is rising very slowly** ({slope:+.1%}/month) — "
            f"momentum is fading. Weinstein warns this often precedes a Stage 3 top."
        )
    elif slope > -0.02:
        reasons.append(
            f"**The 30-week MA has flattened** ({slope:+.1%}/month) — the trend "
            f"has stalled. This is the transition zone where smart money exits."
        )
    else:
        reasons.append(
            f"**The 30-week MA is actively falling** ({slope:+.1%}/month) — the "
            f"long-term trend line is now sloping down. This is a Stage 4 signature."
        )

    # Reason 3: Relative strength
    if rs > 10:
        reasons.append(
            f"**Relative strength vs SPY is {rs:+.1f}** — the stock is strongly "
            f"outperforming the S&P 500. Weinstein: always favor leaders, not laggards."
        )
    elif rs > 0:
        reasons.append(
            f"**Relative strength vs SPY is {rs:+.1f}** — slightly outperforming "
            f"the market, but not a leader. Look for stocks with RS > +10."
        )
    elif rs > -10:
        reasons.append(
            f"**Relative strength vs SPY is {rs:+.1f}** — underperforming the "
            f"market. Weinstein avoided these on the long side entirely."
        )
    else:
        reasons.append(
            f"**Relative strength vs SPY is {rs:+.1f}** — seriously lagging the "
            f"market. In Weinstein's framework, this is a classic short candidate."
        )

    # Reason 4: volume (only if meaningful)
    if r.volume_surge >= 1.5:
        reasons.append(
            f"**Volume is {r.volume_surge:.1f}× the 50-day average** — heavier "
            f"volume confirms whatever direction the price is moving. High volume "
            f"on a breakout = stronger signal."
        )
    elif r.volume_surge < 0.7:
        reasons.append(
            f"**Volume is only {r.volume_surge:.1f}× the 50-day average** — "
            f"participation is light. Weinstein warns: breakouts on low volume "
            f"often fail."
        )

    return reasons


def _handle_show_ticker(ticker: str) -> tuple[str, Optional[StageResult]]:
    """
    Analyze a single ticker and return (narrative_text, StageResult).
    The StageResult triggers inline chart rendering with arrow annotations.
    """
    r = analyze_stage(ticker)
    if r.error:
        return (
            f"I can't pull data for **{ticker}** right now — "
            f"yfinance returned: _{r.error}_. Double-check the ticker symbol "
            f"or try again in a moment."
        ), None

    icon = STAGE_ICON[r.stage]
    ts = r.trade_setup

    # Stage-specific headline in plain English
    stage_headline = {
        1: (f"**{ticker} is quiet right now** — it's in Stage 1 (Basing). "
            f"The stock isn't trending up or down in a meaningful way. "
            f"Weinstein's advice: watch, don't trade."),
        2: (f"**{ticker} is in an uptrend** — Stage 2 (Advancing). "
            f"This is Weinstein's BUY zone: price is above a rising 30-week MA."),
        3: (f"**{ticker} is getting tired** — Stage 3 (Topping). "
            f"The stock was in a nice uptrend but the momentum is fading. "
            f"Weinstein says trim here, don't add."),
        4: (f"**{ticker} is in a downtrend** — Stage 4 (Declining). "
            f"Weinstein's rule: never buy a Stage 4 stock, no matter how cheap "
            f"it looks. The trend is your enemy."),
    }

    lines = [
        stage_headline.get(r.stage, f"**{ticker}** — Stage {r.stage} {icon} {r.stage_name}"),
        "",
        f"**The quick read (confidence {r.confidence:.0%}):**",
        f"- Last close: **${r.last_close:,.2f}** as of {r.as_of_date}",
        f"- Bull probability: **{r.bull_probability:.0%}** → recommended action: **{r.action}**",
        "",
        "**Why? Three things are driving this:**",
    ]

    for reason in _generate_why(r)[:4]:  # cap at 4 reasons
        lines.append(f"- {reason}")

    if ts and ts.applicable:
        lines.extend([
            "",
            "🎯 **If you want to trade this, here's the Weinstein setup:**",
            f"- **Entry** ({ts.entry_type}): place order at **${ts.entry_price:,.2f}**",
            f"- **Stop-Loss**: exit at **${ts.stop_loss:,.2f}** "
            f"(the 30-week MA — if price closes below, the trade is invalidated)",
            f"- **First Target**: **${ts.target_1:,.2f}** (measured move from base)",
            f"- **Risk/Reward**: **{ts.risk_reward_ratio:.2f}:1**  — "
            f"you'd risk ${ts.risk_per_share:.2f} per share to make ${ts.reward_per_share:.2f}",
            "",
            "See the chart below — I've drawn the entry line (green), stop-loss "
            "(red), and target (green dashed) so you can visualize the trade.",
            "",
            "⚠ This is a technical signal, not a prediction. Size positions to the "
            "stop-loss distance, not to the entry price.",
        ])
    else:
        lines.extend([
            "",
            "📭 **No clean Weinstein setup right now.** The chart below shows "
            "why — the stage isn't suitable for a mechanical entry. Wait for a "
            "Stage 2 breakout (BUY) or Stage 4 breakdown (SHORT) with a confirmed "
            "30-week MA direction.",
        ])

    return "\n".join(lines), r


# ---------------------------------------------------------------------------
# Inline chart with arrow annotations (the "one glance" visual)
# ---------------------------------------------------------------------------

def _detect_stage_transitions(hist: "pd.DataFrame") -> list[dict]:
    """
    Scan history and mark likely Stage transitions:
      - Price crosses above MA (Stage 1→2)
      - Price crosses below MA (Stage 2/3→4)
      - MA slope flip (up→down)
    Returns list of {date, price, label, color}.
    """
    import pandas as pd
    transitions: list[dict] = []
    close = hist["Close"]
    ma = hist["MA30W"]
    if ma.isna().all():
        return transitions

    # Price vs MA cross detection
    above = (close > ma).astype(int)
    cross = above.diff().fillna(0)
    last_year_cutoff = hist.index[-min(252, len(hist))]

    for dt, val in cross.items():
        if dt < last_year_cutoff:
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

    # Dedupe close transitions (keep one per 2-week window)
    deduped: list[dict] = []
    for t in transitions:
        if deduped and (t["date"] - deduped[-1]["date"]).days < 14:
            continue
        deduped.append(t)
    return deduped[-4:]  # keep only the 4 most recent


def _render_chat_chart(ticker: str, result: Optional[StageResult] = None):
    """
    Render an inline chart inside a chat message:
      - Price + 30-week MA
      - Stage transition arrows in the last 12 months
      - Current price labeled "YOU ARE HERE — Stage X"
      - Entry/Stop/Target lines if a trade setup is applicable
    """
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="2y", auto_adjust=True)
        if hist is None or hist.empty:
            st.caption("No chart data available for this ticker.")
            return
    except Exception as exc:
        st.caption(f"Couldn't load chart: {exc}")
        return

    import pandas as pd
    hist["MA30W"] = hist["Close"].rolling(MA_WINDOW_DAYS, min_periods=MA_WINDOW_DAYS).mean()

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=hist.index, y=hist["Close"], mode="lines",
        name="Price", line=dict(color="#4a90e2", width=2),
    ))
    fig.add_trace(go.Scatter(
        x=hist.index, y=hist["MA30W"], mode="lines",
        name="30-week MA", line=dict(color="#ff9800", width=2, dash="dash"),
    ))

    # Stage transition arrows (last 12 months)
    transitions = _detect_stage_transitions(hist)
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

    # "YOU ARE HERE" marker at the latest close
    last_dt = hist.index[-1]
    last_close = float(hist["Close"].iloc[-1])
    here_label = "📍 YOU ARE HERE"
    if result:
        here_label = f"📍 Stage {result.stage} {STAGE_ICON[result.stage]} {result.stage_name}"
    fig.add_annotation(
        x=last_dt, y=last_close,
        text=f"<b>{here_label}</b><br>${last_close:,.2f}",
        showarrow=True,
        arrowhead=2, arrowsize=1.5, arrowwidth=3,
        arrowcolor=STAGE_COLOR[result.stage] if result else "#4a90e2",
        ax=-80, ay=-60,
        font=dict(color="#e6edf3", size=12),
        bgcolor=STAGE_COLOR[result.stage] if result else "#4a90e2",
        bordercolor="white", borderwidth=1, borderpad=6,
    )

    # Trade setup overlay lines (if applicable)
    if result and result.trade_setup and result.trade_setup.applicable:
        ts = result.trade_setup
        fig.add_hline(
            y=ts.entry_price, line_dash="solid", line_color="#00c853", line_width=2,
            annotation_text=f"  Entry ${ts.entry_price:,.2f}",
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

    fig.update_layout(
        title=f"{ticker} — Weinstein view (last 2 years)",
        height=430,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#e6edf3",
        xaxis=dict(showgrid=False, fixedrange=True),
        yaxis=dict(showgrid=True, gridcolor="#30363d", fixedrange=True,
                   title="Price ($)"),
        dragmode=False,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(r=130, t=60, b=40),
    )
    st.plotly_chart(fig, use_container_width=True,
                    config={"scrollZoom": False, "displayModeBar": False})


def _handle_explain_stage(stage: int) -> str:
    explanations = {
        1: (
            "**Stage 1 — Basing**  \n"
            "Price consolidates around a flat 30-week moving average. "
            "Volume dries up. This is the accumulation phase — patient money "
            "is quietly building positions. No edge yet for a trader. "
            "Watch for a Stage 2 breakout."
        ),
        2: (
            "**Stage 2 — Advancing**  \n"
            "Price breaks above a rising 30-week MA, ideally on heavy volume "
            "with positive relative strength vs SPY. **This is Weinstein's "
            "BUY zone.** Entry via buy-stop just above the pivot; stop-loss at "
            "the 30-week MA; trail the stop as the MA rises. Hold until Stage 3."
        ),
        3: (
            "**Stage 3 — Topping**  \n"
            "Price still above the 30-week MA, but the MA has flattened. "
            "Volume starts clustering on down days. Momentum fades. "
            "Take profits on partial positions; tighten stops. Avoid new longs."
        ),
        4: (
            "**Stage 4 — Declining**  \n"
            "Price below a falling 30-week MA with negative relative strength. "
            "This is the SELL/SHORT zone. Entry via sell-stop below recent "
            "pivot low; stop-loss back above the 30-week MA. Weinstein's rule: "
            "**never hold a Stage 4 stock long, even if you love the company.**"
        ),
    }
    return explanations.get(stage, "Stage must be 1, 2, 3, or 4.")


# ---------------------------------------------------------------------------
# LLM fallback (for open-ended questions)
# ---------------------------------------------------------------------------

_LLM_SYSTEM_PROMPT = """\
You are JAIA, a concise, honest stock market assistant. You use Stan Weinstein's \
stage analysis methodology (Secrets for Profiting in Bull and Bear Markets).

ETHICAL RULES:
- Never invent tickers, prices, or financial data
- When uncertain, say so plainly
- Light humor welcome; aggressive upsell is not
- Always pair a buy recommendation with a stop-loss level
- Use phrases like "as of the latest scan" to acknowledge data freshness
- If the user asks for more than the data supports, decline gracefully: \
  "The ship is hitting some turbulence" style responses are welcome.

RESPONSE STYLE:
- 2-4 sentences maximum unless user asks for more
- Markdown bold for key numbers
- End with a concrete next action the user can take
"""


def _get_anthropic_key() -> Optional[str]:
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", "ANTHROPIC_API_KEY", "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    try:
        from dotenv import load_dotenv
        load_dotenv(_PROJECT_ROOT / ".env")
    except Exception:
        pass
    return os.environ.get("ANTHROPIC_API_KEY", "").strip() or None


def _handle_unknown_via_llm(message: str, report: ScanReport) -> str:
    """Fallback: send the question to Claude with market context."""
    api_key = _get_anthropic_key()
    if not api_key:
        return (
            "I don't have an LLM connection right now, so I can only handle "
            "specific commands. Try:\n"
            "- `find me 3 strong buys`\n"
            "- `show NVDA`\n"
            "- `what's the market doing`\n"
            "- `explain stage 2`"
        )

    try:
        import anthropic
    except ImportError:
        return "anthropic SDK not installed."

    # Build compact market context
    buckets = report.stage_buckets
    top_bull = ", ".join(f"{r.ticker}({r.bull_probability:.0%})"
                          for r in report.stage2_breakouts[:5])
    top_bear = ", ".join(f"{r.ticker}({r.bull_probability:.0%})"
                          for r in report.stage4_breakdowns[:5])
    ctx = (
        f"Current market scan (as of {report.as_of}):\n"
        f"- {len(buckets[2])} Stage 2 (BUY): {top_bull or 'none'}\n"
        f"- {len(buckets[4])} Stage 4 (SELL): {top_bear or 'none'}\n"
        f"- {len(buckets[3])} Stage 3 topping\n"
        f"- {len(buckets[1])} Stage 1 basing\n"
    )

    client = anthropic.Anthropic(api_key=api_key)
    try:
        resp = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            system=_LLM_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": f"Market context:\n{ctx}\n\nUser question: {message}",
            }],
        )
        return resp.content[0].text.strip()
    except Exception as exc:
        return f"I ran into an API issue: {exc}. Try a more specific command."


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------

def render_assistant_mode():
    """Top-level renderer for Assistant Mode."""
    _init_session()

    st.markdown("## 💬 Assistant Mode — talk to your stock analyst")
    st.caption(
        "Ask in plain English. I'll run Weinstein Stage Analysis on the fly "
        "and always cite the data timestamp. No hallucinations."
    )

    # Auto-run scan on first load (cached in session)
    with st.spinner("Running morning scan..."):
        report = _run_market_scan()

    # Market brief (always visible at top)
    _render_market_brief(report)

    # Quick-action chips
    st.markdown("")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        if st.button("📊 Market status", use_container_width=True):
            st.session_state.assistant_messages.append({"role": "user", "content": "what's the market doing"})
            st.session_state.assistant_messages.append({"role": "assistant", "content": _handle_market_status(report)})
            st.rerun()
    with c2:
        if st.button("🟢 Find 3 buys", use_container_width=True):
            st.session_state.assistant_messages.append({"role": "user", "content": "find me 3 strong buys"})
            st.session_state.assistant_messages.append({"role": "assistant", "content": _handle_find_n(report, 3, "buy")})
            st.rerun()
    with c3:
        if st.button("🔴 Find 3 sells", use_container_width=True):
            st.session_state.assistant_messages.append({"role": "user", "content": "find me 3 sells"})
            st.session_state.assistant_messages.append({"role": "assistant", "content": _handle_find_n(report, 3, "sell")})
            st.rerun()
    with c4:
        if st.button("📚 Explain Stage 2", use_container_width=True):
            st.session_state.assistant_messages.append({"role": "user", "content": "explain stage 2"})
            st.session_state.assistant_messages.append({"role": "assistant", "content": _handle_explain_stage(2)})
            st.rerun()

    st.markdown("---")

    # Render chat history
    for msg in st.session_state.assistant_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            # Render inline chart if this message is attached to a ticker
            if msg.get("chart_ticker"):
                _render_chat_chart(
                    msg["chart_ticker"],
                    result=msg.get("stage_result"),
                )

    # Chat input
    user_input = st.chat_input("e.g. 'find me 5 excellent stocks' or 'show NVDA'")
    if user_input:
        # Append user message
        st.session_state.assistant_messages.append({
            "role": "user",
            "content": user_input,
        })

        # Dispatch to intent handler
        intent, params = _detect_intent(user_input)

        chart_ticker: Optional[str] = None
        stage_result: Optional[StageResult] = None

        if intent == "greet":
            response = _handle_greet()
        elif intent == "market_status":
            response = _handle_market_status(report)
        elif intent == "find_n":
            response = _handle_find_n(report, params["n"], params["direction"])
        elif intent == "show_ticker":
            response, stage_result = _handle_show_ticker(params["ticker"])
            if stage_result:
                chart_ticker = params["ticker"]
        elif intent == "explain_stage":
            response = _handle_explain_stage(params["stage"])
        else:
            response = _handle_unknown_via_llm(user_input, report)

        msg_payload = {"role": "assistant", "content": response}
        if chart_ticker:
            msg_payload["chart_ticker"] = chart_ticker
            msg_payload["stage_result"] = stage_result
        st.session_state.assistant_messages.append(msg_payload)
        st.rerun()

    # Sidebar: reset + re-scan
    with st.sidebar:
        st.markdown("### 💬 Assistant Mode")
        st.caption("Weinstein chatbot · live scan")
        if st.button("🔄 Re-run market scan", use_container_width=True):
            st.session_state.assistant_scan = None
            st.rerun()
        if st.button("🗑 Clear chat", use_container_width=True):
            st.session_state.assistant_messages = [
                {"role": "assistant", "content": GREETING}
            ]
            st.rerun()
