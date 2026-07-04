# OROS Full Universe Rebuild - Architecture and Migration Plan

Date: 2026-06-18
Owner: JP
Status: Proposal for review, no production code changed yet

This plan turns OROS from a fixed 80 ticker analyzer into an unattended,
full universe research engine that scans every listed US equity, ranks the
field through a Weinstein anchored funnel grounded in a six book RAG, and
constructs a portfolio. It reflects four decisions you made: free data stack
(yfinance plus SEC files), full US listed universe near 8,000 names, Haiku
for screening and Sonnet for memos, and produce the plan plus rotate and wire
the keys.

---

## 1. Security and keys (do this first)

### 1.1 Live test results, June 18

| Key | Status | Note |
|---|---|---|
| ANTHROPIC_API_KEY | valid (200) | required, powers all reasoning |
| FRED_API_KEY | valid (200) | required, macro context |
| ALPHA_VANTAGE_API_KEY | valid, free tier | 25 req/day, 5/min hard cap |
| FMP_API_KEY | failing (403) | dead or downgraded, barely used |
| ELEVENLABS_API_KEY | valid (200) | optional, audio brief only |

### 1.2 Rotation runbook

Every key above appeared in a shared image, so treat all five as compromised
and rotate regardless of test status.

1. Rotate at each provider console: Anthropic, FRED, Alpha Vantage, ElevenLabs.
   Decide on FMP: either get a working key or remove it from `.env` since the
   code already pivoted to SEC XBRL and Alpha Vantage.
2. Put the new Anthropic key in the Mac Keychain, not the file:
   `security add-generic-password -a "$USER" -s "ANTHROPIC_API_KEY" -w "sk-ant-..."`
3. Put the rest in `.env` (already gitignored). Use the refreshed `.env.example`
   as the template.
4. Confirm with the health check: `PYTHONPATH=. python3 scripts/check_keys.py`.
   It verifies each credential against the live provider and prints status
   without ever exposing a secret. Exit code is zero only if every required
   key passes.
5. Scrub history: confirm `.env` was never committed
   (`git log --all --full-history -- .env`), and delete the key image from
   anywhere it was stored.

### 1.3 Keys this build actually needs

The free path needs no new paid market data key. It needs Anthropic (reasoning),
FRED (macro), and Alpha Vantage (earnings and news, used sparingly). SEC access
needs only a descriptive User-Agent string, not a key. ElevenLabs stays optional.
Placeholders for Tiingo, EODHD, and Polygon are pre wired in `.env.example` so a
paid bulk provider can be turned on later with no code rewrite if yfinance proves
too fragile at 8,000 names.

---

## 2. Root cause of the SPCX miss

OROS cannot recommend SPCX because it cannot see it. The investable set is two
hardcoded Python lists: an 80 ticker universe in `data/tickers.py` and a roughly
150 name momentum pool in `ml/momentum_screener.py`. A symbol absent from both is
never priced, never staged, never memoed. This is a coverage gap by construction,
not a ranking error. Any symbol level tuning leaves the blind spot in place.

The fix is a dynamic universe sourced from authoritative symbol lists, refreshed
on a schedule, with liquidity filters instead of a human curated list.

---

## 3. Target architecture: the funnel

The core principle is that the LLM never touches the whole universe. Cost and
quality both come from narrowing hard with cheap compute, then spending tokens
only on the survivors.

```
  Tier 0  Universe build      ~8,000 symbols   SEC + exchange files   0 tokens
            |  liquidity + data-quality filter
  Tier 1  Quant scan          ~3,000 ranked    pandas Weinstein math  0 tokens
            |  stage gates (Stage 2 breakouts, Stage 1->2 transitions)
  Tier 2  RAG screen          ~50 to 80        Haiku + 5-book RAG      cheap
            |  conviction score, framework agreement, red flags
  Tier 3  Specialist memo     ~10 to 20        Sonnet + tool use       moderate
            |  full adaptive research memo per finalist
  Tier 4  Portfolio build     1 portfolio      pandas optimizer        0 tokens
```

Each tier is independently cacheable and independently testable. A failure in
one tier degrades gracefully rather than breaking the run.

---

## 4. Data layer for a free 8,000 name scan

The make or break detail is that Weinstein stage analysis runs on weekly bars
and a 30 week moving average. Weekly bars change once a week. So we never pull
8,000 price histories on every run. We separate slow data from fast data, and the
fast momentum (speed two) capability rides on a daily cache, not a paid live feed.

Weekly refresh job (once per week, off hours):
* Build the symbol universe from SEC `company_tickers.json` plus the Nasdaq and
  NYSE listed files. This is the dynamic replacement for the hardcoded lists and
  the direct fix for SPCX.
* Apply a liquidity and quality filter: minimum price, minimum average dollar
  volume, real history length. This trims the raw 8,000 to a clean scannable set
  and removes the dead tickers that make a naive yfinance loop fail.
* Bulk download weekly OHLCV using batched yfinance calls with backoff, then
  cache to local parquet under `data/cache/prices/`. Batching plus caching is
  what keeps free data viable at this scale.

Daily refresh job (once per day, after the close):
* Bulk download daily OHLCV for the scannable set and cache it. Daily bars are
  about five times the weekly volume but still small at one pull a day.
* Run the daily breakout scan that powers fast momentum entries: a late Stage 2
  break of resistance on a volume surge with improving relative strength.

Intraday rechecks (a few times on trading days):
* Refresh delayed price and volume only for the breakout shortlist that passed
  the daily scan, never the whole universe, enabling near same day entries while
  staying inside free rate limits.

Resilience details that make the free path survive unattended: per symbol retry
with exponential backoff, a stale cache fallback so a throttled fetch reuses last
good data, a per run data quality report (symbols fetched, failed, stale), and a
single config flag to switch the price source to a paid provider if failure rates
climb. The paid fallback is plumbing only, off by default.

---

## 5. RAG expansion to the book set

Today `rag/` indexes Weinstein alone (`weinstein_book.py`, `chunker.py`,
`vector_store.py` over ChromaDB). You have added two PDFs in `Finance Books/`:
the technical analysis reference and Graham's Intelligent Investor. That is three.
Recommended additions, each chosen to fill a specific gap rather than to pile on:

* O'Neil, How to Make Money in Stocks (CANSLIM). Growth plus technical hybrid,
  pairs with Weinstein on breakout entries.
* Minervini, Trade Like a Stock Market Wizard (SEPA). Momentum and stage, the
  closest cousin to Weinstein, sharpens entry timing and risk.
* Lynch, One Up On Wall Street (GARP). Qualitative growth at a reasonable price,
  and his stock categories (slow growers, stalwarts, fast growers, cyclicals,
  turnarounds, asset plays) map onto the specialist agents. Best source for a
  long hold growth mandate, and it bridges Graham and O'Neil.

Final set, seven books: Weinstein, the technical analysis book, Graham, O'Neil,
Minervini, Lynch, plus How to Swing Trade (direct speed two breakout management).
Bogle was dropped (indexing thesis does not fit an active selection engine) and
How to Day Trade for a Living was removed (speed one intraday, out of scope). The
count is safe because retrieval is bounded (see cost scaling below) and because
the strategy router (section 6.1) scopes which books speak per mandate, so the
agent is never pulled in seven directions at once.

Whole corpus measured: about 6,800 chunks across the seven books. That is tiny
for a vector store. ChromaDB on its HNSW index handles hundreds of thousands of
vectors at millisecond latency, so no FAISS and no topic modeling are needed. See
section 5.1.

All PDFs live in one clean folder, `Finance Books/`, books only. The current loader
still reads the legacy `Weinstein /` folder, so that stays untouched until Phase 3
repoints the generalized loader at `Finance Books/`. One source needs preprocessing:
Trade Like a Stock Market Wizard arrived as a scanned image PDF (98 MB, almost no
text layer), so it is run through OCR (ocrmypdf) to produce a searchable PDF and a
text sidecar before ingestion.

Design changes:
* Generalize the loader from one Weinstein specific module to a books ingestion
  pipeline that accepts PDF, EPUB, and plain TXT, chunks each source section
  aware, and tags every chunk with book title and concept (stage, volume,
  relative strength, valuation, risk, sizing). EPUB is preferred where available
  because its text is cleaner than PDF and needs no OCR. DRM free files only;
  a DRM locked purchase (for example Kindle) cannot be ingested.
* One shared ChromaDB collection for the knowledge base, separate from the per
  ticker `{ticker}_filings` collections, so framework knowledge and company
  filings never bleed together.
* At Tier 2 and Tier 3, retrieval is concept scoped. A stage question pulls
  Weinstein and Minervini, a valuation question pulls Graham, and the agent cites
  which framework drove each call. That citation trail is what makes the output
  explainable and auditable, which matters for your ethics lens.

Cost scaling, verified against the current code: retrieval is bounded at
`top_k=5` over 800 character chunks, and embeddings run locally via
SentenceTransformers. So six books do not mean six times the context. Each
memo still injects only the top few chunks, about a thousand tokens, and that
slice caches at 90% off. Adding books raises quality and citation coverage, not
per memo token cost. The rule that preserves this is bounded retrieval: fixed k,
a hard cap on injected context, never dumping whole chapters. Indexing the books
themselves costs zero Anthropic tokens because the embedding model is local.
Source the PDFs legitimately and as selectable text, not scans, so they need no
OCR pass.

Reconciliation rule: when frameworks disagree (Graham says cheap, Weinstein says
Stage 4 downtrend), the agent surfaces the conflict rather than silently picking
one. Weinstein remains the timing foundation per the project charter, with the
others as confirming or cautioning context.

### 5.1 Why ChromaDB, not FAISS or topic modeling

The size that drives a vector store is chunk count, not PDF megabytes. The whole
seven book corpus measures about 6,800 chunks. ChromaDB, already in the stack,
runs an HNSW approximate nearest neighbor index that handles hundreds of thousands
to over a million vectors with millisecond queries, so 6,800 is trivial. FAISS
earns its place at the million plus vector scale or when GPU throughput is needed,
which is far beyond this corpus, so adopting it now would be premature optimization.
Even later, per ticker 10-K text lives in separate `{ticker}_filings` collections
queried one at a time, so the knowledge base never balloons. Topic modeling (LDA,
BERTopic) is a theme discovery tool, not a retrieval engine, so it cannot replace
the vector store. Its only optional role is auto clustering chunks into concept
tags to enrich the manual tags assigned at ingestion, a nice to have, not a need.

### 5.2 Visual chart matching: textbook charts versus live charts

The books are full of annotated example charts. Using them to recognize the same
setups on live charts is a separate multimodal capability, layered in three tiers,
and in every tier it confirms the numeric gates rather than triggering trades on
its own. Textbook charts are cherry picked perfect specimens and real charts are
messy, so vision is noisy and hindsight prone; the 30 week MA, relative strength,
and volume stay the ground truth.

Chart capture, verified per book (charts are stored differently, so capture method
differs, and OCR is never the tool for it). Weinstein holds 558 embedded raster
chart images, extracted directly and confirmed visually. The technical book (60),
How to Swing Trade (56), and Lynch (35) are likewise embedded raster, extracted the
same way. O'Neil draws its charts as vector graphics, so they are captured by
rendering the page to an image, also confirmed visually. Graham has one image, by
nature, it is a prose value book with almost no price charts. Minervini is a full
page scan, so each page image is itself the chart, with OCR adding the searchable
text. The Phase 7 figure extraction step applies this per book rule across all
seven so every chart is captured.

* Tier one, rules from text. OCR captures the figure captions and the prose that
  states each pattern as a rule (volatility contraction, volume drying into the
  apex then expanding on breakout). The RAG retrieves the rule and the agent checks
  it against live numeric data. No pixels needed. Ships first, free.
* Tier two, vision confirmation on finalists. Render the live chart with mplfinance
  and pass it to the multimodal model alongside an extracted textbook example,
  asking whether the live chart shows the same setup. Run only on the ten to twenty
  finalists so vision tokens stay inside the funnel.
* Tier three, learned chart classifier (optional research module). Extract the
  annotated chart crops as labeled exemplars, then either embed textbook crops and
  live renders into a shared CLIP style vision space for nearest neighbor matching,
  or fine tune a small CNN. Data volume is the constraint, dozens to low hundreds
  of examples, so few shot matching or programmatic and GAN based augmentation is
  the realistic route. This is where the project's CNN and GAN tooling fits.

---

## 6. Portfolio construction over the full universe

New module, for example `ml/portfolio_builder.py`, that turns ranked finalists
into a constructed portfolio rather than a flat list.

Inputs: Tier 3 finalists with conviction scores, stage, relative strength,
proposed stop, and sector. Logic:
* Eligibility: Stage 2 or confirmed Stage 1 to 2 transition only, consistent with
  the buy timing rules. No new longs in Stage 3 or 4.
* Position sizing from risk, reusing `ml/position_sizer.py`: size each name so
  the distance from entry to stop is a fixed fraction of portfolio risk, so a
  wider stop yields a smaller position.
* Diversification caps: per name and per sector ceilings so one hot group cannot
  dominate, with sector strength from `ml/sector_rs.py` as a tilt.
* Cash discipline: when too few names clear the gates, the remainder stays in
  cash. Capital preservation is an allowed and expected output, not a failure.
* Output: target weights, share counts for a stated account size, per position
  stop, portfolio heat, and a plain language rationale per holding.

This is deterministic pandas math, zero tokens, and fully unit testable.

---

## 6.1 Strategy router: one engine, many mandates

Scope decision, confirmed across three speeds:

* Speed one, true intraday day trading (multiple round trips a session, flat by
  close, order flow). Out of scope. Needs a paid real time tick feed and a
  literature the book set does not contain.
* Speed two, fast momentum swing. Enter on a confirmed late Stage 2 breakout,
  hold one day to a few days, sometimes sell into strength the same day. This is
  the SPCX case. It runs on free daily bars, with Weinstein as the stage filter
  and Minervini and O'Neil as the breakout playbook. In scope, and it is the one
  capability the original weekly only design was missing.
* Speed three, weekly position swing and long hold. The original design.

To support speed two the scan runs at two resolutions: the weekly Weinstein scan
for stage, plus a daily bar breakout scan over the universe refreshed after the
close. For near same day entries the system re checks only the breakout shortlist
a few times a day on roughly fifteen minute delayed quotes, which stays inside
free limits because it is the shortlist, not the universe. Daily bars are noisier
than weekly, so the daily layer leans harder on the volume, relative strength, and
stage gates and on tighter stops to control false signals.

Rather than blend all six books on every decision, the router selects a mandate
when a portfolio is created, maps it to an archetype, and scopes the active books,
gates, and sizing to that archetype. This is what makes a larger library an asset
instead of a source of contradictions.

Mandate intake (one time, then autonomous): on portfolio creation the user
answers a short intake, horizon, risk tolerance, goal, optional constraints and
account size. The system stores the mandate and runs it hands off thereafter. A
saved risk profile can also drive it with no intake if you prefer fully silent
setup.

Archetype map:

| Archetype | Horizon | Data resolution | Primary books | Gate emphasis | Sizing |
|---|---|---|---|---|---|
| Fast momentum swing | 1 day to a few days | Daily, intraday shortlist recheck | Minervini, O'Neil, Weinstein, technical book | Late Stage 2 breakout, volume surge, RS | Risk based, tight stops |
| Swing momentum | Days to weeks | Weekly plus daily | Weinstein, Minervini, O'Neil, technical book | Stage 2 breakout, relative strength, volume | Risk based stops |
| Growth hold | Medium to long | Weekly | Lynch, O'Neil | Earnings and revenue growth, GARP | Moderate |
| Value hold | Long | Weekly | Graham, Lynch | Margin of safety, quality, valuation | Conservative |

Always on overlay: Graham for a margin of safety and risk check applied to every
archetype, with cost and turnover discipline as a portfolio level rule. Weinstein
remains the stage and timing foundation for every active sleeve per the project
charter.

Mechanically the router sets three things per run: which book tags are in scope for
RAG retrieval, which gate set from `ml/` executes, and which sizing rule the
portfolio builder applies. Conflicts within an archetype are rare because only the
scoped books speak; any cross framework conflict is surfaced, not hidden, per the
reconciliation rule. This keeps the system explainable, every holding traces to a
mandate, an archetype, and the specific framework that justified it.

---

## 7. Default operating model: event driven, not clock driven

Goal number one is minimal human involvement, and the cost work in section 8
showed that brute forcing Sonnet on a fixed clock pays repeatedly for analysis
of data that has not changed. The default operating model resolves both at once
by tying spend to events, not to a schedule. Nobody is ever in the loop. The
trade off is freshness versus cost, never human effort, because a cron that
fires on an event is exactly as hands off as one that fires on a clock.

The system runs in three always on layers:

* Layer A, free quant scan, dual resolution. The weekly pass rebuilds stage from
  cached weekly history across the whole universe. A daily pass after the close
  runs the breakout scan that catches fast momentum (speed two) the same day,
  and a few intraday rechecks on the breakout shortlist using delayed quotes
  allow near same day entries. Zero tokens. This is what discovers a fresh name
  like SPCX the day it breaks out.
* Layer B, Haiku delta triage, daily. Looks only at names that newly crossed a
  gate since the previous scan, not the whole shortlist. A handful of names at
  roughly 12k tokens each, pennies per day.
* Layer C, Sonnet deep memo, event triggered. Fires automatically the moment a
  name crosses the Stage 2 gate, on whatever day that happens, and stays silent
  when nothing changes. Because Weinstein holds that only five to ten percent of
  stocks have a setup at any time, genuine new breakouts arrive a few at a time,
  so this layer is naturally cheap. You pay for events, not for a clock.

Housekeeping: a weekly off hours job rebuilds the dynamic universe and the weekly
price cache, and a daily after close job refreshes the daily bar cache. Daily bars
are roughly five times the volume of weekly but still small and pulled once a day,
so the free data path stays inside its rate limits.

Autonomy guardrails:

* Deferring budget cap, not a blocking one. When a daily spend ceiling is hit,
  remaining memos are queued for the next automatic cycle and logged. The system
  never pauses to ask you for approval, so the balance is protected without
  putting a human in the loop.
* Propose and journal only. The system writes dated reports and a proposed
  portfolio to `data/journal/`. It never places trades or moves money.
* Optional manual override: a single on demand button to force a same day deep
  memo on a chosen name. This is the only human touch point and it is opt in,
  used when you deliberately want to spend on a hot name now. It is not part of
  the autonomous loop.
* Notification: each run emits a brief, optionally voiced through the existing
  ElevenLabs path, so you review conclusions rather than operate the machine.

In this Cowork session the same layers can run through scheduled tasks as a
lightweight alternative to system cron.

---

## 8. Model tiering and token budget

Per your choice, Haiku screens and Sonnet writes memos. The classifier can stay
on Haiku. Indicative envelope at three runs per day:

| Tier | Names per run | Tokens each | Per run | Per day (x3) |
|---|---|---|---|---|
| 1 quant scan | ~3,000 | 0 | 0 | 0 |
| 2 RAG screen (Haiku) | ~60 | ~12k | ~720k | ~2.2M |
| 3 memo (Sonnet) | ~15 | ~60k | ~900k | ~2.7M |

That table is the brute force ceiling, three full Sonnet runs a day, roughly $15
a day at $3 per million input and $15 per million output. The event driven model
in section 7 is the default precisely because most of that ceiling is wasted
spend on unchanged weekly data.

Cost levers, in order of impact:

* Event driven Tier 3. Memos fire only on a stage change, not on a clock, so on
  a quiet day Tier 3 costs nothing.
* Batch API. Scheduled scans are not latency sensitive, so they run through the
  Message Batches API at 50% off every model.
* Prompt caching. The static 10-K and book context is the bulk of memo input and
  caches at 90% off, so repeat runs reread cache, not full text.
* Tiering. Haiku for screening and triage, Sonnet only for the finalist memos.
  The same workload on a frontier model would cost several times more for little
  gain, which is why tiering, not volume, is the control.

Realistic blended budget under the event driven model: daily free scan plus
Haiku triage near $0.15 a day, and Sonnet memos a few dollars per weekly burst
of breakouts, landing around $10 to $20 a month while still scanning the full
universe every day. On the current $32 Anthropic balance that is months of
runway, not days.

Budget governor: the token accounting hook enforces a daily spend ceiling and
defers rather than blocks. When the ceiling is reached, remaining memos queue for
the next automatic cycle and the event is logged, so a runaway loop can never
drain the balance and no human approval is ever required.

Instrumentation: the API returns input and output token counts on every call.
Wire a token accounting hook that logs per ticker and per tier usage into the
journal so every run reports its own cost. This makes the budget observable from
day one and answers your opening question, how many tokens are we using, with
real numbers rather than estimates.

---

## 9. UI changes

The product shifts from one ticker in, one memo out toward a portfolio cockpit.

* New Universe and Scan view: the dated Tier 1 leaderboard, stage distribution,
  fresh Stage 2 breakouts including newly discovered names like SPCX, and data
  quality for the run.
* New Portfolio view: proposed holdings, weights, stops, portfolio heat, sector
  exposure, and the cash sleeve, with the per holding rationale.
* Existing single ticker research and chat remain as a drill down from any name
  in the scan or portfolio.
* Token and cost panel: per run usage from the accounting hook, so spend is
  visible in the UI.
* The educational and not financial advice disclaimer stays prominent on every
  surface that proposes positions.

---

## 10. Risks and honest mitigations

* yfinance at 8,000 names is the top risk. Mitigation: weekly batched cache, not
  per run pulls, plus backoff, stale fallback, and a flip to a paid provider held
  in reserve. Expect to revisit this once real failure rates are measured.
* Survivorship and delisting noise in a full universe. Mitigation: liquidity and
  history length filters at Tier 0, and a data quality report each run.
* Free Alpha Vantage at 25 per day cannot cover earnings for thousands of names.
  Mitigation: query earnings only for the small shortlist, cache 24 hours, fall
  back to yfinance dates.
* Token cost creep if Tier 3 width grows. Mitigation: hard cap finalist count and
  watch the per run token report.
* Over automation risk: an unattended system proposing positions must never act.
  Mitigation: propose and journal only, no order placement, no fund movement,
  human in the loop for any real trade.

---

## 11. Phased migration roadmap

Phase 0, now: keys rotated and verified, `.env.example` and health check in
place, FMP retired from code and the user facing disclaimer. Cost hardening
specified as the foundation: event driven cadence, Batch API, prompt caching,
and a deferring budget governor wired into the token accounting hook.

Phase 1 (BUILT, verified 2026-06-18): `data/universe.py` builds the universe from
Nasdaq Trader listing files plus the SEC CIK map, with a balanced liquidity filter.
Self-test result: 11,528 clean symbols (5,357 ETFs, 6,330 with SEC CIK) across
NASDAQ, NYSE, NYSE Arca, NYSE American, and Cboe BZX. SPCX now resolves to Space
Exploration Technologies Corp Class A common stock (NASDAQ, CIK 0001181412) and
passes the liquidity filter, so the root-cause coverage gap is closed. Additive
module: `tickers.py` categories and all specialist imports remain untouched.
Branch: `feature/dynamic-universe`.

Phase 2 (BUILT, verified 2026-06-18): `data/price_cache.py` caches daily OHLCV to
parquet in batches with retry, stale fallback, and a data-quality report, and
`ml/universe_scanner.py` scans from that cache by feeding cached bars into the
existing `analyze_stage(df=...)`, reusing 100% of the Weinstein math and the
`ScanReport` type. Sample self-test (25 names): 24 of 25 staged cleanly, full
distribution (6/11/2/5 across stages 1 to 4) and a ranked Stage 2 leaderboard.
Both modules are additive; `batch_scanner` and `stage_analyzer` were not modified.

New-listing finding: SPCX is now visible and scanned, but it IPO'd 2026-06-12 with
only 5 trading days, so Weinstein cannot compute a 30-week MA and correctly reports
"insufficient history" rather than silently dropping it. Pure stage analysis will
never stage a week-old stock by design. Catching IPO-era opportunities like SPCX
needs a dedicated new-listing / IPO-momentum track driven by O'Neil (the N in
CANSLIM, new-issue bases) and Minervini on daily bars, not the 30-week MA. See the
open item in section 12.

Phase 3 (RAG + router BUILT 2026-06-18; index build + Haiku screen pending):
`rag/books.py` generalizes ingestion to all seven books (PDF/EPUB/TXT, prefers an
OCR .txt sidecar for the scanned Minervini), section-aware chunks, concept- and
book-tags every chunk, and serves concept-scoped retrieval from a shared
`finance_books` ChromaDB collection. `rag/strategy_router.py` maps a mandate plus
the market regime to active books, concepts, weighted strategy blend, and a
max-invested cap. Verified: text pipeline tags correctly (Weinstein 844, technical
337, Graham 2432 chunks with concept tags); router tilts weights by regime (bull
favors momentum/IPO at 100% invested, bear favors Weinstein/value at 30%).
Remaining, to run on the app machine: one-time index build
`PYTHONPATH=. python3 rag/books.py --ingest` (needs chromadb + sentence-transformers,
already in requirements), and the Haiku Tier 2 screen, which needs the rotated
Anthropic key and is wired in with the portfolio engine.

Phase 4: Sonnet Tier 3 memos on finalists, with the token accounting hook.

Phase 5 (ENGINE BUILT 2026-06-18; UI pending): `ml/portfolio_engine.py` is the
integration spine. It detects the market regime (SPY stage -> bull/neutral/bear),
asks the router for the regime-tilted strategy blend and deploy cap, scores every
name across Weinstein, momentum, and IPO on one 0..1 scale (value/growth slots
wired but idle until the book index + key are live), then selects a concentrated
10-15 name portfolio with risk-based sizing, single-name and sector caps, and a
regime-set cash sleeve. Verified on the cached sample: BULL regime, 11 holdings,
95% invested, AI-compute sector correctly capped at 3, benchmark ETFs excluded.
Output is one portfolio with a per-holding rationale, not separate lists. Keys
rotated by the user and re-verified 2026-06-18 (all required pass; FMP retired).
Open tuning item: risk-based sizing gives the widest-stop names the smallest
weight (AMD highest conviction but ~2% size), a conviction/size inversion to tune.
UI (BUILT): `ui/components/portfolio_mode.py` plus a third "Portfolio" tab in
`ui/app.py` (additive, Research and Trader untouched). It shows the regime banner,
blend weights, deploy cap, the holdings table with conviction, shares, weight,
entry, stop, and risk, plus invested/cash metrics and per-holding rationale. A
"Build portfolio" button refreshes the cache for the chosen candidate source
(scan list or dynamic-universe subset) and constructs. This replaces the
discern-the-lists UX with one portfolio screen.

Phase 5b (BUILT 2026-06-18): news, catalyst, and macro layer, `ml/news_signals.py`.
API-first, never scraped. SEC 8-K typed catalysts (item codes classify earnings
2.02, M&A 2.01/1.01, exec 5.02, and red flags like bankruptcy/delisting/auditor
change), Alpha Vantage NEWS_SENTIMENT (scored, relevance-weighted, free-tier so
finalist-only and cached) with a VADER/yfinance fallback, the VIX as the regime
dial, and optional low-weight StockTwits attention/froth. Reddit dropped (blocks
unauthenticated access). Wired into the engine: `detect_regime` now applies a VIX
override on top of SPY stage; finalists are enriched so news sentiment tilts the
quant score, hard red flags drop the name, imminent-earnings names are half-sized,
and one-sided social froth is faded. Verified: VIX read, NVDA AV news +0.21 with
8-K earnings catalysts and a froth flag, froth penalty visibly lowered ENPH/ANET/
NVDA/FSLR conviction. UI shows News and Flags columns plus AV/social toggles.
Rule held throughout: news flags, routes, and sizes risk; the breakout still
triggers.

Phase 5c (BUILT 2026-06-18) - book-equal strategy registry. `ml/strategy_registry.py`
makes every book a peer strategy behind one `score(ctx) -> StrategyScore` interface,
registered and interchangeable. Nothing is hard-coded as a foundation: Weinstein is
the `weinstein` key alongside Minervini SEPA, O'Neil CANSLIM, the technical book,
swing, Graham value, Lynch GARP, and the IPO track. Quant strategies score from
price; value/growth score from fundamentals (yfinance, shortlist-only). The router
(`rag/strategy_router.py`) now weights individual strategy keys per regime, so the
market decides which strategies lead. The portfolio engine blends all of them in
two passes (price strategies over all names, fundamentals on the shortlist), and
eligibility is "any strategy likes it", not Weinstein Stage 2. Verified: in a bull
regime SEPA outweighs Weinstein, and JPM (Weinstein 0.0, Stage 1) is held because
swing/technical/SEPA outvoted it. This formally supersedes the original
Weinstein-foundation charter; the project is now book-agnostic.

Phase 5d (BUILT 2026-06-18) - backtest harness, `evaluation/backtest.py`. Rolls the
as-of date with no lookahead, scores with the registry's price strategies, routes by
the regime at that date, holds picks a fixed horizon, and reports hit rate and return
per leading strategy and per regime versus SPY. Demo (12 names, 8 months, 40 trades):
avg +5.0% vs SPY +1.8%, but median +0.4% and hit 52.5%, so the edge was outlier-driven;
SEPA (35 of 40 picks) had a negative median, and Weinstein's strong number was n=5 noise.
This is the tool to tune strategy weights with evidence. Limits: small universe, short
period, no costs, survivorship bias, no news layer, fundamentals (value/growth) excluded
because the source is current-only. Enhancement: point-in-time fundamentals from SEC XBRL
(historical filings) would let value/growth be backtested honestly.

Phase 5e (BUILT 2026-06-18): backtest realism + point-in-time fundamentals.
`evaluation/backtest.py` now deducts round-trip transaction costs (cost_bps param,
returns net, SPY is the buy-and-hold baseline). `ml/fundamentals_pit.py` computes
as-of P/E and P/B from SEC XBRL using only values filed by the as-of date (verified:
AAPL P/E 32.5 in June 2025 vs 39.9 today), and `ScoringContext(as_of=...)` feeds
these into the Graham/Lynch strategies so value can be backtested without lookahead.
Next: TTM EPS and XBRL growth so Lynch GARP is fully point-in-time.

Phase 5f (BUILT 2026-06-18) - two-layer learned meta-model, `ml/meta_model.py`.
Layer 1 unsupervised regime discovery (numpy KMeans; upgrade to GMM/HMM via hmmlearn).
Layer 2 supervised learned router (numpy ridge mapping strategy scores -> forward net
return; upgrade to TabPFN or gradient-boosted on the Mac). Walk-forward, net of costs,
book-blend baseline. Demo (156 rows, 8 OOS dates): book +5.26%, learned router +3.04%,
learned+regime +4.02%, all 75% hit. HONEST RESULT: the books beat the ML on this tiny
sample, as expected at this data size; the value is the working pipeline. Real verdict
needs the full dataset (wide universe, multi-year) built on the Mac, caches to
data/cache/meta/dataset.parquet, then swap in TabPFN/HMM. Overfitting is the failure
mode; out-of-sample only, the harness is the judge, not in-sample fit.

Phase 5g (BUILT 2026-06-18) - quant-grade features + IC evaluation, `ml/meta_features.py`.
Adds classic cross-sectional factors (12-1 momentum, 60d volatility, beta, dollar-volume
liquidity) beside the book scores, z-scores features WITHIN each date, conditions on FRED
macro (yield-curve slope DGS10-DGS2, HY credit spread), labels on forward EXCESS return
(market-neutral), and evaluates by Information Coefficient (rank corr), IC t-stat, and the
long-short tercile spread, walk-forward. Demo (182 rows, 13 dates, 14 names): book signal
mean IC 0.062, LS spread +5.5%, but IC_t only 0.53 (n=7) so NOT significant; the learned
model went negative (-0.069), overfitting on tiny data again. Takeaway for review: method
is now defensible; significance needs breadth and length. Per Grinold's Fundamental Law
(IR ~ IC x sqrt(breadth)), a hundreds-of-names universe over years is required to power the
test - the Mac run. Survivorship bias (current-listing universe) remains the stated caveat.

Phase 5h (BUILT 2026-06-18) - market-state analog engine, `ml/market_analog.py`.
Describes the current market as a state vector (avg volatility, breadth, yield slope,
credit spread), finds the k nearest PAST states, and reports each book strategy's
Information Coefficient in those analog periods - an interpretable, regime-conditional
"today looks like these dates, and strategy X led then" signal. News sentiment may
enrich the live state only (LLM lookahead forbids it in history). Must be walk-forward
validated: does analog-tilted weighting beat static weighting OOS?

Phase 5i (BUILT 2026-06-18) - market memory wired into construction.
`ml/market_memory.py` is a persistent, deterministic record of market state and
per-strategy performance; it seeds from history and grows each run. The portfolio
engine now computes the current market state, calls `memory_tilt(state)`, and nudges
the router's strategy weights by what worked in similar past states before selecting
and sizing. Verified: in the current state memory boosted SEPA (0.22 -> 0.30) and cut
swing (0.12 -> 0.08). Zero tokens. This closes the loop: state -> memory -> weights ->
portfolio, and the system self-adjusts as outcomes accumulate. The LLM remains an
optional narrative layer, not the engine.

Phase 5j (BUILT 2026-06-18) - sector classifier + tab integration.
`data/sectors.py` resolves a sector for any name from its SEC SIC code (coarse
GICS-like mapping), with a yfinance fallback, cached. Wired into the engine's
shortlist enrichment so the dynamic universe no longer shows UNKNOWN and the
diversification cap works (verified: semiconductors capped at 3, book spread
across 5 sectors). The Portfolio now auto-saves its holdings to a "Portfolio
Holdings" scan list, so the Trader and Research tabs can analyze the constructed
book - the two tabs are connected. Deterministic, no LLM. Note: a few instrument
SIC codes (e.g. 3825) misclassify; refine the SIC map if needed.

Phase 5k (BUILT 2026-06-18) - relevance + ordering + UI fixes. (1) Liquidity gate
in the engine (min $5 price, min $5M 20d dollar volume) drops penny/illiquid names
so the book holds investable stocks, not noise. (2) The dynamic universe now puts
the liquid large-cap core first (S&P 100 + sector leaders) so the cap captures real
names (NVDA, AMD, AAPL) instead of just the alphabetical A-N slice. (3) Verified: a
mixed sample now yields a clean book across 6 sectors with the semis cap biting,
all recognizable names, real sectors. (4) UI tab order changed to Portfolio, Trader,
Research. Note: the proper full-universe ordering by liquidity needs the nightly
cache (Phase 6) to rank all names; the core-first heuristic is the interim fix.

Phase 5l (BUILT 2026-06-18) - explicit multi-agent orchestration, `ml/agent_orchestrator.py`.
Named agents each own one responsibility (DataAgent, RegimeAgent, RouterAgent, MemoryAgent,
ConstructionAgent, RiskAgent) sharing an OrchestrationContext; the Orchestrator runs them in
order and records an agent trace. It delegates heavy scoring/sizing to the tested
`build_portfolio` (now accepts precomputed regime + weights, no recompute), so behavior is
unchanged - this is a coordination and transparency layer. The Portfolio UI calls
`run_orchestrated` and shows the agent trace ("who did what"), satisfying the project's
agent-trace design rule and the Lecture 11 multi-agent framing. Verified trace: DataAgent
(15 names) -> RegimeAgent (BULL) -> RouterAgent (base weights) -> MemoryAgent (tilt) ->
ConstructionAgent (8 holdings) -> RiskAgent (within limits). Adding a new agent is now a
one-class change.

PRE-PHASE-6 HARDENING CHECKLIST (do before unattended operation):
1. Validation: purged + embargoed walk-forward (overlapping 20d horizons leak labels);
   deflate metrics for multiple testing; no claim until IC_t > ~2.
2. Data integrity: state and, where possible, fix survivorship bias (point-in-time
   universe); verify split/dividend adjustments across the universe.
3. Portfolio risk: covariance/correlation-aware construction (sector caps miss correlated
   names), portfolio vol target, turnover measurement, drawdown circuit-breaker / kill-switch.
4. Ethical gate: edge is NOT yet statistically proven; paper-trade forward and journal
   outcomes before any real capital. Propose-and-journal-only stays enforced.
5. Ops: data-quality gating before acting, deferring budget governor, failure alerts,
   reproducibility (pinned seeds, dated data snapshots).

Phase 8 (BUILT 2026-06-22) - data collection warehouse for training.
`ml/data_collector.py` snapshots, each run, a point-in-time feature row per scanned
name (the 8 strategy scores, classic factors, sector, liquidity, market state) into a
growing `data/warehouse/snapshots.parquet`, stamped with the date. `label_warehouse`
joins forward returns once the horizon elapses, producing labeled training examples.
This captures signals that cannot be backfilled (news, froth, regime) and is
survivorship-bias-free (a name is recorded even if it later delists). Wired into the
nightly runner so the corpus grows daily; the brief reports row/label counts. This is
the training set for the learned router and the time-series transformer. Verified: 46
feature rows collected in a test run.

Small-cap note: small caps are a legitimate return source. The liquidity slider lets
the user include them; the warehouse + IC analysis then decide empirically whether
those picks delivered, rather than by opinion.

Phase 7b (BUILT 2026-06-18) - quality fixes after live review.
* Liquidity floor raised to $25M average daily dollar volume (was $5M), with a UI
  "Min liquidity" slider so the user dials how blue-chip the book is. Removes the
  obscure micro/small-cap momentum names that crowded the book.
* Zero-share holdings are dropped from the final book (no more $0 rows like the MU
  data glitch, where a far stop made it unsizable).
* The liquid large-cap core is always prepended to the candidate set, so megacaps
  (NVDA, AMD, AAPL) are evaluated even when the nightly liquidity ranking was built
  from a partial cache. Verified: book now holds recognizable names across 6 sectors.
* Known data note: a few tickers (e.g. MU) show implausible prices from yfinance;
  a price-sanity check is a future refinement.
* Analyst 401s were a stale Keychain ANTHROPIC_API_KEY (app reads Keychain first);
  fix is `security add-generic-password -U -s ANTHROPIC_API_KEY -a "$USER" -w <new key>`.

Phase 7 (BUILT 2026-06-18) - independent analyst + paper-trade comparison.
* `ml/analyst_agent.py` - an independent Claude (Haiku) second opinion on each finalist:
  agree/neutral/disagree, confidence, rationale, and risk flags a price model misses.
  Live/forward only (cannot be backtested - the model may know the future), finalist-only,
  a few cents per run, budget-guarded, run on demand via a UI button. Verified: it
  DISAGREED on NTRS (the froth-flagged pick) at 72% citing sector crowding and weak
  sentiment - a genuine cross-check.
* Paper-trade comparison: every UI Build is now journaled tagged by mandate, and
  `portfolio_journal.compare_by_mandate` plus a UI scoreboard compare swing vs fast (or
  any mandate) on realized out-of-sample returns as horizons elapse. Counts the full book,
  not cherry-picked winners (which would be selection bias and invalid).

Phase 6 (BUILT 2026-06-18) - unattended operation.
* `ml/portfolio_journal.py` - records every proposed portfolio (holdings, entries,
  stops, agent trace) to `data/journal/`, and `update_outcomes` fills realized
  returns later for the forward paper-trading track record.
* `data/universe.build_liquid_ranking` - ranks the universe by 20d dollar volume and
  persists it; the Portfolio reads it so the wide scan is liquidity-ordered, not
  alphabetical (kills the heuristic). Verified: top names are MU, ARM, INTC, ASML,
  CSCO, LRCX, etc.
* `scripts/oros_run.py` - the cron entry point: refresh -> liquidity-rank -> run the
  orchestrator -> journal -> update outcomes -> brief. Deferring budget cap,
  propose-and-journal-only (no orders, no money moved). Verified end-to-end in 17s.
* `docs/OROS_SCHEDULING.md` - cron and launchd setup, with the guardrails and the
  paper-trade-first discipline.
* `ml/notifier.py` - change detection (names added/dropped vs the last proposal),
  an opportunity scan that runs the IPO/new-listing track over young names so
  fresh breakouts like SPCX are never missed, and alerts (macOS notification +
  `data/journal/alerts.log`). Wired into the runner (brief now shows changes and
  IPO watch/breakouts) and into the Portfolio UI (an Opportunities & changes panel).
* Portfolio UI: a "Open Chase to place these trades yourself" link, with the
  explicit reminder that OROS proposes and journals only and never trades or moves
  money - the user executes in their brokerage.

Remaining from the hardening checklist before real capital: purged/embargoed
walk-forward, portfolio correlation + kill-switch, and a forward paper-trading
record accumulated via the nightly journal.

Phase 7 (optional research): visual chart matching, starting with text rule
confirmation, then model vision on finalists, then a CNN or CLIP classifier.

Each phase is shippable and testable on its own, so OROS keeps working throughout
rather than going dark during a big rewrite.

---

## 12. Decisions locked and open items

Locked:
* Free data stack (yfinance plus SEC files), full US listed universe near 8,000.
* Haiku screening, Sonnet memos.
* Event driven operating model with Batch API, prompt caching, deferring budget cap.
* Scope is fast momentum swing through long hold (speeds two and three). True
  intraday day trading (speed one) is out, it needs paid real time data.
* Dual resolution scan: weekly for stage, daily plus intraday shortlist recheck
  for fast breakout entries, all on free data.
* Book set, seven: Weinstein, technical book, Graham, O'Neil, Minervini, Lynch,
  How to Swing Trade. Bogle and How to Day Trade for a Living dropped. Corpus is
  about 6,800 chunks, so ChromaDB stays, no FAISS, no topic modeling.
* Minervini PDF is a scan, run through OCR (ocrmypdf) before ingestion.
* Visual chart matching planned in three tiers (text rules, vision on finalists,
  optional CNN or CLIP classifier) as a confirming signal, never a trigger.
* All book PDFs go in `Finance Books/`; legacy `Weinstein /` folder left as is
  until Phase 3 repoints the loader.
* Strategy router with one time mandate intake, then autonomous.

Open for your call:
* FMP: rotate to a working key, or remove it from the stack (recommended remove).
* Account size assumption for position sizing in the portfolio builder.
* Notification channel for the unattended runs: audio brief, email, or file only.
* Whether mandate intake stays (recommended) or is replaced by a silent saved
  risk profile with no questions at all.
* New-listing / IPO-momentum track (BUILT, verified 2026-06-18):
  `ml/new_listing_scanner.py`. A walled-off track for stocks under ~150 days that
  Weinstein cannot stage, using O'Neil and Minervini new-issue breakout rules on
  daily bars and short MAs. Verified: SPCX (5 days) -> TOO_NEW watch with "needs
  ~10 more days"; 500-day names -> handed back to the Weinstein engine; a synthetic
  base-and-breakout -> BREAKOUT buy candidate with a stop; a tight base -> BASING
  with the exact trigger price. Kept entirely out of `stage_analyzer` so it never
  weakens the core discipline. Open sub-item: how to surface this as its own UI
  watchlist and feed graduates (>=150 days) into the Weinstein scan automatically.
