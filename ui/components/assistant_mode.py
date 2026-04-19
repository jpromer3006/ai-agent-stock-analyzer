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

import streamlit as st

from data.watchlists import DEFAULT_NAME, list_watchlists, load_watchlist
from ml.batch_scanner import ScanReport, scan_universe
from ml.stage_analyzer import StageResult, analyze_stage

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


def _handle_show_ticker(ticker: str) -> str:
    """Analyze a single ticker and return a concise, honest summary."""
    r = analyze_stage(ticker)
    if r.error:
        return (
            f"I can't pull data for **{ticker}** right now — "
            f"yfinance returned: _{r.error}_. Double-check the ticker symbol "
            f"or try again in a moment."
        )

    icon = STAGE_ICON[r.stage]
    ts = r.trade_setup

    lines = [
        f"**{ticker}** — Stage {r.stage} {icon} {r.stage_name}  "
        f"(confidence {r.confidence:.0%})",
        "",
        f"- Last close: **${r.last_close:,.2f}** as of {r.as_of_date}",
        f"- vs 30-week MA: **{r.pct_above_ma:+.1%}**  (MA slope {r.ma_slope_pct:+.1%})",
        f"- Mansfield RS vs SPY: **{r.mansfield_rs:+.1f}**",
        f"- Bull probability: **{r.bull_probability:.0%}** → **{r.action}**",
    ]

    if ts and ts.applicable:
        lines.extend([
            "",
            "🎯 **Weinstein Trade Setup:**",
            f"- Entry ({ts.entry_type}): **${ts.entry_price:,.2f}**",
            f"- Stop-Loss (30W MA): **${ts.stop_loss:,.2f}**",
            f"- Target 1: **${ts.target_1:,.2f}**",
            f"- Risk/Reward: **{ts.risk_reward_ratio:.2f}:1**",
            "",
            "⚠ Technical signal, not a prediction. Size to the stop-loss distance.",
        ])
    else:
        lines.extend([
            "",
            f"📭 No clean Weinstein setup right now — {r.stage_name} stage.",
            "Wait for a clean Stage 2 breakout or Stage 4 breakdown.",
        ])

    return "\n".join(lines)


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

        if intent == "greet":
            response = _handle_greet()
        elif intent == "market_status":
            response = _handle_market_status(report)
        elif intent == "find_n":
            response = _handle_find_n(report, params["n"], params["direction"])
        elif intent == "show_ticker":
            response = _handle_show_ticker(params["ticker"])
        elif intent == "explain_stage":
            response = _handle_explain_stage(params["stage"])
        else:
            response = _handle_unknown_via_llm(user_input, report)

        st.session_state.assistant_messages.append({
            "role": "assistant",
            "content": response,
        })
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
