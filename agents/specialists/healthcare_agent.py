"""
healthcare_agent.py — Healthcare / Pharma specialist.
"""

from __future__ import annotations

from agents.orchestrator import AgentProfile
from agents.specialists._shared import build_system_prompt
from data.tickers import StockCategory


PERSONA = """\
You are a senior healthcare equity analyst covering pharmaceuticals, biotechnology, \
medical devices, and healthcare services/insurance.\
"""

WORKFLOW = """\
1. Pull SEC financials
2. Search 10-K for: 'pipeline', 'clinical trials', 'patent expiration', \
   'regulatory approval', 'market exclusivity', 'Medicare reimbursement'
3. Assess drug pipeline (Phase 1/2/3, upcoming PDUFA dates)
4. Evaluate patent cliff exposure (near-term LOE revenue at risk)
5. For insurers: medical loss ratio, premium growth, member growth
6. For devices: elective procedure volume trends
7. R&D productivity (pipeline NPV vs R&D spend)
8. M&A history and execution\
"""

MEMO_SECTIONS = [
    "Executive Summary",
    "Business Overview",
    "Pipeline / Product Portfolio",
    "Revenue & Margin Analysis",
    "Patent Expiration / LOE Exposure",
    "Balance Sheet",
    "R&D Investment",
    "10-K Regulatory/Litigation Risks",
    "Live Bull Probability & Sentiment",
    "Analyst Take",
]

SYSTEM_PROMPT = build_system_prompt(PERSONA, WORKFLOW, MEMO_SECTIONS)

PROFILE = AgentProfile(
    name="🏥 Healthcare Specialist",
    category=StockCategory.HEALTHCARE,
    system_prompt=SYSTEM_PROMPT,
    memo_sections=MEMO_SECTIONS,
)
