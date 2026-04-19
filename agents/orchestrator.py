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
    # Keychain first
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

    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set. Add to Keychain or .env."
        )
    return key


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
    from agents.specialists import reit_agent, infra_agent, bank_agent, tech_agent
    from agents.specialists import energy_agent, consumer_agent, healthcare_agent, generic_agent

    mapping = {
        StockCategory.REIT:               reit_agent.PROFILE,
        StockCategory.INFRASTRUCTURE_EPC: infra_agent.PROFILE,
        StockCategory.BANK:               bank_agent.PROFILE,
        StockCategory.TECH:               tech_agent.PROFILE,
        StockCategory.ENERGY:             energy_agent.PROFILE,
        StockCategory.CONSUMER:           consumer_agent.PROFILE,
        StockCategory.HEALTHCARE:         healthcare_agent.PROFILE,
        StockCategory.GENERIC:            generic_agent.PROFILE,
    }
    return mapping[category]


# ---------------------------------------------------------------------------
# Main agentic loop
# ---------------------------------------------------------------------------
def run_agent(
    ticker: str,
    user_query: Optional[str] = None,
    max_steps: int = 25,
    model: str = "claude-sonnet-4-20250514",
) -> Generator[dict, None, None]:
    """
    Run the full agentic research loop for a ticker.
    Yields trace events suitable for streaming to the UI.
    """
    ticker = ticker.upper()

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
            yield {"type": "done", "memo": memo, "steps": step + 1}
            return

        if response.stop_reason != "tool_use":
            # Unexpected — emit and stop
            memo = "\n".join(
                b.text for b in response.content if b.type == "text"
            )
            yield {"type": "done", "memo": memo, "steps": step + 1}
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
        yield {"type": "done", "memo": memo, "steps": max_steps}
    except Exception as exc:
        yield {
            "type": "done",
            "memo": (
                f"# {ticker} Research Memo — Incomplete\n\n"
                f"The agent reached its tool-call budget. Retry with **Run Agent** "
                f"— cached data from this run will make the second attempt complete "
                f"faster.\n\nError: {exc}"
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
