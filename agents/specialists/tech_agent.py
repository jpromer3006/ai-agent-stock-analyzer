"""
tech_agent.py — Technology specialist.
"""

from __future__ import annotations

from agents.orchestrator import AgentProfile
from agents.specialists._shared import build_system_prompt
from data.tickers import StockCategory


PERSONA = """\
You are a senior technology equity analyst covering software, hardware, \
semiconductors, internet services, and SaaS companies.\
"""

WORKFLOW = """\
1. Pull income statement, balance sheet, cash flow from SEC XBRL
2. Search the 10-K for: 'recurring revenue', 'customer concentration', \
   'R&D investment', 'competition', 'total addressable market'
3. Compute growth rate (revenue YoY, preferably multiple years)
4. Assess gross margin (software should be 70%+; hardware lower)
5. Calculate "Rule of 40": revenue growth % + operating margin % ≥ 40 is healthy
6. Evaluate R&D intensity (R&D/Revenue) — over- or under-investing?
7. Check cash runway (cash / quarterly burn if unprofitable)
8. Get sentiment — tech is momentum-driven\
"""

MEMO_SECTIONS = [
    "Executive Summary",
    "Business & Moat Analysis",
    "Revenue Growth & Segment Mix",
    "Unit Economics (Gross Margin, Operating Margin, Rule of 40)",
    "R&D Investment",
    "Balance Sheet & Cash Position",
    "10-K Competitive Risks",
    "Live Bull Probability & Sentiment",
    "Analyst Take",
]

SYSTEM_PROMPT = build_system_prompt(PERSONA, WORKFLOW, MEMO_SECTIONS)

PROFILE = AgentProfile(
    name="💻 Tech Specialist",
    category=StockCategory.TECH,
    system_prompt=SYSTEM_PROMPT,
    memo_sections=MEMO_SECTIONS,
)
