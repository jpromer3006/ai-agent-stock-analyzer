"""
reit_agent.py — REIT specialist.

Focus: FFO/AFFO, NOI, occupancy, portfolio composition, dividend coverage,
LTV ratio, interest-rate sensitivity, tenant quality.
"""

from __future__ import annotations

from agents.orchestrator import AgentProfile
from agents.specialists._shared import build_system_prompt
from data.tickers import StockCategory


PERSONA = """\
You are a senior REIT equity analyst at a top-tier investment bank. You cover \
Real Estate Investment Trusts across industrial, retail, residential, healthcare, \
data center, cell tower, and specialty sub-sectors.

Your job is to produce a data-driven research memo on a specific REIT. You have \
access to tools that pull SEC XBRL financials, semantic search over the latest \
10-K, news sentiment, price history, live bull probability, and macro context.\
"""


WORKFLOW = """\
1. Pull the income statement, balance sheet, and cash flow statement (SEC XBRL)
2. Compute / estimate FFO (Net Income + Depreciation + amortization)
3. Assess portfolio composition via search_10k (tenant concentration, geography, property type)
4. Check dividend coverage: dividends paid vs. operating cash flow — compute a coverage ratio
5. Evaluate leverage: Total Debt / Total Assets (LTV), debt maturity profile
6. Analyze interest-rate sensitivity (search_10k for 'interest rate risk')
7. Get sentiment + macro context (rates matter enormously for REITs)
8. Compute live bull probability

For REITs specifically, tenant concentration and rent escalators matter — \
surface these concretely with specific numbers from the 10-K.\
"""


TOOL_SCHEMAS = [
    {
        "name": "search_10k_reit",
        "description": (
            "Convenience: search 10-K with REIT-specific queries. "
            "Predefined topics: 'portfolio', 'dividends', 'rates', 'tenants', 'leverage', 'occupancy'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "topic": {
                    "type": "string",
                    "enum": ["portfolio", "dividends", "rates", "tenants", "leverage", "occupancy"],
                },
            },
            "required": ["ticker", "topic"],
        },
    },
]


def tool_search_10k_reit(ticker: str, topic: str, **_) -> str:
    from rag.vector_store import search_and_format
    query_map = {
        "portfolio":  "portfolio composition property type geographic diversification",
        "dividends":  "dividend policy distributions coverage FFO payout",
        "rates":      "interest rate risk sensitivity hedging variable rate debt",
        "tenants":    "tenant concentration creditworthiness investment grade lease",
        "leverage":   "total debt leverage loan to value debt maturity ladder",
        "occupancy":  "occupancy rate lease expiration renewal rent growth",
    }
    return search_and_format(ticker, query_map.get(topic, topic), top_k=5)


TOOL_REGISTRY = {
    "search_10k_reit": tool_search_10k_reit,
}


MEMO_SECTIONS = [
    "Executive Summary",
    "Portfolio & Business Overview",
    "Income Statement Analysis",
    "Balance Sheet & Leverage",
    "Cash Flow & Dividend Safety",
    "Interest Rate Sensitivity",
    "10-K Intelligence (Risk Factors + MD&A Highlights)",
    "Live Bull Probability & Sentiment",
    "Risk Flags",
    "Analyst Take",
]


SYSTEM_PROMPT = build_system_prompt(PERSONA, WORKFLOW, MEMO_SECTIONS)


PROFILE = AgentProfile(
    name="🏢 REIT Specialist",
    category=StockCategory.REIT,
    system_prompt=SYSTEM_PROMPT,
    extra_tool_schemas=TOOL_SCHEMAS,
    extra_tool_registry=TOOL_REGISTRY,
    memo_sections=MEMO_SECTIONS,
)
