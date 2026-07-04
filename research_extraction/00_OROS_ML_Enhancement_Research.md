# OROS ML Enhancement: Research Report and Architecture

**Date:** June 10, 2026
**Scope:** Adding probabilistic ML prediction on top of the deterministic Weinstein stage engine
**Targets:** Breakout success probability, stage transition forecasting, return ranking, risk and exit timing

---

## 1. Executive Summary

Your instinct is correct on two counts. First, the Weinstein engine in OROS is a signal generator, not a predictor. Every classification in `ml/stage_analyzer.py` is a deterministic function of fixed thresholds (price 2% above a rising 30 week MA, volume surge above 1.5x, Mansfield RS above 0). It tells you a breakout happened. It cannot tell you which breakouts will follow through. Second, XGBoost over price features plus news sentiment is exactly the right starting point, and the formal name for this pattern is **meta labeling** (Lopez de Prado, 2018): keep the rule based system as the primary model, train ML only to predict the probability that each of its signals succeeds.

The strongest finding from exploring your codebase: **OROS has already built its own training data factory without calling it that.** The journal (`ml/journal.py`) freezes a look ahead safe snapshot of 31 decision time features for 80 tickers every scan day, paired with outcome prices in `data/journal/outcome_prices/`. And `ml/stage_history.py` can reconstruct 10 years of weekly stage transitions per ticker, giving roughly 41,000 ticker weeks of labeled history. The ML layer slots into existing seams; almost nothing needs rearchitecting.

The recommended build order: (1) meta label XGBoost classifier replacing the hand weighted entry quality score, (2) sentiment upgrade from VADER to a transformer ensemble plus your paid feed, with BERTopic narrative features, (3) HMM regime model upgrading `market_context`, (4) XGBoost ranker for capital allocation, (5) survival model for exits. Sequence deep learning (LSTM) is deliberately deferred: with an 80 ticker universe it will overfit before it helps.

---

## 2. Where OROS Stands Today

| Component | File | Nature | ML readiness |
|---|---|---|---|
| Stage classifier | `ml/stage_analyzer.py` (1,320 lines) | Deterministic thresholds, faithful to the book | Feature source, stays as is |
| Entry quality score | `ml/entry_gates.py` | Hand weighted linear blend (extension .25, volume .25, freshness .20, RS .15, sentiment .15) | **Prime replacement target** |
| Sentiment | `ml/sentiment_read.py` | VADER mean over 15 yfinance headlines | **Prime upgrade target** |
| Market regime | `ml/market_context.py` | Rule based 5 state label (BULL / BULL_FADING / NEUTRAL / BEAR_EARLY / BEAR) | HMM upgrade target |
| Transition watch | `ml/transition_watch.py` | 4 hand crafted leading signals for Stage 4 repair | Sequence model target |
| Decision journal | `ml/journal.py` + `data/journal/` | Daily frozen snapshots, schema v2, 31 fields, 80 picks/day since 2026-05-25 | **Training labels** |
| Stage history | `ml/stage_history.py` | Walk forward 10 year weekly stage sequence | **Training labels at scale** |
| Confidence scorer | `ml/confidence_scorer.py` | Heuristic | Calibration target |

Stack already in place: Python 3.11, pandas, numpy, scikit-learn, sentence-transformers, chromadb, plotly. Only additions needed: `xgboost`, `hmmlearn`, `bertopic`, `shap`, `mapie` (conformal), optionally `lifelines` (survival).

A note on the deterministic framing: determinism is not the weakness. Transparency is OROS's design ethos (the entry gate comment literally says "transparent, not back-fit"). The weakness is that the rules are **uncalibrated**: a confirmed BUY at 1.5x volume in a strong sector and a confirmed BUY at 1.51x volume in a weak tape print the same signal. ML's job here is calibration and ranking, not replacing the rules. This framing also keeps the system explainable, which matters for the ethical dimension below.

---

## 3. Algorithm Survey by Prediction Target

### 3.1 Breakout Success Probability — Meta Labeling with XGBoost (your XGBoost idea, formalized)

**The pattern.** Primary model = Weinstein engine (generates BUY signals). Secondary model = XGBoost binary classifier answering one question per signal: *will this trade hit its profit target before its stop?* You then size positions by predicted probability, or skip signals below a threshold. Research from Hudson & Thames and 2025 follow ups consistently shows meta labeling improves precision and Sharpe of rule based primaries, precisely because the primary handles side selection and the ML only handles bet/no bet, a much easier learning problem.

**Labels: the Triple Barrier Method.** For each confirmed BUY in the journal, simulate three barriers: profit target (e.g. +15% or `target_1` from the `TradeSetup` dataclass), stop (weekly close below the 30 week MA, Weinstein's own invalidator, already computed as `is_invalidated`), and time expiry (e.g. 26 weeks). Label 1 if profit barrier hits first, 0 otherwise. This is better than fixed horizon labels because it encodes your actual exit logic. Your journal plus `stage_history` lets you generate these labels both forward (live journal) and backward (10 year reconstruction, thousands of historical Stage 2 entries).

**Features (decision time only, all already in the journal schema):** `pct_above_ma`, `ma_slope_pct`, `mansfield_rs`, `volume_surge`, `return_since_breakout`, `weeks_held`, `pct_from_52w_high`, `today_change_pct`, `resistance_grade`, `days_to_earnings`, sentiment block, plus context features: market regime, sector RS (`ml/sector_rs.py`), VIX level, and the new NLP features from 3.2.

**Why XGBoost specifically.** Tabular features, modest sample sizes, missing values (sentiment is None for non buys), nonlinear interactions (volume matters more when extension is low), monotonic constraint support (you can force "more extended above MA never increases success probability," encoding Weinstein Ch. 5 as an inductive bias). Gradient boosted trees remain the consistent winner on this data shape. LightGBM and CatBoost are interchangeable here; benchmark all three, they share an API.

**Alternatives considered.** Logistic regression: keep as the mandatory baseline, if the champion cannot beat it out of sample, ship the logistic model. Random Forest: fine, slightly weaker, useful in the ensemble. Generic neural nets on tabular data: consistently underperform GBTs at this scale, skip.

**TabPFN-2.5 as co-champion (added June 10, 2026).** TabPFN, the tabular foundation model from Prior Labs, is the serious challenger to XGBoost here. TabPFN-2.5 leads the TabArena benchmark, handles up to 50k rows and 2,000 features, and shows a 100% win rate over default XGBoost on classification under 10k rows, which is precisely the OROS label regime. Three properties fit the meta model role: it is tuning free (fewer configurations tried means a smaller deflated Sharpe penalty for search), its prior data fitted inference yields well calibrated probabilities natively (collapsing the isotonic calibration step), and it learns in context, so retraining is just supplying the current labeled set at inference, making the quarterly retrain trivial. Caveats: explanation is approximate kernel SHAP rather than exact TreeSHAP, so XGBoost and the Bayes net stay in the stack as explanation companions regardless of which model wins; inference carries the training set in context (fine at 80 tickers daily, slow for bulk backtests); the i.i.d. prior makes walk forward validation non negotiable; and the TabPFN-2.5 License v1.0 terms must be verified for OROS's intended use before promotion past research. Decision rule: Phase 2 trains XGBoost and TabPFN-2.5 on identical folds; the promotion scorecard picks the champion, and the loser remains in the benchmark table for every retrain cycle.

**Calibration and honesty.** Raw XGBoost scores are not probabilities. Apply isotonic regression or Platt scaling on a held out fold, then wrap with **conformal prediction** (MAPIE) to output intervals: "success probability 0.62 ± 0.11." This honest uncertainty becomes `bull_probability`'s replacement and feeds position sizing.

### 3.2 News NLP — Sentiment Upgrade plus Topic Modeling (your second idea)

**Sentiment engine upgrade.** VADER is a general purpose social media lexicon; it reads "Company cuts guidance" as neutral. The 2025/2026 benchmark literature is consistent: finance tuned transformers (FinBERT, and now DeBERTa variants) beat VADER decisively, and fine tuned LLMs beat FinBERT, with an ensemble of DeBERTa + RoBERTa + FinBERT reaching roughly 80% sentiment accuracy vs below 70% for individual models. Since you chose a **paid feed with pre scored sentiment**, the recommended design is two channels:

1. **Vendor sentiment** as the primary signal (entity resolved, point in time timestamped, this matters most for backtest integrity).
2. **In house transformer score** (FinBERT or DeBERTa via your existing `sentence-transformers` stack) over the same headlines as a disagreement detector. When vendor and in house scores diverge sharply, that itself is a feature; divergence often flags ambiguous or manipulative coverage.

Keep `sentiment_read.py`'s graceful degradation contract (never raises, None means neutral). Extend the journal schema (bump `SCHEMA_VERSION` to 3) with: `vendor_sentiment`, `model_sentiment`, `sentiment_divergence`, `sentiment_volume_zscore` (abnormal news volume is itself predictive), `sentiment_momentum_5d`.

**Topic modeling with BERTopic.** This is where NLP adds something sentiment cannot: *what kind* of news is driving the tape. BERTopic (HDBSCAN clustering over transformer embeddings + class based TF-IDF) needs no preset topic count and produces interpretable topics. 2025 work on narrative econometrics shows topic activations carry return relevant signal beyond polarity. Practical design for OROS:

- Fit BERTopic monthly over the trailing 90 days of headlines for the 80 ticker universe (cheap at this scale; reuse your chromadb embeddings).
- Map topics to a small stable taxonomy: earnings, guidance, M&A, litigation/regulatory, product, analyst action, macro, insider activity.
- Per ticker per day features: topic mix vector, dominant topic, **topic novelty** (is today's narrative new for this ticker?), and **topic conditioned sentiment** (negative sentiment on litigation is different from negative sentiment on a missed product rumor; 2026 work on event conditional sentiment confirms conditioning beats pooled polarity).

These become roughly 10 to 12 additional XGBoost features. LDA is the classical alternative but is weaker on short headline text; BERTopic is the right tool. Your `agents/specialists/sentiment_analyst.py` four signal analyst can consume the same topic features for its narratives, so the LLM layer and the ML layer stay consistent.

### 3.3 Stage Transition Forecasting — HMM First, Sequence Models Later

**The insight you already had.** `transition_watch.py` exists because strict Weinstein classification lags at the Stage 4 to Stage 1 turn. Its four hand crafted signals are a manual approximation of what a sequence model learns.

**Hidden Markov Model (recommended).** Weinstein's four stages *are* a hidden state model: latent regime, observable emissions (returns, volatility, volume, RS). A 4 state Gaussian HMM (`hmmlearn`) fit per ticker on weekly features gives you, every week, a full posterior over stages and a **transition probability vector**: "ORA: P(Stage 2 to Stage 3 within 4 weeks) = 0.31." That is the probabilistic early warning the deterministic classifier cannot produce. Use `stage_history.py` output to validate that HMM states align with Weinstein stages (they should, by construction of the emissions). A market level HMM on SPY/breadth features similarly upgrades `market_context.py` from its rule based 5 state label to regime probabilities with expected regime duration; the 75% of stocks follow the market rule (Ch. 8, already cited in your entry gate) then gets a probabilistic backbone.

**LSTM/GRU/Temporal CNN (defer to Phase 4+).** Sequence deep learning can learn richer transition dynamics, but 80 tickers x 520 weeks is small for these models, and they are opaque, which cuts against OROS's transparency ethos. If you later expand the universe past ~500 tickers, revisit with a small GRU or a temporal convolutional network trained on stage labeled windows, and treat its output as one more feature into XGBoost rather than as a decision maker. This keeps the deep model auditable through the tree model's SHAP values.

### 3.4 Return Ranking — Learning to Rank for Capital Allocation

When the scan produces 12 confirmed buys and you can take 4, classification probabilities are not the ideal sort key. Train **XGBoost in ranking mode** (`rank:pairwise` / LambdaMART objective) where each scan day is a query group and the label is forward 13 week return quintile. Same features as 3.1, so this is nearly free once the meta label pipeline exists. Cross sectional ranking is also more robust than absolute return regression because it differences out market drift. Output: `allocation_rank` per pick, feeding `position_sizer.py`.

### 3.5 Risk and Exit Timing — Survival Analysis and Quantile Regression

Weinstein's sell side (weekly close below the 30 week MA) is the crudest part of the system, as you noted. Two complementary upgrades:

- **Survival analysis** on Stage 2 episodes from `stage_history.py`: model time until stage death (transition to 3/4) with a Cox proportional hazards model or Random Survival Forest (`lifelines` / `scikit-survival`). Covariates: weeks in stage, extension, RS trend, volume trend, sentiment drift. Output a weekly **hazard score** per holding: "this Stage 2 is aging; historical episodes with this profile died within 6 weeks 70% of the time." This directly improves TRIM timing ahead of the MA cross.
- **Quantile regression** (XGBoost `reg:quantileerror` or GBT quantile loss) predicting the 5th percentile of 4 week forward return, a conditional drawdown estimate per position that can tighten stops or cut size before the technical invalidation prints.

### 3.6 Algorithms Surveyed and Set Aside (for now)

| Algorithm | Verdict | Reason |
|---|---|---|
| LSTM/Transformer price forecasting | Defer | Sample starved at 80 tickers; opaque; forecasting raw prices is the hardest target with the weakest literature support |
| GANs (synthetic market data augmentation) | Skip for now | Interesting for stress testing scenarios, but synthetic OHLCV rarely preserves the cross asset structure that matters; revisit only for robustness testing, never for training labels |
| Reinforcement learning (execution/sizing) | Defer | Needs a high fidelity simulator; meta label probability times Kelly fraction gets 90% of the benefit with 10% of the risk |
| Unsupervised clustering of tickers (k-means on feature profiles) | Cheap add | Useful as a feature (behavioral cohort) and as an HCI grouping in the UI; low effort, consider in Phase 2 |
| Anomaly detection (Isolation Forest on feature vectors) | Cheap add | Flag scan days where a ticker's feature vector is far outside training distribution, suppress model confidence there; pairs naturally with conformal prediction |

---

## 4. Recommended Architecture

```
                        ┌─────────────────────────────────────────┐
                        │              DATA LAYER                  │
                        │ yfinance OHLCV │ paid news+sentiment feed│
                        │ SEC EDGAR │ earnings calendar │ VIX      │
                        └───────────────┬─────────────────────────┘
                                        │
              ┌─────────────────────────┼──────────────────────────┐
              ▼                         ▼                          ▼
   ┌────────────────────┐   ┌────────────────────┐   ┌────────────────────┐
   │  WEINSTEIN ENGINE  │   │     NLP LAYER      │   │   REGIME LAYER     │
   │  (unchanged)       │   │ vendor sentiment   │   │ market HMM (SPY)   │
   │  stage_analyzer    │   │ FinBERT/DeBERTa    │   │ per-ticker 4-state │
   │  → BUY/WATCH/SELL  │   │ BERTopic topics    │   │ HMM → transition   │
   │  signals + features│   │ novelty, divergence│   │ probabilities      │
   └─────────┬──────────┘   └─────────┬──────────┘   └─────────┬──────────┘
             │ signals + features      │ NLP features           │ regime features
             └────────────┬────────────┴────────────┬───────────┘
                          ▼                         │
              ┌───────────────────────────┐         │
              │   META MODEL (XGBoost)    │◄────────┘
              │ P(signal succeeds) +      │
              │ conformal interval        │
              │ SHAP per-signal reasons   │
              ├───────────────────────────┤
              │   RANKER (LambdaMART)     │ → allocation_rank
              ├───────────────────────────┤
              │ EXIT MODELS (survival +   │ → hazard score,
              │ quantile drawdown)        │   conditional drawdown
              └────────────┬──────────────┘
                           ▼
       entry_gates.py (ML score replaces hand weights; gates remain)
                           ▼
       journal.py schema v3 (freeze features + predictions + intervals)
                           ▼
       position_sizer.py (probability- and rank-aware sizing)
                           ▼
       UI: probability + interval + SHAP reasons + topic chips
```

Key integration decisions, mapped to real seams in the code:

1. **`entry_gates.compute_entry_quality()` is the swap point.** Today it returns (score, grade, reasons) from hand weights. The meta model returns the same tuple shape: calibrated probability as score, conformal width informing the grade, top SHAP contributions as the reasons list. The function contract, the journal fields, and the UI all survive unchanged. Keep the hand weighted score computed in parallel as `entry_quality_score_legacy` for A/B comparison.
2. **The journal is the flywheel.** Every scan freezes features *and* model predictions; outcomes arrive via the existing `outcome_prices` mechanism; retraining consumes the journal. This loop is already look ahead safe by construction, which is rarer and more valuable than any single algorithm choice.
3. **The Weinstein engine is never overridden on direction.** ML filters, sizes, and warns. It does not generate longs the rules did not produce. This preserves interpretability and bounds model risk: worst case, a bad model makes you skip good trades, never take rule violating ones.

---

## 5. Validation Protocol (where most projects die)

With a small live journal (16 days so far) and a reconstructed 10 year history, discipline here decides whether the model is real:

- **Purged walk forward CV** as the primary protocol: train on an expanding window, embargo a gap equal to the label horizon (26 weeks for triple barrier labels) before each test window, because overlapping label windows leak. Standard k-fold is invalid for this data and will report fantasy accuracy.
- **Combinatorial Purged CV (CPCV)** as the secondary check; 2024/2025 comparisons show it gives lower probability of backtest overfitting than plain walk forward, at the cost of assuming the future resembles recombinations of the past.
- **Deflated Sharpe ratio** when comparing the ML filtered strategy against the raw Weinstein baseline, to correct for the number of configurations tried.
- **Baselines that must be beaten out of sample:** (a) the current hand weighted entry quality score, (b) logistic regression on the same features, (c) always take every confirmed BUY. If XGBoost cannot beat all three, do not ship it.
- **Leakage tripwires:** every feature must exist in the journal snapshot at decision time (your journal docstring already states this principle; enforce it programmatically with a schema whitelist), vendor sentiment must be point in time (confirm the feed provides as published timestamps, not revised history), and `stage_history` reconstructed labels must only use data available at each historical week (it is already walk forward, verify after any edit).
- **Evaluation harness:** extend `evaluation/` with `test_meta_model.py` mirroring the golden questions pattern: frozen test scan days with known outcomes, minimum precision/calibration thresholds (Brier score, reliability curve) that must pass before a model file is promoted.

---

## 6. Phased Roadmap

| Phase | Build | Effort | Dependencies |
|---|---|---|---|
| **1. Label factory** | Triple barrier labeler over `stage_history` Stage 2 episodes + live journal; ~10 yr x 80 tickers of labeled entries | Small | None, pure pandas |
| **2. Meta model v1** | XGBoost and TabPFN-2.5 co-champions on identical folds + calibration + SHAP, journal schema v3, parallel scoring in `entry_gates` (shadow mode, logged not acted on) | Medium | Phase 1 |
| **3. NLP layer** | Paid feed integration, FinBERT/DeBERTa divergence channel, BERTopic taxonomy, ~12 new features, retrain meta model | Medium | Feed contract |
| **4. Regime + transitions** | Market HMM replacing the regime label internals, per ticker HMM transition probabilities surfaced in transition watch | Medium | None (parallel) |
| **5. Ranker + sizing** | LambdaMART allocation rank into `position_sizer` | Small | Phase 2 |
| **6. Exit models** | Survival hazard + quantile drawdown into TRIM logic | Medium | Phase 1 |
| **7. Go live** | Promote meta model from shadow to active after it beats all three baselines across two retraining cycles | Gate, not build | Phases 2 to 5 |

Shadow mode (Phase 2) is the design thinking move: the model's predictions are journaled next to real outcomes for weeks before a single decision depends on it. You evaluate it exactly as you would a new team member, on a paper trail.

---

## 7. Ethical and HCI Considerations

These follow directly from the transparency ethos already encoded in the comments of `entry_gates.py`:

**Interpretability is a requirement, not a feature.** Every probability shown in the Trader tab carries its top three SHAP contributions in plain language ("62% success probability: volume surge strong, sector RS strong, but extended 18% above MA"). This keeps the human in the same epistemic position they enjoy with the current transparent score, and SHAP on trees is exact, not approximated.

**Calibrated uncertainty over false precision.** Showing "0.62" implies more knowledge than the model has. Showing "0.51 to 0.73" (conformal interval) communicates honestly and measurably changes how users weight model advice. The UI should degrade gracefully: wide intervals render visually as low confidence.

**Automation bias is the main hazard.** A probability next to every pick will anchor decisions even when labeled advisory. Mitigations: keep the deterministic Weinstein verdict visually primary and the ML score secondary; never auto suppress a confirmed BUY from the display (annotate it instead); show the model's live rolling hit rate alongside its predictions so trust tracks performance rather than presentation.

**Drift and accountability.** Models trained through mid 2026 will quietly rot. Schedule quarterly retrains, monitor population stability index on features, and let the anomaly detector suppress confidence on out of distribution days. The journal gives you a complete audit trail of what the model said and why, per day, forever. Keep it.

**Sentiment data provenance.** Vendor sentiment scores embed the vendor's editorial choices about sources. The divergence channel (3.2) is partly an ethical control: it surfaces when your independent read disagrees with the purchased one, instead of silently outsourcing judgment.

---

## 8. Sources

- [Meta-Labeling (Wikipedia overview, Lopez de Prado 2017/2018)](https://en.wikipedia.org/wiki/Meta-Labeling)
- [Does Meta Labeling Add to Signal Efficacy? Hudson & Thames](https://hudsonthames.org/does-meta-labeling-add-to-signal-efficacy-triple-barrier-method/)
- [Singh & Joubert, Does Meta-Labeling Add to Signal Efficacy (paper)](https://hudsonthames.org/wp-content/uploads/2022/04/Does-Meta-Labeling-Add-to-Signal-Efficacy.pdf)
- [Triple Barrier Labeling, Quantreo](https://www.newsletter.quantreo.com/p/the-triple-barrier-labeling-of-marco)
- [Impact of LLM News Sentiment Analysis on Stock Price Movement Prediction (arXiv 2026)](https://arxiv.org/html/2602.00086v3)
- [Topic and Event Conditional Sentiment from Finetuned LLMs (arXiv 2026)](https://arxiv.org/pdf/2603.09085)
- [Financial Sentiment Analysis: Comparative Study of Fine-Tuned Models (MDPI 2025)](https://www.mdpi.com/2227-7072/13/2/75)
- [From Headlines to Forecasts: Narrative Econometrics in Equity Markets (MDPI 2025)](https://www.mdpi.com/1911-8074/18/9/524)
- [A Framework for Measuring How News Topics Drive Stock Movement (arXiv 2025)](https://arxiv.org/html/2510.06864v1)
- [FinTextSim: Enhancing Financial Text Analysis with BERTopic (arXiv 2025)](https://arxiv.org/abs/2504.15683)
- [Market Regime Detection Using Hidden Markov Models, QuestDB](https://questdb.com/glossary/market-regime-detection-using-hidden-markov-models/)
- [Regime Adaptive Trading with HMM, QuantInsti](https://blog.quantinsti.com/regime-adaptive-trading-python/)
- [Backtest Overfitting in the ML Era: Comparison of OOS Testing Methods (ScienceDirect)](https://www.sciencedirect.com/science/article/abs/pii/S0950705124011110)
- [Purged Cross-Validation (Wikipedia)](https://en.wikipedia.org/wiki/Purged_cross-validation)
- [Cross Validation in Finance: Purging, Embargoing, Combinatorial, QuantInsti](https://blog.quantinsti.com/cross-validation-embargo-purging-combinatorial/)
- [Interpretable Hypothesis-Driven Trading: Walk-Forward Validation Framework (arXiv 2025)](https://arxiv.org/html/2512.12924v1)
- [TabPFN-2.5: Advancing the State of the Art in Tabular Foundation Models (arXiv 2025)](https://arxiv.org/abs/2511.08667)
- [The State of Tabular Foundation Models, Mindful Modeler (2026)](https://mindfulmodeler.substack.com/p/the-state-of-tabular-foundation-models)
- [Exploring TabPFN, Towards Data Science](https://towardsdatascience.com/exploring-tabpfn-a-foundation-model-built-for-tabular-data/)
