"""
orchestrator.py — True agentic loop using Claude's tool-use API.

Flow:
    1. Classify ticker → select specialist agent
    2. Build the agent's tool schema (base tools + specialist-specific tools)
    3. Run the Claude tool-use loop:
        - Claude selects a tool to call
        - We execute it, return result
        - Claude decides next tool or writes the memo
    4. Yield trace events for live UI display

Trace events (for Streamlit streaming):
    - {type: "classified", category, agent_name}
    - {type: "tool_call", tool, input}
    - {type: "tool_result", tool, result_preview}
    - {type: "text_delta", text}
    - {type: "done", memo}
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Generator, Optional

from agents.base_tools import BASE_TOOL_REGISTRY, BASE_TOOL_SCHEMAS
from agents.classifier import classify
from data.tickers import StockCategory, UNIVERSE

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent

# ---------------------------------------------------------------------------
# API key
# ---------------------------------------------------------------------------
def _get_anthropic_key() -> str:
    # Shared resolver: Keychain first, falls back to a working .env key.
    from agents.anthropic_key import require_anthropic_key
    return require_anthropic_key()


# ---------------------------------------------------------------------------
# Citation formatting
# ---------------------------------------------------------------------------
_SOURCE_RE = re.compile(r"\[Source:\s*([^\]]+)\]", re.IGNORECASE)


def _to_footnotes(memo: str) -> str:
    """Convert inline ``[Source: X]`` citations into numbered footnote markers
    plus a single ``## Sources`` list appended to the memo.

    Stacked sources — one bracket holding several (``[Source: A; B]``) or
    adjacent brackets — are each numbered once; a source that recurs reuses
    its number, so the same filing is never listed twice. Idempotent: a memo
    that already carries a ``## Sources`` section, or one with no inline
    citations, is returned unchanged.
    """
    if not memo or "## Sources" in memo:
        return memo
    if not _SOURCE_RE.search(memo):
        return memo

    order: list[str] = []        # unique sources, first-seen order
    index: dict[str, int] = {}   # normalized source -> 1-based number

    def _register(src: str) -> int:
        label = " ".join(src.split())  # collapse whitespace (matches legacy)
        if label in index:
            return index[label]
        order.append(label)
        index[label] = len(order)
        return index[label]

    def _replace(m: "re.Match[str]") -> str:
        parts = [s.strip() for s in m.group(1).split(";") if s.strip()]
        return "".join(f"[{_register(s)}]" for s in parts)

    body = _SOURCE_RE.sub(_replace, memo).rstrip()
    if not order:
        return memo
    src_lines = "\n".join(f"[{i}] {label}" for i, label in enumerate(order, 1))
    return f"{body}\n\n---\n\n## Sources\n\n{src_lines}\n"


def _append_disclaimer(memo: str) -> str:
    """Footnote citations, then append the canonical educational disclaimer.

    Restores the legacy finalize step the refactor dropped: every memo that
    reaches the UI carries numbered Sources and the 'not financial advice'
    disclaimer the project rules require. Idempotent on both the footnotes
    (``## Sources`` guard) and the disclaimer (``Important Disclaimer`` guard).
    """
    if not memo:
        return memo
    memo = _to_footnotes(memo)
    if "Important Disclaimer" in memo:
        return memo
    try:
        from ui.components.disclaimer import DISCLAIMER_MARKDOWN
    except Exception:
        return memo  # never block a memo if the disclaimer module is unavailable
    sep = "" if memo.endswith("\n") else "\n"
    return f"{memo}{sep}\n{DISCLAIMER_MARKDOWN}"


def _source_provenance(memo: str, ticker: str) -> str:
    """Append a 'Source Provenance' block listing the reputable origin of every
    data family the memo drew on, with auditable links. Makes the memo's
    sourcing transparent and verifiable (SEC figures resolve to the company's
    EDGAR filings page). Idempotent; only lists families actually cited.
    """
    if not memo or "### Source Provenance" in memo:
        return memo

    low = memo.lower()
    lines: list[str] = []

    if any(k in low for k in ("sec ", "xbrl", "10-k", "10k", "edgar", "md&a", "risk factor")):
        edgar = None
        try:
            from data.sec_client import get_cik
            cik = get_cik(ticker)
            if cik:
                edgar = (
                    f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                    f"&CIK={cik}&type=10-K&dateb=&owner=include&count=40"
                )
        except Exception:
            edgar = None
        if not edgar:
            edgar = (
                f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                f"&company={ticker}&type=10-K&dateb=&owner=include&count=40"
            )
        lines.append(
            f"- **SEC EDGAR** (primary): structured XBRL facts and 10-K text — "
            f"official filings for {ticker.upper()} at {edgar}"
        )
    if any(k in low for k in ("fred", "macro", "federal reserve", "treasury", "cpi", "rate")):
        lines.append(
            "- **FRED, Federal Reserve Bank of St. Louis** (macro series): "
            "https://fred.stlouisfed.org"
        )
    if any(k in low for k in ("yfinance", "live price", "market data", "sentiment", "headline")):
        lines.append(
            "- **Market data and news sentiment**: live quotes and headlines via "
            "Yahoo Finance, VADER sentiment on recent headlines"
        )
    if any(k in low for k in ("weinstein", "stage analysis", "30-week")):
        lines.append(
            "- **Methodology**: Stan Weinstein, *Secrets for Profiting in Bull "
            "and Bear Markets* (stage analysis framework)"
        )

    if not lines:
        return memo
    block = "### Source Provenance\n\n" + "\n".join(lines) + "\n"
    sep = "" if memo.endswith("\n") else "\n"
    return f"{memo}{sep}\n{block}"


def _finalize_memo(memo: str, ticker: str) -> str:
    """Single finalize step for every memo the agent emits: number the
    citations, append reputable source provenance, then the disclaimer.
    Produces the order body -> Sources -> Source Provenance -> Disclaimer.
    """
    memo = _to_footnotes(memo)
    memo = _source_provenance(memo, ticker)
    memo = _append_disclaimer(memo)  # _to_footnotes inside is idempotent
    return memo


# ---------------------------------------------------------------------------
# Agent profile
# ---------------------------------------------------------------------------
@dataclass
class AgentProfile:
    """Describes a specialist agent: prompt, extra tools, memo structure."""
    name: str                       # e.g., "REIT Agent"
    category: StockCategory
    system_prompt: str
    extra_tool_schemas: list[dict] = field(default_factory=list)
    extra_tool_registry: dict = field(default_factory=dict)
    memo_sections: list[str] = field(default_factory=list)


def _load_specialist(category: StockCategory) -> AgentProfile:
    """Lazy-load the specialist agent module for a category."""
    from agents.specialists import (
        reit_agent, infra_agent, bank_agent, tech_agent,
        energy_agent, consumer_agent, healthcare_agent, generic_agent,
        ai_compute_agent, semis_agent, software_agent,
        biotech_agent, pharma_meddevice_agent,
        utilities_agent, defense_agent, industrials_agent, insurance_agent,
        fund_agent,
    )

    mapping = {
        # Original 8
        StockCategory.REIT:               reit_agent.PROFILE,
        StockCategory.INFRASTRUCTURE_EPC: infra_agent.PROFILE,
        StockCategory.BANK:               bank_agent.PROFILE,
        StockCategory.TECH:               tech_agent.PROFILE,   # mega-platforms
        StockCategory.ENERGY:             energy_agent.PROFILE,
        StockCategory.CONSUMER:           consumer_agent.PROFILE,
        StockCategory.HEALTHCARE:         healthcare_agent.PROFILE,
        StockCategory.GENERIC:            generic_agent.PROFILE,
        # Phase 3b additions (restored — the refactor had dropped these from
        # the mapping, so AI_COMPUTE/SEMIS/etc. KeyError'd on classify)
        StockCategory.AI_COMPUTE:         ai_compute_agent.PROFILE,
        StockCategory.SEMICONDUCTORS:     semis_agent.PROFILE,
        StockCategory.SOFTWARE_SAAS:      software_agent.PROFILE,
        StockCategory.BIOTECH:            biotech_agent.PROFILE,
        StockCategory.PHARMA_MEDDEVICE:   pharma_meddevice_agent.PROFILE,
        StockCategory.UTILITIES:          utilities_agent.PROFILE,
        StockCategory.DEFENSE:            defense_agent.PROFILE,
        StockCategory.INDUSTRIALS:        industrials_agent.PROFILE,
        StockCategory.INSURANCE:          insurance_agent.PROFILE,
        # Funds (ETFs + mutual funds): price/stage/macro tools only — funds
        # file N-PORT/N-CSR, not 10-Ks, so SEC tools are useless for them.
        StockCategory.FUND:               fund_agent.PROFILE,
    }
    # Fallback to the generic specialist so a new/unmapped category degrades
    # to a working memo instead of crashing the whole research run.
    return mapping.get(category, generic_agent.PROFILE)


# ---------------------------------------------------------------------------
# Main agentic loop
# ---------------------------------------------------------------------------
def run_agent(
    ticker: str,
    user_query: Optional[str] = None,
    max_steps: int = 25,
    model: Optional[str] = None,
) -> Generator[dict, None, None]:
    """
    Run the full agentic research loop for a ticker.
    Yields trace events suitable for streaming to the UI.
    """
    ticker = ticker.upper()
    if model is None:
        from agents.anthropic_key import default_model
        model = default_model()

    # 1. Classify
    category = classify(ticker)
    profile = _load_specialist(category)
    meta = UNIVERSE.get(ticker)
    company = meta.company_name if meta else ticker

    yield {
        "type": "classified",
        "ticker": ticker,
        "company": company,
        "category": category.value,
        "agent_name": profile.name,
    }

    # 2. Build tools
    tools = BASE_TOOL_SCHEMAS + profile.extra_tool_schemas
    registry = {**BASE_TOOL_REGISTRY, **profile.extra_tool_registry}

    # 3. Build user message
    default_query = (
        f"Generate a comprehensive research memo for {ticker} ({company}). "
        f"Use your tools to pull SEC financial data, analyze the 10-K, "
        f"and assess the investment case. Structure your response with these sections:\n\n"
        + "\n".join(f"## {s}" for s in profile.memo_sections)
        + "\n\nEFFICIENCY: Call MULTIPLE tools in parallel in the same response "
        "whenever possible — e.g. get_market_regime, get_income_statement, "
        "get_balance_sheet, get_cash_flow can all be requested together in one "
        "round. This is much faster than calling them sequentially. Only serialize "
        "when a later call genuinely depends on an earlier result."
    )
    raw_query = user_query or default_query

    # Reinforce citation rules in the user message (system prompt alone gets diluted
    # across multi-turn tool use loops)
    CITATION_REMINDER = (
        "\n\n"
        "CRITICAL REMINDER — CITATION ENFORCEMENT:\n"
        "Your response will be automatically validated. Every dollar amount, "
        "percentage, and ratio MUST be followed by an inline citation within "
        "the same sentence, in one of these exact formats:\n"
        "  [Source: SEC XBRL FY2024]\n"
        "  [Source: SEC 10-K Item 7 - MD&A]\n"
        "  [Source: SEC 10-K Item 1A - Risk Factors]\n"
        "  [Source: computed from SEC XBRL]\n"
        "  [Source: yfinance — live]\n\n"
        "Examples:\n"
        "  ✅ 'Revenue of $5,749M [Source: SEC XBRL FY2025] rose 9.1% "
        "[Source: computed from SEC XBRL].'\n"
        "  ❌ 'Revenue was $5,749M and rose 9.1%.' (no citations — will fail)\n\n"
        "A post-generation validator will reject responses with < 70% citation "
        "coverage. Cite every number, every time."
    )
    user_message = raw_query + CITATION_REMINDER

    # 4. Run tool-use loop
    try:
        import anthropic
    except ImportError:
        yield {"type": "error", "message": "anthropic SDK not installed"}
        return

    api_key = _get_anthropic_key()
    client = anthropic.Anthropic(api_key=api_key)

    messages = [{"role": "user", "content": user_message}]

    for step in range(max_steps):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=3000,
                system=profile.system_prompt,
                tools=tools,
                messages=messages,
            )
        except Exception as exc:
            yield {"type": "error", "message": f"Claude API error: {exc}"}
            return

        # Emit any text blocks
        for block in response.content:
            if block.type == "text":
                yield {"type": "text_delta", "text": block.text}

        if response.stop_reason == "end_turn":
            # Assemble final memo from any text blocks
            memo = "\n".join(
                b.text for b in response.content if b.type == "text"
            )
            yield {"type": "done", "memo": _finalize_memo(memo, ticker), "steps": step + 1}
            return

        if response.stop_reason != "tool_use":
            # Unexpected — emit and stop
            memo = "\n".join(
                b.text for b in response.content if b.type == "text"
            )
            yield {"type": "done", "memo": _finalize_memo(memo, ticker), "steps": step + 1}
            return

        # Execute tool calls
        tool_results = []
        assistant_content = []
        for block in response.content:
            if block.type == "text":
                assistant_content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                assistant_content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })
                yield {
                    "type": "tool_call",
                    "tool": block.name,
                    "input": block.input,
                }
                # Execute
                impl = registry.get(block.name)
                if impl is None:
                    result_str = f"Tool '{block.name}' not found."
                else:
                    try:
                        result_str = impl(**block.input)
                    except Exception as exc:
                        result_str = f"Tool '{block.name}' failed: {exc}"

                preview = result_str[:400] + ("..." if len(result_str) > 400 else "")
                yield {
                    "type": "tool_result",
                    "tool": block.name,
                    "result_preview": preview,
                    "result_length": len(result_str),
                }
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_str,
                })

        # Append assistant message and user tool_result message
        messages.append({"role": "assistant", "content": assistant_content})
        messages.append({"role": "user", "content": tool_results})

    # Max steps reached — graceful recovery: ask Claude to finalize with what it has
    yield {
        "type": "tool_call",
        "tool": "finalize_memo",
        "input": {"reason": "max_steps_reached"},
    }
    messages.append({
        "role": "user",
        "content": (
            f"You've used all {max_steps} tool rounds. STOP calling tools and "
            f"write the final memo NOW using the data you already have. It's "
            f"better to deliver a memo that says 'data not available for section X' "
            f"than to fail entirely. Structure it with the required sections and "
            f"cite inline where you have data. If a section has no data, write: "
            f"'Data not pulled within allotted tool calls — run the agent again "
            f"to retrieve.'"
        ),
    })
    try:
        final = client.messages.create(
            model=model,
            max_tokens=3000,
            system=profile.system_prompt,
            messages=messages,  # no tools this time — force text response
        )
        memo = "\n".join(b.text for b in final.content if b.type == "text")
        if not memo.strip():
            memo = (
                f"# {ticker} Research Memo — Incomplete\n\n"
                f"The agent reached its tool-call budget ({max_steps} rounds) "
                f"before the memo was written. This typically means {ticker}'s "
                f"10-K is unusually long or the agent took many serial tool "
                f"calls. Try **Run Agent** again — cached data from this run "
                f"will make the second attempt complete faster."
            )
        yield {"type": "done", "memo": _finalize_memo(memo, ticker), "steps": max_steps}
    except Exception as exc:
        yield {
            "type": "done",
            "memo": _finalize_memo(
                f"# {ticker} Research Memo — Incomplete\n\n"
                f"The agent reached its tool-call budget. Retry with **Run Agent** "
                f"— cached data from this run will make the second attempt complete "
                f"faster.\n\nError: {exc}",
                ticker,
            ),
            "steps": max_steps,
        }


# ---------------------------------------------------------------------------
# Synchronous collector (for non-streaming callers)
# ---------------------------------------------------------------------------
@dataclass
class AgentRunResult:
    ticker: str
    company: str
    category: str
    agent_name: str
    memo: str
    tool_calls: list[dict]
    steps: int
    generated_at: datetime = field(default_factory=datetime.utcnow)


def analyze(ticker: str, user_query: Optional[str] = None) -> AgentRunResult:
    """Run the agent and collect results into a single AgentRunResult."""
    events = list(run_agent(ticker, user_query))

    classified = next((e for e in events if e["type"] == "classified"), {})
    done = next((e for e in events if e["type"] == "done"), {"memo": "", "steps": 0})
    tool_calls = [
        {
            "tool": e["tool"],
            "input": e["input"],
            "result_preview": next(
                (r["result_preview"] for r in events
                 if r["type"] == "tool_result" and r.get("tool") == e["tool"]),
                ""
            ),
        }
        for e in events if e["type"] == "tool_call"
    ]

    return AgentRunResult(
        ticker=ticker.upper(),
        company=classified.get("company", ticker),
        category=classified.get("category", ""),
        agent_name=classified.get("agent_name", ""),
        memo=done.get("memo", ""),
        tool_calls=tool_calls,
        steps=done.get("steps", 0),
    )


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "O"
    print(f"Running agent for {ticker}...\n")

    for event in run_agent(ticker):
        etype = event["type"]
        if etype == "classified":
            print(f"→ Classified: {event['category']} → {event['agent_name']}")
        elif etype == "tool_call":
            print(f"→ Tool: {event['tool']}({json.dumps(event['input'])})")
        elif etype == "tool_result":
            print(f"  ← {event['result_length']} chars")
        elif etype == "text_delta":
            print(f"\n[Memo fragment]\n{event['text'][:500]}...")
        elif etype == "done":
            print(f"\n=== DONE in {event['steps']} steps ===")
            print(event["memo"][:2000])
        elif etype == "error":
            print(f"ERROR: {event['message']}")
