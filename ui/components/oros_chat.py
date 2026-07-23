"""
oros_chat.py - Ask OROS (JP, 2026-07-20): a scoped Claude chat.

Two uses:
  render_ask_oros()  - end of the Research dossier: answers questions about
                       the memo/dossier and finance/SEC/OROS topics only.
  draft_scripts()    - YouTube News tab: rewrites broadcast scene scripts
                       from an uploaded research report.

Guardrails, in order of strength:
  1. Out-of-scope questions get the EXACT fixed refusal line.
  2. Never personalized financial advice - educational explanations only.
  3. The broadcast disclaimer is enforced in code (daily_video appends the
     official disclosure to any overridden sign-off), so no prompt - however
     clever - can remove it. The assistant is also told not to write one.
"""

from __future__ import annotations

import json
import logging

import streamlit as st

logger = logging.getLogger(__name__)

REFUSAL = "OROS is an AI-Agent Stock Analyzer and is unable to answer that."

_QA_SYSTEM = (
    "You are OROS, an AI-Agent Stock Analyzer built as a Fordham University "
    "educational project. You may ONLY discuss: the research dossier and "
    "memo provided in context; finance, markets, and investing concepts; "
    "SEC filings and public-company fundamentals; economic data; and how "
    "the OROS system itself works (stage analysis, its agents, risk rules, "
    "the paper portfolios, the Market Desk broadcast). "
    f'If a question falls outside those topics, reply EXACTLY: "{REFUSAL}" '
    "and nothing else. "
    "Hard rules: educational only - NEVER personalized financial advice, "
    "never tell the user to buy, sell, or hold anything; if asked, explain "
    "the relevant analysis and note that OROS is educational and they "
    "should consult a licensed advisor. Never invent figures - if the "
    "context or your knowledge does not contain a number, say so plainly. "
    "Cite the memo sections you draw on. Be concise and warm. "
    "Reply in whatever language the user writes in; when refusing an "
    "out-of-scope question in another language, translate the refusal "
    "line faithfully, preserving its exact meaning."
)

_SCRIPT_SYSTEM = (
    "You write broadcast narration for the OROS Market Desk, an autonomous, "
    "educational stock-analysis news show. Style: plain spoken English for "
    "text-to-speech - no markdown, no headers, no stage directions; short "
    "clear sentences; an honest, calm anchor's voice; educational, never "
    "telling viewers to buy or sell. Separate narration segments with a "
    "single blank line. NEVER write, alter, or include legal disclaimers - "
    "the system appends the official disclosure itself and it cannot be "
    "removed. Return ONLY a JSON object mapping the scene keys you were "
    "given to their new narration strings. No commentary outside the JSON."
)


def _client():
    from agents.anthropic_key import require_anthropic_key
    import anthropic
    return anthropic.Anthropic(api_key=require_anthropic_key())


def _text_of(resp) -> str:
    return "".join(b.text for b in resp.content
                   if getattr(b, "type", "") == "text").strip()


def ask_oros(messages: list[dict], context: str = "",
             system: str | None = None, model: str = "claude-haiku-4-5",
             max_tokens: int = 700) -> str:
    sys = (system or _QA_SYSTEM)
    if context:
        sys += "\n\n--- Dossier / memo context ---\n" + context[:12000]
    resp = _client().messages.create(model=model, max_tokens=max_tokens,
                                     system=sys, messages=messages)
    return _text_of(resp)


def draft_scripts(current: dict[str, str], instruction: str,
                  report_text: str = "") -> dict[str, str]:
    """Rewrite scene scripts; returns {scene_key: narration}. Uses the
    stronger default agent model since this is on-air copy."""
    from agents.anthropic_key import default_model
    user = ("Current scene scripts (JSON):\n" + json.dumps(current, indent=1)
            + (("\n\n--- Source research report ---\n" + report_text[:14000])
               if report_text else "")
            + "\n\nInstruction: " + instruction
            + "\n\nRewrite ONLY the scenes given above. Return JSON only.")
    txt = ask_oros([{"role": "user", "content": user}],
                   system=_SCRIPT_SYSTEM, model=default_model(),
                   max_tokens=2000)
    if txt.startswith("```"):
        txt = txt.strip("`")
        txt = txt[txt.find("{"):txt.rfind("}") + 1]
    out = json.loads(txt[txt.find("{"):txt.rfind("}") + 1])
    return {str(k): str(v) for k, v in out.items() if str(v).strip()}


def render_ask_oros(subject: str, context: str = "",
                    heading: str = "### 6️⃣ 💬 Ask OROS",
                    caption: str | None = None,
                    placeholder: str | None = None) -> None:
    """Reusable scoped chat. `subject` keys the history (a ticker on the
    dossier, 'portfolio' on the Portfolio tab); `context` grounds answers."""
    st.markdown(heading)
    st.caption(caption or
               "Questions about this dossier, the memo, or anything "
               "finance, SEC, or OROS. Educational conversation only — "
               "OROS never gives personalized financial advice.")
    key = f"oros_chat_{subject}"
    hist = st.session_state.setdefault(key, [])
    for m in hist:
        with st.chat_message(m["role"]):
            st.markdown(m["content"].replace("$", "\\$"))
    q = st.chat_input(placeholder or
                      f"Ask about {subject}, the memo, or the system...",
                      key=f"oros_chat_input_{subject}")
    if q:
        hist.append({"role": "user", "content": q})
        with st.chat_message("user"):
            st.markdown(q)
        with st.chat_message("assistant"):
            with st.spinner("OROS is reading the dossier..."):
                try:
                    a = ask_oros(hist[-8:], context=context)
                except Exception as e:  # noqa: BLE001
                    logger.warning("ask-oros failed: %s", e)
                    a = ("OROS chat is unavailable right now "
                         f"({type(e).__name__}). The dossier above is "
                         "unaffected.")
            st.markdown(a.replace("$", "\\$"))
        hist.append({"role": "assistant", "content": a})
