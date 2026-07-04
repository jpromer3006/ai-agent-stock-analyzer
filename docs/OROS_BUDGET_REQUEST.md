# OROS - Applied Finance Project: Budget Request

Prepared for course funding review. OROS is a multi-strategy, book-equal agentic
stock-analysis platform with a quant-grade evaluation harness (point-in-time
backtest, Information Coefficient, transaction costs) and an ML layer.

## The one cost principle

Only LLM inference (the Anthropic API) consumes paid tokens. Everything
computationally heavy in OROS is free local compute and costs nothing per run:

- Scanning the full ~8,000-name US universe (yfinance, free)
- SEC EDGAR filings and XBRL fundamentals (free), FRED macro (free)
- All backtests, the Information Coefficient evaluation, walk-forward validation
- ML training: the learned router, regime discovery, and any time-series
  transformer fine-tuning (Chronos / PatchTST) run locally or on free Colab/Kaggle GPU
- News/sentiment via Alpha Vantage free tier and StockTwits (free)

The core product - scan the universe, score every book strategy (the agents),
consult market memory for which strategy fits the current state, then construct
and size the portfolio - is **100% free local compute, zero tokens**. Market
memory is a deterministic on-disk record of market state and per-strategy
performance that the portfolio engine reads to tilt strategy weights; it is NOT
LLM context and uses no tokens. A fine-tuned time-series transformer (Lecture 12)
also adds zero API cost. The LLM (Claude) is an OPTIONAL narrative layer on top -
turning the computed portfolio into a written memo - not the engine.

## Monthly cost estimate (Anthropic API only)

Event-driven design: the LLM runs cheap Haiku screening daily and deeper Sonnet
memos only when a name changes state, with prompt caching and the Batch API.

| Item | Basis | Monthly |
|---|---|---|
| Portfolio engine: scan + agents + memory + construction | free local compute | $0 |
| ML training, regime/analog, transformer fine-tuning | local or free Colab/Kaggle | $0 |
| Data (yfinance, SEC, FRED, AV free, StockTwits) | free | $0 |
| Optional: Haiku screening of borderline names | daily, cached | $4 - 6 |
| Optional: Sonnet narrative memos on finalists | event-driven, cached + batch | $8 - 20 |
| Optional: ad-hoc LLM analysis / experiments | buffer | $5 - 15 |
| Optional: ElevenLabs audio brief | free tier | $0 - 5 |
| **Core engine only (no LLM)** | | **~$0 / month** |
| **Typical (with optional LLM layer)** | | **~$25 - 45 / month** |
| **Heavy-use ceiling** | frequent Sonnet memos | **~$50 - 60 / month** |

Optional only if needed later: Alpha Vantage premium (~$50/month) for higher news
limits. Not required; the free tier covers the finalist-only usage.

## Against the proposed budget

Proposed: ~$100 from the department over 2 months, plus ~$30/month personal = about
**$160 over 2 months (~$80/month)**.

Estimated need: ~$25-45/month typical, ~$60/month at heavy use. So the proposed
budget comfortably covers the project with headroom for experimentation, a margin
for the optional AV premium, and slack for the fine-tuning and memory features,
which add capability without adding meaningful token cost.

## How the new features map to the course

- Fine-tuning a time-series transformer (Chronos / PatchTST) - Lecture 12. Runs as
  a peer strategy, zero-shot baseline first, optional fine-tune. No API tokens.
- Market memory (`ml/market_memory.py`) - a persistent record of market state and
  per-strategy performance that the portfolio engine reads to pick and weight
  strategies for the current state. Deterministic, zero tokens. This is the
  learning loop, not LLM context.
- Multi-agent framing - Lecture 11: each book strategy is an agent, the router is
  the orchestrator, with regime, risk, and news agents around them.
- Deliverables (Lecture 9): working demo, GitHub repo, presentation, and the 2-3
  page writeup (business problem, architecture, dataset, evaluation, limitations,
  next steps) - all already in scope, with a real evaluation section (IC, costs,
  point-in-time backtest) that exceeds the example projects.

All outputs are educational and not financial advice.
