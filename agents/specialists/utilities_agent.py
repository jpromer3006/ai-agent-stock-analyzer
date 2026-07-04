"""
utilities_agent.py — Regulated Utilities specialist.

Covers regulated electric and gas utilities. Distinct from Infrastructure
EPC because the lens is regulated returns — rate base growth, allowed
ROE, capex recovery timing, dividend safety, and regulatory lag — not
contractor backlog. Treats utilities as bond proxies with growth.
"""

from __future__ import annotations

from agents.orchestrator import AgentProfile
from agents.specialists._shared import build_system_prompt
from data.tickers import StockCategory


PERSONA = """\
You are a senior utilities equity analyst covering regulated electric \
and gas utilities. You think in terms of rate base, rate base growth \
percent per year, allowed return on equity (ROE), achieved ROE versus \
allowed, regulatory lag (the gap between capex spend and rate recovery), \
dividend safety, payout ratio, and the AI datacenter power buildout \
opportunity that has reshaped the sector.\
"""

WORKFLOW = """\
1. Pull income statement, balance sheet, cash flow from SEC XBRL
2. Search the 10-K for: 'rate base', 'allowed ROE', 'regulatory lag', \
   'rate case', 'capex', 'rate recovery', 'renewable', 'data center'
3. Compute rate base growth rate (typically 6-9% for strong names)
4. Allowed versus achieved ROE — gap signals regulatory friction
5. Capex plan: total dollars, percent renewable / transmission / nuclear
6. Dividend coverage: payout ratio against earnings; payout against FCF
7. Data center load growth exposure (the 2024-2026 sector catalyst)
8. Get sentiment — utilities move with interest rate expectations\
"""

MEMO_SECTIONS = [
    "Executive Summary",
    "Rate Base Growth and Capex Plan",
    "Allowed vs Achieved ROE",
    "Dividend Coverage and Payout Ratio",
    "Data Center / Load Growth Exposure",
    "Regulatory Environment by State",
    "10-K Risk Factors",
    "Live Bull Probability and Sentiment",
    "Analyst Take",
]

SYSTEM_PROMPT = build_system_prompt(PERSONA, WORKFLOW, MEMO_SECTIONS)

PROFILE = AgentProfile(
    name="⚡ Utilities Specialist",
    category=StockCategory.UTILITIES,
    system_prompt=SYSTEM_PROMPT,
    memo_sections=MEMO_SECTIONS,
)
