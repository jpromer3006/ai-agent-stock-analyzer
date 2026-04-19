"""
chat_panel.py — Reusable chat panel with optional audio playback.

Used inside Research Mode to let the user ask follow-up questions
about the current ticker's memo and/or the latest Trader scan.

Context-aware: the chat has access to (a) the current memo,
(b) the ticker being researched, and (c) the latest Trader scan
from session state. This is what the old Assistant Mode was missing.

Hybrid engine:
    - Templates for fast intents: show TICKER, market status,
      find N buys, explain stage
    - Claude fallback for open-ended questions (uses current memo
      as context so answers are grounded in what the user just read)
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, Optional

import streamlit as st

from data.audio_client import generate_audio, is_available as audio_is_available
from ml.stage_analyzer import StageResult, analyze_stage


_PROJECT_ROOT = Path(__file__).parent.parent.parent


# ---------------------------------------------------------------------------
# Session state keys (scoped so multiple chat panels can coexist)
# ---------------------------------------------------------------------------

def _msg_key(panel_id: str) -> str:
    return f"chat_messages__{panel_id}"


# ---------------------------------------------------------------------------
# API key loader
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Context builder — injects memo + Trader scan state so chat is "smart"
# ---------------------------------------------------------------------------

def _build_context_block(
    current_ticker: Optional[str] = None,
    current_memo: Optional[str] = None,
) -> str:
    """
    Build a context string that includes:
      - Current memo if available (truncated)
      - Latest Trader scan summary if available
    """
    parts: list[str] = []

    if current_ticker and current_memo:
        # Truncate memo to ~1500 chars to fit in context
        memo_snippet = current_memo[:1500]
        if len(current_memo) > 1500:
            memo_snippet += "\n\n[...memo truncated; full text available in the UI...]"
        parts.append(
            f"The user is currently researching **{current_ticker}**. "
            f"Here is the research memo they just received:\n\n"
            f"```\n{memo_snippet}\n```\n"
        )

    # Look for latest Trader scan across possible session keys
    scan_report = None
    for k, v in st.session_state.items():
        if k.startswith("scan_") or k == "assistant_scan":
            if hasattr(v, "results") and hasattr(v, "stage_buckets"):
                scan_report = v
                break

    if scan_report is not None:
        buckets = scan_report.stage_buckets
        top_bull = [
            f"{r.ticker}({r.bull_probability:.0%})"
            for r in scan_report.stage2_breakouts[:5]
        ]
        top_bear = [
            f"{r.ticker}({r.bull_probability:.0%})"
            for r in scan_report.stage4_breakdowns[:5]
        ]
        parts.append(
            f"Latest Trader scan (as of {scan_report.as_of}):\n"
            f"- Stage 2 BUY zone ({len(buckets[2])} total): {', '.join(top_bull) or 'none'}\n"
            f"- Stage 4 SELL zone ({len(buckets[4])} total): {', '.join(top_bear) or 'none'}\n"
            f"- Stage 3 topping: {len(buckets[3])} · Stage 1 basing: {len(buckets[1])}\n"
        )

    if not parts:
        return "(No ticker is being researched yet and no Trader scan has been run.)"
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are JAIA, a concise, honest stock research assistant grounded in Stan \
Weinstein's Stage Analysis methodology (from "Secrets for Profiting in Bull and \
Bear Markets").

ETHICAL RULES (non-negotiable):
- Never invent tickers, prices, or figures
- If the memo or scan doesn't contain information, say so plainly
- Light humor is welcome; aggressive upsell is not
- Pair every buy recommendation with a stop-loss level
- When asked for more than the data supports: "The ship is hitting some \
  turbulence" style honest responses are preferred to hallucinations

CONTEXT RULES:
- You have access to the user's CURRENT MEMO (if any) and the LATEST TRADER \
  SCAN (if any). Reference them directly.
- If the user asks a question that isn't answerable from context, say so and \
  suggest what to do (e.g., "Run a ticker in Research Mode first").

RESPONSE STYLE:
- 2-5 sentences unless the user explicitly asks for more detail
- Use markdown **bold** for the key number or ticker
- End with a concrete next action
"""


# ---------------------------------------------------------------------------
# Intent detection (templates first, fast)
# ---------------------------------------------------------------------------

def _detect_intent(message: str) -> tuple[str, dict]:
    msg = message.strip().lower()

    # Explain stage (cheap template)
    m = re.search(r"\bstage\s*([1-4])\b", msg)
    if m and re.search(r"\b(what|explain|mean|is|tell)", msg):
        return "explain_stage", {"stage": int(m.group(1))}

    # Show a specific ticker
    m = re.search(r"\b(?:show|analyze|check|look at|tell me about)\s+(?:me\s+)?([A-Z]{1,5})\b", message)
    if m:
        return "show_ticker", {"ticker": m.group(1).upper()}

    # Bare ticker like "NVDA?"
    m = re.match(r"^([A-Z]{1,5})(\s|\?|$)", message.strip())
    if m:
        return "show_ticker", {"ticker": m.group(1).upper()}

    return "llm", {}


# ---------------------------------------------------------------------------
# Template handlers
# ---------------------------------------------------------------------------

_STAGE_EXPLANATIONS = {
    1: "**Stage 1 — Basing.** Price consolidates around a flat 30-week MA. "
       "Accumulation phase. No edge yet. Watch, don't trade.",
    2: "**Stage 2 — Advancing.** Price breaks above a rising 30-week MA on "
       "heavy volume with positive RS. This is Weinstein's BUY zone. Entry "
       "via buy-stop above pivot; stop at the MA.",
    3: "**Stage 3 — Topping.** MA flattens after a run. Volume clusters on "
       "down days. Take profits; avoid new longs.",
    4: "**Stage 4 — Declining.** Price below a falling MA with negative RS. "
       "SHORT zone. Weinstein's rule: never hold a Stage 4 stock, even if "
       "you love the company.",
}


def _handle_explain_stage(stage: int) -> str:
    return _STAGE_EXPLANATIONS.get(stage, "Stage must be 1-4.")


def _handle_show_ticker(ticker: str) -> str:
    """Quick Weinstein stage summary for a ticker (no memo)."""
    r = analyze_stage(ticker)
    if r.error:
        return (
            f"I can't pull data for **{ticker}** right now — yfinance returned: "
            f"_{r.error}_. Check the symbol and try again."
        )
    ts = r.trade_setup
    icon = {1: "◯", 2: "✓", 3: "⚠", 4: "✗"}[r.stage]
    lines = [
        f"**{ticker}** is Stage {r.stage} {icon} **{r.stage_name}** "
        f"(confidence {r.confidence:.0%}).",
        f"- Price **${r.last_close:,.2f}** · vs 30W MA **{r.pct_above_ma:+.1%}** · "
        f"Bull Prob **{r.bull_probability:.0%}** → **{r.action}**",
    ]
    if ts and ts.applicable:
        lines.append(
            f"- Setup: {ts.entry_type} at **${ts.entry_price:,.2f}**, "
            f"stop **${ts.stop_loss:,.2f}**, target **${ts.target_1:,.2f}** "
            f"(R/R {ts.risk_reward_ratio:.2f}:1)"
        )
    else:
        lines.append("- No clean setup right now (not Stage 2 or 4).")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# LLM fallback
# ---------------------------------------------------------------------------

def _handle_llm(
    user_message: str,
    current_ticker: Optional[str],
    current_memo: Optional[str],
) -> str:
    api_key = _get_anthropic_key()
    if not api_key:
        return (
            "I don't have an LLM connection right now. Try:\n"
            "- `show NVDA` — quick Weinstein stage summary\n"
            "- `what is stage 2` — explanation\n"
            "- Or run a ticker in the Analyze box above for a full memo."
        )

    try:
        import anthropic
    except ImportError:
        return "anthropic SDK not installed."

    context = _build_context_block(current_ticker, current_memo)
    user_msg = f"CONTEXT:\n{context}\n\nUSER QUESTION: {user_message}"

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=400,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        return resp.content[0].text.strip()
    except Exception as exc:
        return f"LLM call failed: {exc}. Try a more specific command like `show NVDA`."


# ---------------------------------------------------------------------------
# Public render
# ---------------------------------------------------------------------------

def render_chat_panel(
    panel_id: str,
    *,
    title: str = "💬 Ask about this analysis",
    caption: str = "",
    current_ticker: Optional[str] = None,
    current_memo: Optional[str] = None,
    placeholder: str = "Ask a follow-up, e.g. 'is this a good time to buy?'",
    enable_audio: bool = True,
):
    """
    Render a chat panel inside the current Streamlit container.

    Parameters
    ----------
    panel_id : str
        Unique identifier — used to scope session state so multiple panels
        can coexist (e.g. if we add Trader-mode chat later).
    current_ticker, current_memo : context for the LLM to reference
    enable_audio : if True, show 🔊 Listen buttons on assistant messages
    """
    msgs_key = _msg_key(panel_id)
    if msgs_key not in st.session_state:
        st.session_state[msgs_key] = []

    st.markdown(f"### {title}")
    if caption:
        st.caption(caption)

    audio_enabled = enable_audio and audio_is_available()

    # Render chat history
    for i, msg in enumerate(st.session_state[msgs_key]):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if (msg["role"] == "assistant" and audio_enabled
                    and len(msg["content"]) > 80):
                if msg.get("audio_path"):
                    try:
                        with open(msg["audio_path"], "rb") as f:
                            st.audio(f.read(), format="audio/mpeg")
                    except Exception:
                        pass
                else:
                    if st.button("🔊 Listen", key=f"{panel_id}_play_{i}"):
                        with st.spinner("Generating audio..."):
                            path = generate_audio(msg["content"])
                        if path:
                            msg["audio_path"] = str(path)
                            st.rerun()

    # Chat input
    user_input = st.chat_input(placeholder, key=f"{panel_id}_chat_input")
    if user_input:
        st.session_state[msgs_key].append({
            "role": "user",
            "content": user_input,
        })
        intent, params = _detect_intent(user_input)
        if intent == "explain_stage":
            response = _handle_explain_stage(params["stage"])
        elif intent == "show_ticker":
            response = _handle_show_ticker(params["ticker"])
        else:
            response = _handle_llm(user_input, current_ticker, current_memo)
        st.session_state[msgs_key].append({
            "role": "assistant",
            "content": response,
        })
        st.rerun()


def render_audio_button(
    text: str,
    *,
    key: str,
    button_label: str = "🔊 Listen to this memo",
    spinner_text: str = "Generating audio...",
):
    """
    Standalone 🔊 button that plays a given block of text (e.g. a memo).
    Audio path persists via session state keyed on `key`.
    """
    if not audio_is_available():
        return

    audio_key = f"audio_path__{key}"

    if st.session_state.get(audio_key):
        try:
            with open(st.session_state[audio_key], "rb") as f:
                st.audio(f.read(), format="audio/mpeg")
        except Exception:
            st.session_state.pop(audio_key, None)
        return

    if st.button(button_label, key=f"play_btn__{key}"):
        with st.spinner(spinner_text):
            path = generate_audio(text)
        if path:
            st.session_state[audio_key] = str(path)
            st.rerun()
        else:
            st.caption("⚠ Audio generation failed — API quota or network issue.")
