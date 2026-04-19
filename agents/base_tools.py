"""
base_tools.py — Shared tool definitions for all specialist agents.

Each tool has:
    1. A Claude tool-use schema (`SCHEMA`) — JSON-like dict
    2. An implementation function that takes **kwargs and returns str

The orchestrator passes tool schemas to Claude, receives tool_use requests,
and dispatches to the implementation via TOOL_REGISTRY.

Design: return strings (not structured data). Claude processes text;
we preserve human-readable formatting so the agent trace is legible.
"""

from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Tool implementations
# ═══════════════════════════════════════════════════════════════════

def tool_get_income_statement(ticker: str, **_) -> str:
    from data.sec_client import format_income_statement
    return format_income_statement(ticker)


def tool_get_balance_sheet(ticker: str, **_) -> str:
    from data.sec_client import format_balance_sheet
    return format_balance_sheet(ticker)


def tool_get_cash_flow(ticker: str, **_) -> str:
    from data.sec_client import format_cash_flow
    return format_cash_flow(ticker)


def tool_search_10k(ticker: str, query: str, top_k: int = 5, **_) -> str:
    from rag.vector_store import search_and_format
    return search_and_format(ticker, query, top_k=top_k)


def tool_get_risk_factors(ticker: str, **_) -> str:
    from data.sec_client import extract_10k_sections
    sections = extract_10k_sections(ticker)
    rf = sections.get("Item 1A - Risk Factors", "")
    if not rf:
        return f"No Item 1A Risk Factors found for {ticker}."
    return f"=== Risk Factors (Item 1A) — {ticker} ===\n{rf[:8000]}"


def tool_get_mda(ticker: str, **_) -> str:
    from data.sec_client import extract_10k_sections
    sections = extract_10k_sections(ticker)
    mda = sections.get("Item 7 - MD&A", "")
    if not mda:
        return f"No Item 7 MD&A found for {ticker}."
    return f"=== MD&A (Item 7) — {ticker} ===\n{mda[:8000]}"


def tool_get_business_description(ticker: str, **_) -> str:
    from data.sec_client import extract_10k_sections
    sections = extract_10k_sections(ticker)
    biz = sections.get("Item 1 - Business", "")
    if not biz:
        return f"No Item 1 Business description found for {ticker}."
    return f"=== Business Overview (Item 1) — {ticker} ===\n{biz[:6000]}"


def tool_get_sentiment(ticker: str, **_) -> str:
    from ml.live_scorer import _sentiment_compound
    compound = _sentiment_compound(ticker)
    if compound is None:
        return f"No news headlines available for {ticker}."

    # Also include a few sample headlines
    try:
        import yfinance as yf
        news = yf.Ticker(ticker).news or []
        headlines = []
        for item in news[:5]:
            title = item.get("title") or item.get("content", {}).get("title", "")
            if title:
                headlines.append(f"  • {title}")
        hdl_str = "\n".join(headlines) if headlines else "  (no headlines)"
    except Exception:
        hdl_str = "  (headlines unavailable)"

    tone = "BULLISH" if compound > 0.15 else "BEARISH" if compound < -0.15 else "NEUTRAL"
    return (
        f"=== News Sentiment — {ticker} ===\n"
        f"VADER compound: {compound:+.3f}  →  {tone}\n"
        f"Recent headlines:\n{hdl_str}"
    )


def tool_get_price_history(ticker: str, period: str = "1y", **_) -> str:
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period=period)
        if hist is None or hist.empty:
            return f"No price history available for {ticker}."
        close = hist["Close"]
        current = float(close.iloc[-1])
        first = float(close.iloc[0])
        high = float(close.max())
        low = float(close.min())
        return_pct = (current - first) / first * 100
        return (
            f"=== Price History — {ticker} (period: {period}) ===\n"
            f"  Current:     ${current:,.2f}\n"
            f"  Period start: ${first:,.2f}\n"
            f"  Period high: ${high:,.2f}\n"
            f"  Period low:  ${low:,.2f}\n"
            f"  Return:      {return_pct:+.1f}%\n"
            f"  Data points: {len(close)}"
        )
    except Exception as exc:
        return f"Price fetch failed for {ticker}: {exc}"


def tool_get_key_stats(ticker: str, **_) -> str:
    """yfinance fundamentals snapshot."""
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        if not info:
            return f"No key stats available for {ticker}."
        lines = [f"=== Key Stats — {ticker} ==="]
        fields = [
            ("currentPrice", "Current Price", "${:,.2f}"),
            ("marketCap", "Market Cap", "${:,.0f}"),
            ("trailingPE", "P/E (TTM)", "{:.2f}"),
            ("forwardPE", "Forward P/E", "{:.2f}"),
            ("priceToBook", "P/B", "{:.2f}"),
            ("dividendYield", "Div Yield", "{:.2%}"),
            ("beta", "Beta", "{:.2f}"),
            ("profitMargins", "Profit Margin", "{:.1%}"),
            ("returnOnEquity", "ROE", "{:.1%}"),
            ("debtToEquity", "D/E", "{:.2f}"),
            ("fiftyTwoWeekHigh", "52W High", "${:,.2f}"),
            ("fiftyTwoWeekLow", "52W Low", "${:,.2f}"),
        ]
        for key, label, fmt in fields:
            val = info.get(key)
            if val is not None:
                try:
                    lines.append(f"  {label:<18} {fmt.format(val)}")
                except Exception:
                    lines.append(f"  {label:<18} {val}")
        return "\n".join(lines)
    except Exception as exc:
        return f"Key stats fetch failed for {ticker}: {exc}"


def tool_compute_bull_prob(ticker: str, **_) -> str:
    from ml.live_scorer import compute_bull_prob
    result = compute_bull_prob(ticker)
    return result.explanation


def tool_get_market_regime(**_) -> str:
    """
    Weinstein Chapter 8 — market-wide stage analysis.
    Returns SPY stage + momentum + breadth + playbook recommendation.
    """
    from ml.market_context import compute_market_regime, format_regime
    # Try to use any recent scan from session state if available (heuristic)
    regime = compute_market_regime()
    return format_regime(regime)


def tool_get_macro_context(**_) -> str:
    """Fetch current macro backdrop from FRED."""
    try:
        import requests
        series = {
            "FEDFUNDS": "Fed Funds Rate",
            "GS10": "10-Year Treasury",
            "CPIAUCSL": "CPI All Urban",
            "UNRATE": "Unemployment Rate",
        }
        lines = ["=== Macro Context (FRED) ==="]
        for sid, name in series.items():
            try:
                url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
                r = requests.get(url, timeout=8)
                rows = r.text.strip().split("\n")
                if len(rows) < 2:
                    continue
                last_two = rows[-2:]
                latest = last_two[-1].split(",")
                if len(latest) >= 2:
                    lines.append(f"  {name:<22}  {latest[0]}:  {latest[1]}")
            except Exception:
                continue
        return "\n".join(lines) if len(lines) > 1 else "Macro data unavailable."
    except Exception as exc:
        return f"Macro fetch failed: {exc}"


# ═══════════════════════════════════════════════════════════════════
# Claude tool schemas (shared base tools)
# ═══════════════════════════════════════════════════════════════════

BASE_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "get_income_statement",
        "description": (
            "Get the company's income statement from SEC XBRL filings for the last 3 fiscal years. "
            "Returns revenue, gross profit, operating income, net income, and EPS."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Stock ticker symbol (e.g., 'O', 'PLD', 'AAPL')"}
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_balance_sheet",
        "description": (
            "Get the company's balance sheet from SEC XBRL for the last 3 fiscal year-ends. "
            "Returns total assets, liabilities, debt, equity, and cash position."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"}
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_cash_flow",
        "description": (
            "Get the cash flow statement from SEC XBRL for the last 3 fiscal years. "
            "Returns operating, investing, financing cash flows, capex, dividends, and computed FCF."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"}
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "search_10k",
        "description": (
            "Semantic search over the company's latest 10-K filing. "
            "Use this to find specific topics — risk factors, revenue recognition, competitive threats, "
            "dividend policy, portfolio composition, etc. Returns top matching passages with section labels."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "query": {"type": "string", "description": "What to search for, e.g., 'interest rate risk', 'customer concentration'"},
                "top_k": {"type": "integer", "description": "Number of results to return (default 5)", "default": 5},
            },
            "required": ["ticker", "query"],
        },
    },
    {
        "name": "get_risk_factors",
        "description": "Get the full Item 1A Risk Factors section from the latest 10-K filing.",
        "input_schema": {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
        },
    },
    {
        "name": "get_mda",
        "description": "Get the Item 7 Management's Discussion and Analysis section from the latest 10-K.",
        "input_schema": {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
        },
    },
    {
        "name": "get_business_description",
        "description": "Get the Item 1 Business description from the latest 10-K — company overview, segments, strategy.",
        "input_schema": {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
        },
    },
    {
        "name": "get_sentiment",
        "description": "Get VADER news sentiment score + sample headlines for the ticker.",
        "input_schema": {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
        },
    },
    {
        "name": "get_price_history",
        "description": "Get recent price performance: current price, period high/low, return %.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "period": {"type": "string", "description": "e.g. '1mo', '3mo', '1y', '5y'", "default": "1y"},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_key_stats",
        "description": "Get key valuation and profitability stats from yfinance (P/E, P/B, ROE, dividend yield, etc.).",
        "input_schema": {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
        },
    },
    {
        "name": "compute_bull_prob",
        "description": (
            "Compute a live bull probability score (0-100%) combining price momentum, revenue growth, "
            "cash flow trend, news sentiment, and leverage delta. Returns the score plus feature breakdown."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
        },
    },
    {
        "name": "get_macro_context",
        "description": "Get current macro backdrop: Fed Funds rate, 10Y Treasury, CPI, unemployment.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_market_regime",
        "description": (
            "Weinstein Chapter 8 — market-wide stage analysis. "
            "Returns the S&P 500's current stage (1-4), 30-week MA position, "
            "50/200-day momentum, and breadth (if scan is available). "
            "Call this FIRST before recommending any long or short position — "
            "Weinstein's 'No Isolationism' rule says no stock is an island. "
            "The output includes a playbook action (e.g., BULL: favor Stage 2 longs, "
            "BEAR: cash is a position, BULL_FADING: trim, etc.)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
]


# ═══════════════════════════════════════════════════════════════════
# Tool registry: name → implementation
# ═══════════════════════════════════════════════════════════════════
BASE_TOOL_REGISTRY: dict[str, Callable[..., str]] = {
    "get_income_statement":     tool_get_income_statement,
    "get_balance_sheet":        tool_get_balance_sheet,
    "get_cash_flow":            tool_get_cash_flow,
    "search_10k":               tool_search_10k,
    "get_risk_factors":         tool_get_risk_factors,
    "get_mda":                  tool_get_mda,
    "get_business_description": tool_get_business_description,
    "get_sentiment":            tool_get_sentiment,
    "get_price_history":        tool_get_price_history,
    "get_key_stats":            tool_get_key_stats,
    "compute_bull_prob":        tool_compute_bull_prob,
    "get_macro_context":        tool_get_macro_context,
    "get_market_regime":        tool_get_market_regime,
}
