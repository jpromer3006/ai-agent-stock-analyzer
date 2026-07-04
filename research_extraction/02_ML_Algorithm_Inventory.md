# OROS ML Algorithm Inventory — Master Ingestion List

**Date:** June 10, 2026
**Purpose:** Single reference of every algorithm surfaced in the research (report 00, Fordham review 01, TabPFN discussion), organized by where it sits in the ML ingestion pipeline. Status legend: **BUILD** (committed), **BENCH** (run in benchmark, promote only if it wins), **DEFER** (revisit at larger scale), **SKIP** (assessed and rejected).

---

## Stage A — Labeling (turns history into training data)

| # | Algorithm / Method | Role in OROS | Status | Phase | Library |
|---|---|---|---|---|---|
| A1 | Triple Barrier Method | Label every Stage 2 entry: profit target vs 30 week MA invalidation vs time expiry, over `stage_history` + live journal | BUILD | 1 | pandas (custom) |
| A2 | Trend scanning labels | Alternative label set for robustness checks on A1 | BENCH | 1 | custom |

## Stage B — Feature Generators (feed the models, never decide)

| # | Algorithm / Method | Role in OROS | Status | Phase | Library |
|---|---|---|---|---|---|
| B1 | FinBERT / DeBERTa transformer sentiment | In house sentiment channel; divergence vs paid vendor feed | BUILD | 3 | transformers |
| B2 | BERTopic (HDBSCAN + c-TF-IDF) | News topic mix, topic novelty, topic conditioned sentiment per ticker per day | BUILD | 3 | bertopic |
| B3 | Hidden Markov Model (per ticker, 4 state) | Posterior over Weinstein stages + transition probability early warnings | BUILD | 4 | hmmlearn |
| B4 | Hidden Markov Model (market level) | Regime probabilities + expected regime duration, upgrades `market_context` 5 state label | BUILD | 4 | hmmlearn |
| B5 | Network analysis (degree/closeness/betweenness centrality, lead lag PageRank) | Correlation network over 80 tickers: centrality features, `leader_breakout_score`, rotation graph for UI | BUILD | 4b | networkx |
| B6 | PCA (first principal component of universe returns) | Market breadth factor feature, complement to Mansfield RS | BUILD | 3 | sklearn |
| B7 | Hierarchical clustering (dendrogram) | One off audit of the 15 hand assigned sector categories in `data/tickers.py` | BUILD (one off) | 2 | scipy |
| B8 | K-means cohorts | Behavioral cohort id as feature + UI grouping | BENCH | 2 | sklearn |

## Stage C — Core Predictive Models (the decision layer)

| # | Algorithm / Method | Role in OROS | Status | Phase | Library |
|---|---|---|---|---|---|
| C1 | **XGBoost classifier (meta labeling)** | P(breakout succeeds) over journal features; monotonic constraints encode Weinstein priors; exact TreeSHAP reasons | BUILD (co-champion) | 2 | xgboost |
| C2 | **TabPFN-2.5** | Same slot as C1 via in context learning; tuning free, natively calibrated; regime filtered context experiment | BUILD (co-champion) | 2 | tabpfn |
| C3 | LightGBM / CatBoost | Same slot, GBT family sweep alongside C1 | BENCH | 2 | lightgbm, catboost |
| C4 | Logistic regression | Mandatory floor baseline; ship it if nothing beats it | BUILD (baseline) | 2 | sklearn |
| C5 | Structured Bayesian network over discretized gates | Calibrated zero black box baseline; reverse inference for trade autopsies (`trade_auditor`) | BUILD (baseline) | 2 | pgmpy |
| C6 | Naive Bayes | 30 minute probability floor under C5 | BENCH | 2 | sklearn |
| C7 | Random Forest | Ensemble member / benchmark row | BENCH | 2 | sklearn |
| C8 | SVM | Benchmark row only; no transparency benefit on tabular journal data | BENCH (once) | 2 | sklearn |
| C9 | LambdaMART ranker (XGBoost rank:pairwise) | `allocation_rank` across same day confirmed buys, feeds `position_sizer` | BUILD | 5 | xgboost |
| C10 | Cox proportional hazards / Random Survival Forest | Stage 2 episode hazard score: time until stage death, improves TRIM timing | BUILD | 6 | lifelines, scikit-survival |
| C11 | Quantile regression (GBT quantile loss or TabPFN-2.5 distributional output) | 5th percentile 4 week forward return = conditional drawdown per position | BUILD | 6 | xgboost / tabpfn |

## Stage D — Trust Layer (calibration, uncertainty, explanation, audit)

| # | Algorithm / Method | Role in OROS | Status | Phase | Library |
|---|---|---|---|---|---|
| D1 | Isotonic regression / Platt scaling | Calibrate C1/C3 raw scores into probabilities (TabPFN largely skips this) | BUILD | 2 | sklearn |
| D2 | Conformal prediction | Honest intervals around every probability: "0.62 ± 0.11" | BUILD | 2 | mapie |
| D3 | SHAP (exact TreeSHAP on GBTs; kernel SHAP approximation on TabPFN) | Top 3 plain language reasons next to every score in the UI | BUILD | 2 | shap |
| D4 | FP-Growth / Apriori association rules | Globally readable strategy rules mined from journal ("extended + weak volume + BEAR_EARLY → failure, lift 3.2"); cross checks the meta model; cross ticker co-breakout basket | BUILD | 5b | mlxtend |
| D5 | Isolation Forest | Out of distribution scan day detector, suppresses model confidence | BUILD | 2 | sklearn |
| D6 | DBSCAN (noise label) | Second out of distribution vote alongside D5 | BUILD | 2 | sklearn |

## Stage E — Validation Protocol (methods, not models)

| # | Method | Role | Status | Phase |
|---|---|---|---|---|
| E1 | Purged walk forward CV with embargo | Primary evaluation; embargo = label horizon (26 weeks) | BUILD | 2 |
| E2 | Combinatorial Purged CV (CPCV) | Secondary overfitting check | BUILD | 2 |
| E3 | Deflated Sharpe ratio | Corrects for number of configurations tried | BUILD | 2 |
| E4 | Brier score + reliability curves | Calibration gate in `evaluation/test_meta_model.py` | BUILD | 2 |
| E5 | Scorecard promotion artifact (from MTA project pattern) | Champion selection each retrain cycle | BUILD | 7 |

## Deferred and Skipped (decided, with reasons on file)

| # | Algorithm | Status | Reason (full text in report 00 / review 01) |
|---|---|---|---|
| X1 | LSTM / GRU / TCN / sequence transformers | DEFER | Sample starved at 80 tickers; revisit past ~500 tickers, then only as a feature into Stage C |
| X2 | MLP on tabular features | SKIP | Dominated by C1–C3 at this scale |
| X3 | GANs (synthetic data) | SKIP for training | Possible future use for stress scenarios only, never labels |
| X4 | Reinforcement learning (sizing/execution) | DEFER | Needs high fidelity simulator; probability x Kelly captures most value |
| X5 | LDA topic modeling | SKIP | Weaker than BERTopic on short headline text |
| X6 | VADER | RETIRE | Replaced by B1 + vendor feed; keep contract, swap engine |
| X7 | PCA as general preprocessing | SKIP | Trees do not need it; only B6 survives |
| X8 | Centaur (Binz et al., Nature 2025, Llama 3.1 + Psych-101) | EXPERIMENT | Not a sentiment model; candidate behavioral reaction simulator: phrase headlines as investor decision scenarios, emit `centaur_reaction_score` for confirmed buys only. Captures what holders *do* (loss aversion, regret, disposition effect) vs what news *says*. Unvalidated outside lab experiments; individual choices vs aggregate flow; costly inference. Journal the feature in shadow, promote only if SHAP contribution is nonzero after one retrain cycle |

---

## Ingestion Flow (how the pieces connect)

```
RAW DATA            STAGE B FEATURES                 STAGE C MODELS              STAGE D TRUST
OHLCV ──────────┐                                 ┌─ C1 XGBoost ─┐
news feed ──────┤→ B1/B2 sentiment+topics ──────→ │  C2 TabPFN   │→ D1/D2 calib+interval
SEC/earnings ───┤→ B3/B4 HMM stage+regime ──────→ │  (champion   │→ D3 SHAP reasons
journal history ┤→ B5 network centrality ───────→ │  by E5       │→ D4 rule cross-check
(Stage A labels)┤→ B6 breadth PC1 ──────────────→ │  scorecard)  │→ D5/D6 OOD veto
                └→ B7/B8 cohorts ───────────────→ └──────┬───────┘
                                                          ↓
                                          C9 ranker → C10/C11 exit models
                                                          ↓
                                     entry_gates → journal v3 → position_sizer → UI
                                            (all evaluated under E1–E5)
```

Counts: 14 BUILD models/features + 6 trust components + 5 validation methods, 7 benchmark only, 7 deferred or skipped. Total surveyed: 39.
