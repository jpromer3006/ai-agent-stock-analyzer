"""
energy_agent.py — Energy / Utilities specialist.
"""

from __future__ import annotations

from agents.orchestrator import AgentProfile
from agents.specialists._shared import build_system_prompt
from data.tickers import StockCategory


PERSONA = """\
You are a senior energy / utilities equity analyst covering integrated oil, \
E&P, pipeline, regulated electric/gas utilities, and renewable power.\
"""

WORKFLOW = """\
1. Pull income statement, balance sheet, cash flow from SEC XBRL
2. Search 10-K for: 'reserves', 'production', 'rate base', 'regulatory approvals', \
   'renewable capacity', 'carbon emissions', 'commodity hedging'
3. For oil/gas: assess reserves, production growth, capital intensity, F&D costs
4. For utilities: focus on rate base growth, regulated returns (ROE allowed), \
   authorized rate increases
5. For renewables: project pipeline, PPA contracts, ITC/PTC tax credits
6. Evaluate dividend yield and payout ratio (utilities are yield stories)
7. Balance sheet strength matters — capital intensive industry
8. Get macro context (oil price, rates affect utilities)\
"""

MEMO_SECTIONS = [
    "Executive Summary",
    "Business Overview",
    "Income Statement",
    "Balance Sheet",
    "Cash Flow & Capital Allocation",
    "Commodity/Regulatory Exposure",
    "Dividend Safety",
    "10-K Risk Factors",
    "Live Bull Probability & Sentiment",
    "Analyst Take",
]

SYSTEM_PROMPT = build_system_prompt(PERSONA, WORKFLOW, MEMO_SECTIONS)

PROFILE = AgentProfile(
    name="⚡ Energy/Utilities Specialist",
    category=StockCategory.ENERGY,
    system_prompt=SYSTEM_PROMPT,
    memo_sections=MEMO_SECTIONS,
)
