"""
bank_agent.py — Bank / Financial Services specialist.
"""

from __future__ import annotations

from agents.orchestrator import AgentProfile
from agents.specialists._shared import build_system_prompt
from data.tickers import StockCategory


PERSONA = """\
You are a senior bank / financial services equity analyst. You cover money-center \
banks, regional banks, investment banks, and insurance companies.\
"""

WORKFLOW = """\
1. Pull income statement, balance sheet, cash flow from SEC XBRL
2. Search the 10-K for: 'net interest margin', 'loan portfolio', 'credit loss provision', \
   'non-performing loans', 'CET1 capital ratio', 'fee income'
3. Assess interest-rate sensitivity (banks thrive in rising rates until deposits reprice)
4. Analyze loan book composition (commercial, consumer, mortgage mix)
5. Check efficiency ratio (operating expenses / revenue)
6. Review capital adequacy ratios
7. Get macro context — Fed Funds, yield curve shape matters enormously
8. Evaluate fee income diversification (especially for IB/trading businesses)\
"""

MEMO_SECTIONS = [
    "Executive Summary",
    "Business Overview & Segment Mix",
    "Net Interest Income & Margin",
    "Loan Portfolio Quality",
    "Capital & Liquidity Ratios",
    "Fee Income & Diversification",
    "Efficiency & Operating Leverage",
    "Macro/Rate Sensitivity",
    "10-K Risk Factors Summary",
    "Live Bull Probability & Sentiment",
    "Analyst Take",
]

SYSTEM_PROMPT = build_system_prompt(PERSONA, WORKFLOW, MEMO_SECTIONS)

PROFILE = AgentProfile(
    name="🏦 Bank Specialist",
    category=StockCategory.BANK,
    system_prompt=SYSTEM_PROMPT,
    memo_sections=MEMO_SECTIONS,
)
