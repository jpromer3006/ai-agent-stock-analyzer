# Screenshots Guide — Report & Presentation Assets

This document lists every screenshot the team needs for the written
report and PowerPoint presentation, with exact navigation steps and
suggested captions.

**Target platform:** the live demo at
https://cells-gate-refused-stream.trycloudflare.com (or `localhost:8501`)

**Tip:** on macOS, `Cmd + Shift + 4` → drag-select the area → saves PNG
to Desktop.

---

## 📸 Screenshot List — in order of appearance in the deliverable

### 1. The landing page (hero shot)
- **When:** first thing after opening the app
- **What to capture:** the top banner + both mode toggles + landing state info
- **Caption (report):** *"The Ai-Agent Stock Analyzer dispatches each ticker to one of two purpose-built modes: Research (deep memo) or Trader (fast stage scan)."*
- **Caption (slide):** *"Two modes, one product — chosen by the user's decision horizon."*

### 2. Research Mode — ticker input with Run Agents button
- **When:** Research Mode landing with a ticker typed in (e.g. `O`)
- **What to capture:** search bar + Run Agents button + category emoji badge + "How it works" panel
- **Caption (report):** *"Users enter any ticker; the classifier auto-routes to the correct specialist agent. The universe spans 50 tickers across 8 categories."*
- **Caption (slide):** *"Natural language in, specialist agent out."*

### 3. Research Mode — Agents Working (live trace)
- **When:** while `Run Agents` is actively running on any ticker
- **What to capture:** the "🤖 Agents Working" expander with live tool calls streaming in
- **Caption (report):** *"The agent trace is streamed to the user. Every tool call — SEC XBRL, 10-K semantic search, market regime — is visible in real time."*
- **Caption (slide):** *"Transparency by design: see every tool call as it happens."*

### 4. Research Mode — completed memo with citations
- **When:** after agents finish (typically 45–90 seconds)
- **What to capture:** the "📄 Research Memo and Analysis" heading + first 3 sections with inline `[Source: SEC XBRL FY2025]` citations visible
- **Caption (report):** *"Every numerical claim is backed by an inline citation. The memo is post-generation validated — any response below 70% citation coverage is rejected."*
- **Caption (slide):** *"No hallucinations. Every number is traceable."*

### 5. Research Mode — 🔊 Listen button + audio player
- **When:** below the memo after clicking `🔊 Listen`
- **What to capture:** the embedded `<audio>` player with play controls
- **Caption (report):** *"ElevenLabs TTS converts any memo into audio. Cached per-message, instant replay."*
- **Caption (slide):** *"Hands-free analysis. Listen while you chart."*

### 6. Research Mode — follow-up chat panel
- **When:** at the bottom of Research Mode after memo completes
- **What to capture:** the "💬 Ask follow-ups about this memo" panel with one user question and one assistant answer
- **Caption (report):** *"Context-aware chat: the LLM sees the entire memo and the latest Trader scan, so follow-up questions stay grounded."*
- **Caption (slide):** *"Ask a question — it references the actual memo and scan data."*

### 7. Research Mode — Add to Top Picks button
- **When:** after a memo completes, near the Download Memo button
- **What to capture:** the "➕ Add X to Top Picks" button
- **Caption (report):** *"One-click workflow from deep research to the Trader scan list."*
- **Caption (slide):** *"Research → watchlist in one click."*

### 8. Trader Mode — Market Regime banner
- **When:** the top of Trader Mode (visible immediately)
- **What to capture:** the full banner: colored verdict card + SPY stage detail + playbook line
- **Caption (report):** *"Weinstein's Chapter 8 'No Isolationism' rule: the broad market's stage is checked before any individual recommendation. The banner shows SPY's current stage, momentum tailwind, breadth, and a concrete playbook action."*
- **Caption (slide):** *"No stock is an island. Market regime first."*

### 9. Trader Mode — Stage distribution cards
- **When:** after clicking Scan Now
- **What to capture:** the 4 colored cards (Basing / Advancing / Topping / Declining) with counts
- **Caption (report):** *"At a glance: how the scanned universe splits across Weinstein's four stages."*
- **Caption (slide):** *"50 tickers in, four buckets out."*

### 10. Trader Mode — Stage 2 Breakouts + Stage 4 Breakdowns
- **When:** the two leaderboards side-by-side after Scan Now
- **What to capture:** both columns in full, showing tickers + bull probabilities + BUY/SELL badges
- **Caption (report):** *"The core trader view: today's strong-buy and strong-sell candidates ranked by bull probability."*
- **Caption (slide):** *"Buy zone and sell zone — one glance."*

### 11. Trader Mode — Full Universe Scan table
- **When:** below the leaderboards
- **What to capture:** the sortable table with bull-probability progress bars
- **Caption (report):** *"All scanned tickers in one sortable table. Columns: Stage, Bull Probability, Action, Price, vs 30W MA, Slope, Relative Strength, Volume, % from 52W High."*
- **Caption (slide):** *"Every ticker, every signal, one sortable view."*

### 12. Trader Mode — Stage Detail chart (the "wow" shot)
- **When:** click any ticker in the leaderboard or full table
- **What to capture:** the chart with triangle markers + YOU ARE HERE circle + three horizontal trade-level lines + the pill strip below showing Entry/Stop/Target/R:R
- **Caption (report):** *"Hover-rich chart: green and red triangles mark MA crossings, the circle marks current position, and the horizontal lines show the Weinstein trade setup (entry via Buy-Stop, Stop at the 30-week MA, Target at a measured move)."*
- **Caption (slide):** *"One glance = a thousand earnings forecasts. — paraphrased from Weinstein."*

### 13. Trader Mode — Trade Setup card (Entry / Stop / Target / R:R tiles)
- **When:** same detail view, above the chart
- **What to capture:** the 4 colored metric tiles
- **Caption (report):** *"Every actionable setup surfaces a Buy-Stop trigger, a protective Stop-Loss at the 30-week MA (Weinstein's invalidator), a measured-move target, and the risk/reward ratio in one header row."*
- **Caption (slide):** *"Four numbers that tell you exactly how to trade it."*

### 14. Evaluation results (honest data)
- **When:** from the evaluation results in `evaluation/results/retrieval.json` — either render in the report or make a bar chart
- **What to include:** sector-by-sector hit rate (REIT 5/5, Tech 4/4, Healthcare 0/4, etc.), mean coverage 34.8%, mean relevance 0.331
- **Caption (report):** *"Evaluation on a 34-question golden set across 8 sectors. The framework surfaced real weaknesses — Healthcare retrieval at 0/4 reveals that pharma terminology doesn't embed cleanly against generic 10-K sections. This transparency is itself a deliverable."*
- **Caption (slide):** *"We measured ourselves honestly. REIT + Tech work great. Healthcare needs work. That's the point of evaluation."*

### 15. Architecture diagram (drawn asset, not a screenshot)
- **Source:** the ASCII diagram in README.md §2
- **What to do:** recreate it cleanly in a slide design tool (Keynote, PowerPoint, Figma, draw.io)
- **Caption (report):** *"The end-to-end pipeline: classifier routes to specialist agent → Claude tool-use loop → data layer (SEC EDGAR, yfinance, FRED, ChromaDB RAG) → citation validator → memo → optional audio + chat."*
- **Caption (slide):** *"The architecture, in one picture."*

### 16. GitHub repo homepage
- **When:** visit https://github.com/jpromer3006/ai-agent-stock-analyzer
- **What to capture:** the repo landing page with README visible + commit count + stars (if any)
- **Caption (report):** *"Open source under MIT license. Public repository includes full codebase, evaluation results, and documentation."*
- **Caption (slide):** *"Shipped on GitHub. Reproducible and auditable."*

---

## 🎨 Style tips for screenshots

- **Dark mode** — the Streamlit default is dark; keep it for consistency
- **Browser chrome** — include the URL bar in one or two "live demo" shots, crop it out of the rest
- **Zoom level** — set browser zoom to 100% for clean text
- **Resolution** — save as PNG at retina resolution (macOS does this by default)
- **File names** — use `NN_description.png` (e.g. `03_agents_working.png`) so sort order matches the report flow

---

## 📐 Report structure (for the Report Lead)

Use these screenshots in this order, mapped to Lecture 9's required sections:

| Report section | Screenshots |
|----------------|-------------|
| **1. Business Problem** | — (narrative only, maybe hero shot #1) |
| **2. Architecture** | #15 (diagram) |
| **3. Dataset** | — (narrative; cite universe table from tickers.py) |
| **4. Evaluation** | #14 (eval results) |
| **5. Limitations** | — (narrative) |
| **6. Next Steps** | — (narrative) |

Anything tutorial-like (memo screenshot, trader detail, etc.) goes in
an optional "Appendix: Demo Walkthrough" section if space permits.

---

## 🎤 Presentation flow (for the Presentation Lead)

~10 slides, ~10 minutes. Suggested order:

| # | Slide | Main visual |
|---|-------|-------------|
| 1 | Title + team names | — |
| 2 | Business problem | stock-ticker imagery or bullet list |
| 3 | Solution overview | #1 (hero) |
| 4 | Architecture | #15 (diagram) |
| 5 | Research demo | #3 (agents working) + #4 (memo) |
| 6 | Research extras (🔊 + chat) | #5 + #6 side-by-side |
| 7 | Trader demo 1 | #8 (regime banner) + #10 (leaderboards) |
| 8 | Trader demo 2 | #12 (detail chart) + #13 (trade setup card) |
| 9 | Evaluation results | #14 |
| 10 | Limitations + Next Steps | — |
| 11 | Thank you / Q&A | #16 (GitHub) |

**Live demo during the presentation:** JP handles it. The slides should
work as a standalone story even without the live demo — that's the
insurance policy.

---

*Last updated: April 2026*
