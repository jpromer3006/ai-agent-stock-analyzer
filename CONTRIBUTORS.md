# Contributors

The **Ai-Agent Stock Analyzer** was built as the Applied Finance Project
for AIGB 6317 — *Machine Learning and LLMs* — at Fordham's Gabelli School
of Business, Spring 2026.

---

## Team

### Project Lead

**JP Romero** ([@jpromer3006](https://github.com/jpromer3006))
- Overall architecture and engineering lead
- Research Mode — SEC EDGAR integration, 8 specialist agents, citation
  validator, RAG pipeline, evaluation harness
- Trader Mode — Weinstein Stage Analysis engine, batch scanner, trade
  setup computation, market regime (Ch. 8) indicator
- Assistant / chat layer with ElevenLabs voice integration
- Streamlit UI, Cloudflare tunnel deployment, launcher script

### Team Member 2

**[Teammate Name — please edit]**
- *[Role / contribution summary — please edit]*
- Examples: evaluation run, PowerPoint presentation lead, report editing,
  demo testing, usability feedback, specific module contributed

### Team Member 3

**[Teammate Name — please edit]**
- *[Role / contribution summary — please edit]*

---

## Faculty

**Dr. Andy Li, PhD, CFA, FRM** — *Lecture 9: Applied Finance Projects*
instructor and project sponsor. Provided the project rubric, the three
reference themes (SEC Filing Research Assistant, Portfolio Risk Monitor,
Earnings Call Analyst), and the commercialization framing.

**Dr. Yilu Zhou, PhD** — *Lectures in LLM and Generative AI*.

---

## Intellectual Property

This project is released under the [MIT License](LICENSE) — permissive,
commercial-use-friendly. Each contributor retains copyright on their own
contributions and grants permission under MIT to all downstream users
(including commercial derivatives).

**For team members**: you are free to showcase this repository on your
résumé, LinkedIn, and personal portfolio. You can fork it, modify it, and
use any portion in future work. The MIT License does the legal heavy
lifting — no separate agreement is required.

**For commercial derivatives**: the production SaaS version of this
platform is operated by **AdminAI LLC**. Any commercial release preserves
attribution to all contributors above in the product's "About" page.

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
  memo generation. Subject to Anthropic's API Terms of Service.
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

## How to Contribute

This is an academic project, not an open community (yet). If you have
suggestions or bug reports, open a GitHub issue. Substantial pull requests
are welcome but will be reviewed with care against the MIT license terms
and the educational goals of the original project.

---

*Last updated: April 2026*
