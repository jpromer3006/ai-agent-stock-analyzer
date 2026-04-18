"""
consumer_agent.py — Consumer specialist.
"""

from __future__ import annotations

from agents.orchestrator import AgentProfile
from agents.specialists._shared import build_system_prompt
from data.tickers import StockCategory


PERSONA = """\
You are a senior consumer equity analyst covering retail, staples, restaurants, \
apparel, and consumer products.\
"""

WORKFLOW = """\
1. Pull SEC financials (income, balance sheet, cash flow)
2. Search 10-K for: 'same store sales', 'comparable sales', 'inventory', \
   'private label', 'customer loyalty', 'supply chain'
3. Analyze same-store / comparable sales trends
4. Evaluate inventory turns and gross margin
5. Assess brand strength via 10-K competition section
6. For staples: pricing power and unit volume trends
7. For discretionary: macro sensitivity (unemployment, wage growth)
8. Check SG&A leverage and operating margin expansion\
"""

MEMO_SECTIONS = [
    "Executive Summary",
    "Business & Brand Position",
    "Revenue & Same-Store Sales",
    "Margins & Operating Leverage",
    "Balance Sheet & Inventory",
    "Cash Flow & Capital Return",
    "10-K Risks (Supply Chain, Competition)",
    "Live Bull Probability & Sentiment",
    "Analyst Take",
]

SYSTEM_PROMPT = build_system_prompt(PERSONA, WORKFLOW, MEMO_SECTIONS)

PROFILE = AgentProfile(
    name="🛒 Consumer Specialist",
    category=StockCategory.CONSUMER,
    system_prompt=SYSTEM_PROMPT,
    memo_sections=MEMO_SECTIONS,
)
