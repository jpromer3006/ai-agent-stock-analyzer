# CLAUDE.md — Ai-Agent Stock Analyzer

Guidance for Claude Code when working in this repository.

## Project Overview

Multi-specialist agentic stock analysis platform. Routes each ticker to a specialist agent (REIT, Infrastructure, Bank, Tech, Energy, Consumer, Generic) which uses Claude's tool-use API to pull data and generate an adaptive research memo.

## Architecture

```
ui/app.py (Streamlit)
    ↓
agents/orchestrator.py  — user-facing entry point
    ↓
agents/classifier.py    — yfinance sector + LLM → StockCategory
    ↓
agents/specialists/*.py — REIT / Infra / Bank / Tech / Energy / Consumer / Generic
    ↓
[Claude tool-use loop calls agents/base_tools.py + specialist-specific tools]
    ↓
data/*.py — SEC EDGAR, FRED, sentiment, price, GEE clients
rag/vector_store.py — ChromaDB + sentence-transformers over 10-K text
ml/live_scorer.py — live bull_prob via RF
```

## Key design rules

1. **Agents own their tools.** Each specialist registers its own tool schema. The orchestrator passes these to `client.messages.create(tools=...)`.
2. **All structured financials from SEC XBRL** — not scraped PDFs. Fast and accurate.
3. **10-K text chunked section-aware** — respect Item 1, 1A, 7, 7A boundaries.
4. **One ChromaDB collection per ticker** — `{ticker}_filings`.
5. **bull_prob recomputed at session start** — not loaded from static JSON.
6. **Agent trace streamed to UI** — every tool call visible to user.

## Secrets

- `ANTHROPIC_API_KEY`: Mac Keychain first, `.env` fallback
- `FRED_API_KEY`, `ALPHA_VANTAGE_API_KEY`, `FMP_API_KEY`: `.env`
- GEE service account: `~/Documents/adminai_project/gee_service_account.json`

## Commands

```bash
# Run Streamlit
python3 -m streamlit run ui/app.py

# Test SEC client
PYTHONPATH=. python3 data/sec_client.py

# Test classifier
PYTHONPATH=. python3 agents/classifier.py

# Test a specialist end-to-end
PYTHONPATH=. python3 -c "from agents.orchestrator import analyze; print(analyze('O'))"
```

## Critical constraints

- **SEC requires User-Agent header** — set to `AdminAI Research admin@adminaillc.com`
- **SEC rate limit**: 10 req/sec max
- **ChromaDB persist_directory**: `data/chromadb/`
- **Cache SEC filings**: `data/cache/sec/xbrl/{ticker}.json` and `data/cache/sec/filings/{ticker}_10k_{year}.txt`
