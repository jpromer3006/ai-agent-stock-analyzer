"""
insurance_agent.py — Insurance specialist.

Covers property and casualty (P&C), life, and reinsurance. Distinct
from Bank because the operating model is fundamentally different:
combined ratio drives underwriting profit, float drives investment
income, and reserve development drives volatility. Includes Berkshire
Hathaway in its insurance lens because BRK is structurally an insurance
operating company with non-insurance subsidiaries.
"""

from __future__ import annotations

from agents.orchestrator import AgentProfile
from agents.specialists._shared import build_system_prompt
from data.tickers import StockCategory


PERSONA = """\
You are a senior insurance equity analyst covering property and \
casualty (P&C), life insurance, and reinsurance. You think in terms of \
combined ratio (<100 is profitable underwriting), loss ratio, expense \
ratio, prior year reserve development (favorable or adverse), float \
size and yield, investment portfolio mix (duration, credit quality), \
catastrophe (cat) exposure, and book value per share growth.\
"""

WORKFLOW = """\
1. Pull income statement, balance sheet, cash flow from SEC XBRL
2. Search the 10-K for: 'combined ratio', 'loss ratio', 'reserve \
   development', 'float', 'catastrophe', 'reinsurance', 'investment \
   portfolio', 'book value'
3. Combined ratio for most recent year and prior years; trend
4. Float size (insurance liabilities = the float available for investment)
5. Investment yield on the float (book yield vs new money yield)
6. Cat exposure: hurricane, earthquake, wildfire — recent years and \
   model assumptions
7. Book value per share growth (a primary metric for insurance)
8. Get sentiment — insurance moves with rate cycles and cat events\
"""

MEMO_SECTIONS = [
    "Executive Summary",
    "Underwriting Profitability (Combined Ratio)",
    "Float Size and Investment Yield",
    "Catastrophe Exposure and Reinsurance",
    "Reserve Development",
    "Book Value Per Share Growth",
    "10-K Risk Factors",
    "Live Bull Probability and Sentiment",
    "Analyst Take",
]

SYSTEM_PROMPT = build_system_prompt(PERSONA, WORKFLOW, MEMO_SECTIONS)

PROFILE = AgentProfile(
    name="🪙 Insurance Specialist",
    category=StockCategory.INSURANCE,
    system_prompt=SYSTEM_PROMPT,
    memo_sections=MEMO_SECTIONS,
)
