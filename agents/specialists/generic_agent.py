"""
generic_agent.py — Fallback specialist for stocks that don't fit a specific sector.
"""

from __future__ import annotations

from agents.orchestrator import AgentProfile
from agents.specialists._shared import build_system_prompt
from data.tickers import StockCategory


PERSONA = """\
You are a senior equity research analyst. You cover a broad range of industries \
and produce rigorous, data-driven research memos.\
"""

WORKFLOW = """\
1. Pull SEC financials (income statement, balance sheet, cash flow)
2. Search 10-K for key topics: business overview, competition, risks, strategy
3. Evaluate revenue growth, profitability, cash generation
4. Assess balance sheet strength
5. Check capital allocation (dividends, buybacks, capex, M&A)
6. Get sentiment + macro context\
"""

MEMO_SECTIONS = [
    "Executive Summary",
    "Business Overview",
    "Income Statement Analysis",
    "Balance Sheet",
    "Cash Flow & Capital Allocation",
    "10-K Risk Factors",
    "Macro & Sentiment",
    "Live Bull Probability",
    "Analyst Take",
]

SYSTEM_PROMPT = build_system_prompt(PERSONA, WORKFLOW, MEMO_SECTIONS)

PROFILE = AgentProfile(
    name="📊 Generic Analyst",
    category=StockCategory.GENERIC,
    system_prompt=SYSTEM_PROMPT,
    memo_sections=MEMO_SECTIONS,
)
