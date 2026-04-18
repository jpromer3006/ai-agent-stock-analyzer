# Ai-Agent Stock Analyzer

> **Fordham Applied Finance Project — Machine Learning and LLMs (Lecture 9)**
> Multi-specialist agentic research platform built on the **SEC Filing AI Research Assistant** theme, extended with a sector-routing classifier so each stock is analyzed by the right specialist agent (REIT, Bank, Tech, Infrastructure, Energy, Consumer, Healthcare, Generic).

---

## 1. Business Problem

Sell-side research is expensive, narrow, and slow. A buy-side analyst covering a mid-cap stock typically needs to:

- Pull three financial statements from SEC EDGAR (income, balance sheet, cash flow)
- Read 100+ pages of a 10-K to extract risk factors, MD&A, business description
- Pull live price and sentiment from Yahoo Finance
- Cross-check against FRED macro data
- Synthesize everything into a coherent memo

This typically takes **4–8 hours per ticker**. At a 20-stock portfolio, keeping every name fresh is infeasible manually.

**Ai-Agent Stock Analyzer** automates this workflow end-to-end. An analyst types a ticker, and in under 2 minutes receives a research memo whose claims are **fully traceable** to SEC filings.

---

## 2. Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│                    Streamlit UI (ui/app.py)                        │
│         Ticker input · live agent trace · memo download            │
└────────────────────────────────────────────────────────────────────┘
                                ↓
┌────────────────────────────────────────────────────────────────────┐
│              Classifier (agents/classifier.py)                     │
│   yfinance sector + keyword rules + LLM fallback                   │
└────────────────────────────────────────────────────────────────────┘
                                ↓ routes to one of 8:
┌─────────┬─────────┬─────────┬─────────┬─────────┬─────────┬───────┐
│ 🏢 REIT │ 🏗️ Infra│ 🏦 Bank │ 💻 Tech │ ⚡ Energy│ 🛒Consmr│🏥+📊 │
└─────────┴─────────┴─────────┴─────────┴─────────┴─────────┴───────┘
                                ↓
┌────────────────────────────────────────────────────────────────────┐
│           Claude Tool-Use Loop (agents/orchestrator.py)            │
│   Autonomous multi-step reasoning with 12+ tools.                  │
│   Every response enforced through citation validator.              │
└────────────────────────────────────────────────────────────────────┘
                                ↓
┌────────────────────────────────────────────────────────────────────┐
│                    Data + Retrieval Layer                          │
│                                                                    │
│  SEC EDGAR           ChromaDB + sentence-transformers              │
│    • XBRL facts  →    • 10-K text chunked section-aware            │
│    • 10-K text        • all-MiniLM-L6-v2 embeddings                │
│                       • per-ticker collections                     │
│                                                                    │
│  yfinance · FRED · VADER sentiment · Live RF-style bull_prob        │
└────────────────────────────────────────────────────────────────────┘
                                ↓
┌────────────────────────────────────────────────────────────────────┐
│           Citation Validator (evaluation/citation_validator.py)    │
│     Rejects memos with < 70% inline citation coverage              │
└────────────────────────────────────────────────────────────────────┘
```

**Key design choices:**

1. **True agentic loop** — Claude decides which tool to call next, not a scripted pipeline
2. **Specialist routing** — REIT analysis ≠ bank analysis ≠ tech analysis; each has its own system prompt, memo sections, and custom tools
3. **Semantic RAG over 10-K** — ChromaDB + sentence-transformers, chunked respecting 10-K item boundaries
4. **Strict inline citations** — every dollar/percent/ratio must cite SEC XBRL, 10-K item, or yfinance
5. **Transparent scoring** — bull probability is a live composite of momentum, growth, sentiment, and leverage (no stale JSON)

---

## 3. Dataset

### SEC EDGAR (primary source)

- **XBRL company facts** — structured financials via `https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json`
- **10-K filings** — full text via `https://data.sec.gov/submissions/CIK{cik}.json`
- **Sections extracted**: Item 1 (Business), Item 1A (Risk Factors), Item 7 (MD&A), Items 2/3/7A/8
- Free, no API key required; rate-limited to 6 req/sec (below SEC's 10/sec cap)
- Cached to `data/cache/sec/xbrl/` and `data/cache/sec/filings/`

### Ticker universe (50 tickers across 8 categories)

| Category | Count | Examples |
|----------|-------|----------|
| REIT | 10 | O, PLD, AMT, SPG, EQIX, PSA, WELL, DLR, VICI, ARE |
| Infrastructure/EPC | 10 | PWR, EME, FLR, KBR, DY, APG, MTZ, PRIM, AGX, ORA |
| Bank | 5 | JPM, BAC, WFC, GS, MS |
| Tech | 5 | MSFT, GOOGL, META, CRM, NVDA |
| Energy | 5 | XOM, CVX, OXY, NEE, DUK |
| Consumer | 5 | WMT, KO, PG, MCD, COST |
| Healthcare | 5 | UNH, JNJ, LLY, PFE, ABBV |
| Generic | 5 | BRK-B, DIS, BA, CAT, GE |

Any ticker outside this universe is classified on the fly via yfinance sector/industry plus a Claude-based fallback.

### Supplementary sources

- **yfinance** — live price, market cap, key stats, news headlines
- **FRED** (public CSV, no key) — macro backdrop (Fed Funds, 10Y, CPI, unemployment)
- **VADER** — lexicon-based sentiment on Yahoo headlines

### Vector store

- **ChromaDB** PersistentClient at `data/chromadb/`
- One collection per ticker: `{ticker}_filings`
- Chunk size: 800 chars with 150-char overlap, respecting section boundaries
- Embeddings: `sentence-transformers/all-MiniLM-L6-v2`

---

## 4. Evaluation

Evaluation framework per Lecture 9 rubric. All results reproducible via the `evaluation/` module.

### Golden question set

`evaluation/golden_questions.json` — **34 analyst-style questions across 8 sectors**. Each question specifies:
- Expected tools the agent should call
- Expected 10-K sections the RAG should return
- Must-contain regex patterns (completeness check)
- Required citation count

### Retrieval quality (`evaluation/test_retrieval.py`)

Measures whether the RAG retrieves passages from the correct 10-K sections.

**Latest full-universe results (33 cases across 8 sectors):**

| Metric | Value |
|--------|-------|
| Cases matching ≥1 expected section | 10 / 23 (43%) |
| Mean section coverage | 34.8% |
| Mean relevance score | 0.331 |

**By sector:**

| Sector | Hit rate | Notes |
|--------|----------|-------|
| REIT (O) | 5/5 | Strong — 80% section coverage |
| Tech (MSFT) | 4/4 | Strong — 100% section coverage |
| Bank (JPM) | 3/4 | Capital ratio queries missed |
| Infra (PWR) | 2/4 | Backlog/customer concentration queries missed |
| Energy (XOM) | 2/4 | Commodity and regulatory queries missed |
| Consumer (WMT) | 2/4 | Same-store sales and supply chain missed |
| Healthcare (UNH) | 0/4 | ⚠️ All four queries failed to hit expected sections |
| Generic (BRK-B) | 2/4 | ⚠️ BRK-B 10-K section extraction fails (edge case) |

**Honest read:** the RAG works well for REIT and Tech (strong prose 10-Ks), struggles on Healthcare (dense pharma-specific language) and fails entirely on BRK-B (non-standard 10-K format). This is a known limitation documented in Section 5. Full results: [`evaluation/results/retrieval.json`](evaluation/results/retrieval.json)

### Answer quality (`evaluation/test_answers.py`)

End-to-end: runs the agent, then scores:
- **Completeness** — answer contains all required regex patterns
- **Citation coverage** — via `citation_validator.py`
- **Tool coverage** — agent called all expected tools
- **Length sanity** — ≥50 words

**Pass criteria (all must hold):**
- completeness ≥ 67%
- citation coverage ≥ 70%
- tool coverage ≥ 50%

**Baseline result (REIT, question reit-001):**
- Completeness: 100% · Citations: 75% · Tools: 50% · PASSED

### Citation validator

`evaluation/citation_validator.py` is a pure-regex post-processor that:
1. Extracts every dollar amount, percentage, and ratio
2. Verifies each has a citation marker within 150 chars
3. Returns coverage % and flags uncited claims

This is the **backbone** of Lecture 9's attribution requirement. Every memo can be audited.

### Running the evaluation suite

```bash
# Retrieval only (fast, no LLM calls)
PYTHONPATH=. python3 evaluation/test_retrieval.py --out results/retrieval.json

# Full answer eval (slower — one LLM run per question)
PYTHONPATH=. python3 evaluation/test_answers.py \
    --sectors REIT BANK TECH \
    --tickers-per-sector 1 \
    --questions-per-sector 2 \
    --out results/answers.json
```

---

## 5. Limitations

1. **XBRL scope** — only works for US-listed SEC filers. International ADRs may have incomplete XBRL tags.
2. **10-K section extraction is heuristic** — regex-based on "Item X" markers. Complex nested HTML or scanned filings may miss sections. **Known fail: BRK-B** (non-standard 10-K format).
3. **Healthcare sector retrieval is weak** — 0/4 hit rate on the golden questions. Pharma-specific terminology (pipeline, LOE, PDUFA) doesn't align well with generic 10-K section embeddings. Candidate fix: sector-specific query rewriting.
3. **No 10-Q support yet** — only annual 10-K filings are ingested. Quarterly changes not tracked.
4. **No year-over-year MD&A diff** — a key Lecture 9 example. On the roadmap.
5. **Bull probability is heuristic, not backtested** — composite of momentum + growth + sentiment + leverage. Transparent but not validated against historical returns.
6. **Citation coverage threshold is 70%, not 100%** — Claude reliably cites most but not all claims; perfect enforcement would require generation retries.
7. **Small golden test set** — 34 questions across 8 sectors. Would benefit from 3x expansion and inter-rater reliability scoring.
8. **No earnings call transcripts** — Lecture 9's third project theme (Earnings Call Analyst) not yet integrated.
9. **Chunking is section-aware but not table-aware** — XBRL tables in 10-K text may be fragmented by our chunker.
10. **ChromaDB collections grow unbounded** — no pruning strategy for outdated filings.

---

## 6. Next Steps

### Immediate (v1.1)

- Expand golden set to 60+ questions with ground-truth expected answers
- Generation retry loop if citation coverage < threshold
- Year-over-year MD&A diff tool (Lecture 9 example question)
- 10-Q filings ingestion alongside 10-K

### Near-term (v1.2)

- Earnings call transcript integration (Lecture 9 theme 1)
- Portfolio-level memo: enter 5–10 tickers, get weekly risk summary (Lecture 9 theme 2)
- Automated citation retry (regenerate until threshold met)
- Confidence score per memo section

### Research extensions

- Fine-tune sector-specific prompts using PDQI-9-style rubrics
- Replace VADER with FinBERT for financial sentiment
- Cross-encoder reranking on RAG results
- Hallucination detection via claim-to-source entailment model

---

## Quick start

```bash
# 1. Install
cd ~/Ai-Agent\ Stock\ Analyzer
pip install -r requirements.txt

# 2. Set Anthropic key (Mac Keychain is checked first, .env as fallback)
security add-generic-password -a "$USER" -s "ANTHROPIC_API_KEY" -w "sk-ant-..."

# 3. Run the UI
python3 -m streamlit run ui/app.py

# 4. Or test from the CLI
PYTHONPATH=. python3 agents/orchestrator.py O    # REIT — Realty Income
PYTHONPATH=. python3 agents/orchestrator.py JPM  # Bank
PYTHONPATH=. python3 agents/orchestrator.py NVDA # Tech
```

## Deliverables (Lecture 9 checklist)

- [x] Working demo (Streamlit + Cloudflare tunnel)
- [x] GitHub repo (public)
- [x] Business problem
- [x] Architecture diagram
- [x] Dataset documentation
- [x] Evaluation framework with golden questions
- [x] Limitations
- [x] Next steps
- [ ] Short presentation
- [ ] 2-3 page writeup (README covers this inline)

## Repo structure

```
Ai-Agent Stock Analyzer/
├── agents/
│   ├── orchestrator.py          # Claude tool-use loop
│   ├── classifier.py            # Ticker → StockCategory routing
│   ├── base_tools.py            # 12 shared tools
│   └── specialists/             # 8 sector-specific agents
│       └── _shared.py           # Shared citation rules (injected into all prompts)
├── data/
│   ├── sec_client.py            # SEC EDGAR XBRL + 10-K text
│   ├── tickers.py               # 50-ticker universe
│   └── cache/                   # SEC filings cache
├── rag/
│   ├── vector_store.py          # ChromaDB + sentence-transformers
│   └── chunker.py               # Section-aware 10-K chunking
├── ml/
│   └── live_scorer.py           # Live bull_prob composite
├── evaluation/
│   ├── golden_questions.json    # 34 Q&A golden set
│   ├── citation_validator.py    # Inline citation regex validator
│   ├── test_retrieval.py        # RAG retrieval eval
│   └── test_answers.py          # End-to-end answer quality eval
├── ui/
│   └── app.py                   # Streamlit UI with agent trace
├── requirements.txt
├── README.md                    # ← you are here
└── CLAUDE.md                    # Guidance for Claude Code when editing
```

---

**Course:** AIGB 6317 — Machine Learning and LLMs
**Lecture:** 9 — Applied Finance Projects (Andy Li, PhD, CFA, FRM)
**Student:** JP Romero, Fordham Gabelli School of Business
