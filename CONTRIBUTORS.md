# Contributors

The **Ai-Agent Stock Analyzer** was built as the Applied Finance Project
for AIGB 6317 — *Machine Learning and LLMs* — at Fordham's Gabelli School
of Business, Spring 2026.

This section is written to accurately reflect who did what, in the spirit
of academic honesty and so that downstream users of this MIT-licensed code
have a clear picture of authorship.

---

## Team

### Sole Code Author & Project Architect

**JP Romero** ([@jpromer3006](https://github.com/jpromer3006))
Solo-built the entire codebase between the assignment of Lecture 9 and the
project deadline, in collaboration with an AI pair programmer
(Anthropic's Claude). Responsibilities included:

- Overall product architecture and system design
- Research Mode: SEC EDGAR integration (XBRL + 10-K text), 8 specialist
  agents, citation validator, semantic RAG pipeline, evaluation harness
  (34-question golden set)
- Trader Mode: Weinstein Stage Analysis engine, batch scanner, trade-setup
  computation, Chapter-8 market regime (SPY stage + breadth + momentum)
- Assistant / chat layer with plain-English reasoning and ElevenLabs
  voice integration
- Streamlit UI, Cloudflare tunnel deployment, `Launch.command` macOS
  one-click launcher
- Evaluation framework (retrieval precision, citation coverage,
  end-to-end answer quality)
- All git history, all commits, all documentation

### Report Lead

**[Teammate Name — to fill in after tomorrow's team meeting]**
- 2–3 page written report covering business problem, architecture,
  dataset, evaluation results, limitations, and next steps
  (per Lecture 9 rubric)
- Source material: this repository's `README.md`, `evaluation/results/`,
  and the working demo

### Presentation Lead

**[Teammate Name — to fill in after tomorrow's team meeting]**
- ~10-slide PowerPoint deck, ~10-minute talk
- Demo screenshots, architecture diagrams, evaluation table
- Source material: this repository and the live demo

---

## Faculty

**Dr. Andy Li, PhD, CFA, FRM** — *Lecture 9: Applied Finance Projects*
instructor and project sponsor. Provided the project rubric, the three
reference themes (SEC Filing Research Assistant, Portfolio Risk Monitor,
Earnings Call Analyst), and the commercialization framing.

**Dr. Yilu Zhou, PhD** — *Lectures in LLM and Generative AI*.

---

## Intellectual Property and Licensing

This project is released under the [MIT License](LICENSE) — permissive,
commercial-use-friendly.

- **Code authorship**: JP Romero is the sole author of all software in
  this repository. Any commercial derivative work is operated solely by
  **AdminAI LLC**.
- **Project deliverables**: The final written report and presentation
  are joint deliverables of the full three-person academic team. Their
  authors retain full rights to showcase those deliverables on résumés,
  portfolios, and LinkedIn.
- **Team members can**: showcase participation in the Applied Finance
  Project, link to this repository as a project they presented, share
  the written report and deck they authored.
- **Team members do not hold**: code copyright or equity in any
  commercial release (consistent with their actual contribution scope).

This division of labor is disclosed to Prof. Li per standard academic
honesty expectations.

---

## Third-Party Methodology and Tools

This project synthesizes, attributes, and extends the following sources:

### Methodology

- **Stan Weinstein** — *Secrets for Profiting in Bull and Bear Markets*
  (1988). The 4-stage analysis framework (Basing / Advancing / Topping /
  Declining), the 30-week moving average rule, Mansfield relative
  strength, buy-stop placement, and Chapter 8 market-regime indicators
  are all his. We implement his methodology mechanically; we do not
  claim authorship of the underlying analytical framework.

### Data Sources

- **SEC EDGAR** — U.S. Securities and Exchange Commission, public XBRL
  company facts API and 10-K/10-Q filing archive. Free public data.
  Attribution: `data.sec.gov`. User-Agent: `AdminAI Research admin@adminaillc.com`.
- **Yahoo Finance (via `yfinance`)** — price history, market cap, news
  headlines. Used under the open-source `yfinance` MIT license.
- **FRED** (Federal Reserve Bank of St. Louis) — macroeconomic data via
  the public CSV endpoint.

### AI / ML Libraries

- **Anthropic Claude** (claude-sonnet-4) — agent reasoning, tool use,
  memo generation, and AI pair-programming assistance during the build.
  Subject to Anthropic's API Terms of Service.
- **ElevenLabs** — text-to-speech via `eleven_turbo_v2_5`, default voice
  "Adam". Subject to ElevenLabs commercial terms.
- **sentence-transformers** (`all-MiniLM-L6-v2`) — embeddings for the
  RAG layer. Apache 2.0.
- **ChromaDB** — vector store. Apache 2.0.
- **VADER Sentiment** — lexicon-based sentiment analysis. MIT.

### Infrastructure

- **Streamlit** — UI framework. Apache 2.0.
- **Plotly** — interactive charts. MIT.
- **Cloudflare Tunnel** — quick public demo tunnel (free tier).
- **GitHub CLI** — repo creation and management. MIT.

---

## Transparency Note on AI-Assisted Development

The code in this repository was written by the author with significant
assistance from an AI pair programmer (Anthropic's Claude, Sonnet 4 model).
This is disclosed openly because:

1. It reflects the educational spirit of AIGB 6317 — the course is
   literally about applying LLMs to real-world problems.
2. Every architectural decision, methodology choice, evaluation strategy,
   and ethical guardrail was directed by the human author.
3. All commits are reviewed and tested by the human author before being
   merged.

This is consistent with how professional engineering teams increasingly
operate. It is not the AI's project; it is a human-led project that uses
AI as a tool, the same way a team uses IDEs, search engines, and Stack
Overflow.

---

## How to Contribute

This is an academic project, not an open community (yet). If you have
suggestions or bug reports, open a GitHub issue. Substantial pull requests
are welcome but will be reviewed with care against the MIT license terms
and the educational goals of the original project.

---

*Last updated: April 2026*
