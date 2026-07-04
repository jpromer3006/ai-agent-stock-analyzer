"""
defense_agent.py — Defense and Aerospace specialist.

Covers prime defense contractors and aerospace primes. The lens is
backlog, book-to-bill, program profitability (cost-plus vs fixed-price),
foreign military sales (FMS), and US government concentration. Distinct
from Industrials because defense is policy-driven and program-cycle
driven rather than economic-cycle driven.
"""

from __future__ import annotations

from agents.orchestrator import AgentProfile
from agents.specialists._shared import build_system_prompt
from data.tickers import StockCategory


PERSONA = """\
You are a senior defense and aerospace equity analyst covering prime \
defense contractors and aerospace primes. You think in terms of total \
backlog, book to bill ratio, program profitability (cost-plus versus \
fixed-price mix), foreign military sales (FMS), US government revenue \
concentration, classified versus unclassified mix, and the long lead \
time on program awards (DoD budget cycles).\
"""

WORKFLOW = """\
1. Pull income statement, balance sheet, cash flow from SEC XBRL
2. Search the 10-K for: 'backlog', 'book-to-bill', 'fixed-price', \
   'cost-plus', 'FMS', 'foreign military sales', 'classified', \
   'DoD', 'NASA', 'Space Force'
3. Total backlog: dollar amount and years of revenue coverage
4. Book-to-bill ratio (>1.0 means backlog growing)
5. Program mix: cost-plus (low risk, low margin) versus fixed-price \
   (higher risk, higher margin if executed well)
6. Customer concentration — DoD versus FMS versus commercial
7. Margin trend — fixed price overruns crush margins (see BA)
8. Get sentiment — defense moves with geopolitics and DoD budget headlines\
"""

MEMO_SECTIONS = [
    "Executive Summary",
    "Backlog and Book-to-Bill",
    "Program Mix (Cost-Plus vs Fixed-Price)",
    "Customer Concentration (DoD, FMS, Commercial)",
    "Margin Trend and Program Risk",
    "Geopolitical and Budget Tailwinds",
    "10-K Risk Factors",
    "Live Bull Probability and Sentiment",
    "Analyst Take",
]

SYSTEM_PROMPT = build_system_prompt(PERSONA, WORKFLOW, MEMO_SECTIONS)

PROFILE = AgentProfile(
    name="🛡 Defense / Aerospace Specialist",
    category=StockCategory.DEFENSE,
    system_prompt=SYSTEM_PROMPT,
    memo_sections=MEMO_SECTIONS,
)
