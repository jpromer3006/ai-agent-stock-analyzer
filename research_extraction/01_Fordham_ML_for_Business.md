# Source Review 01: Fordham — Machine Learning for Business

**Reviewed:** June 10, 2026
**Source:** `/Users/jpromero/Fordham/Machine Learning for Business` (Lectures I–XII, Labs, Homeworks, MTA group project)
**Verdict:** 4 new algorithm families worth extracting for OROS, 3 already covered by the main report, 2 set aside.

---

## Course Coverage Inventory

| Lecture | Topic | OROS status |
|---|---|---|
| I–II | Data basics, curse of dimensionality, feature selection (wrapper, embedded, stepwise), PCA, normalization, sampling | Partially new (PCA market factor idea) |
| III–V | Classification decision trees (entropy, information gain), accuracy, train/test, ROC/AUC, regression trees | Already in report (GBT foundations, evaluation harness) |
| VI–VII | Perceptron, multilayer perceptrons, backprop | Already in report (deferred, sample starved) |
| VIII | Bayesian networks, Naive Bayes, conditional probability | **NEW — extract** |
| IX–X | KMeans (elbow, silhouette), hierarchical clustering, DBSCAN, SVM | **NEW — extract (DBSCAN, hierarchical); SVM as benchmark only** |
| XI | Association rules: Apriori, FP-Tree, market basket, support/confidence/lift | **NEW — extract** |
| XII (TLDR) | Network analysis (degree/closeness/betweenness centrality, PageRank, hubs, bridges), text mining basics (stop words, stemming, POS, n-grams, valence) | **NEW — extract (network analysis); text basics superseded by transformer plan** |
| Group project | MTA workplace accidents: logistic regression, RF, boosting, PR curves, scorecards, ranking | Methodology reusable (scorecard pattern) |

---

## Extract 1: Association Rule Mining (Apriori / FP-Growth) — highest value add

**What the course taught:** mine transactional data for rules of the form {A, B} → C with support, confidence, and lift.

**OROS application — rule mining over the journal.** Treat each journaled pick as a transaction of discretized items: `{volume_surge_high, regime_BULL, sector_RS_top_quartile, fresh_setup, sentiment_positive}` → `{trade_won}`. Run FP-Growth (faster than Apriori, same output) and keep rules with high lift toward success or failure.

Why this matters when XGBoost already exists: association rules are **globally human readable**. SHAP explains one prediction at a time; a mined rule like "extended + weak volume + BEAR_EARLY → failure, lift 3.2, support 41 trades" is a standing piece of strategy knowledge a person can audit, memorize, and challenge. This is the strongest transparency complement to the meta model and fits the OROS ethos directly. Rules also act as a sanity check on the meta model: if XGBoost and the rule base disagree systematically, something is wrong with one of them.

**Second application — cross ticker market basket.** Transactions = scan days; items = tickers that confirmed a breakout that day. Mined rules surface co-breakout structure ("when 2+ utilities break out, infrastructure EPC names follow within 2 weeks"), a data driven sector rotation signal that complements `ml/sector_rs.py`.

**Integration:** new module `ml/rule_miner.py`, run monthly over journal history; surface top rules in the UI as a "what the history says" panel; emit rule match counts as meta model features. Library: `mlxtend` (Apriori/FP-Growth, pandas native).

## Extract 2: Bayesian Networks / Naive Bayes

**What the course taught:** probabilistic classification via conditional probability tables; networks encode dependency structure and report calibrated probabilities natively.

**OROS application.** The Weinstein gate logic is already a causal graph drawn in prose: regime influences breakout success, volume confirms, RS confirms, sentiment modulates. A small hand structured Bayes net (5 to 8 discretized nodes, fit CPTs from journal history with `pgmpy`) yields:

- Natively calibrated P(success | gates) with **zero black box**, the strongest baseline the meta model must beat, alongside logistic regression.
- Inference in any direction: "given the trade failed, which gate state was most likely responsible?" — useful for the trade auditor agent (`agents/trade_auditor.py`).
- Graceful handling of missing evidence (sentiment None marginalizes out cleanly, matching the existing neutral on silence contract in `entry_gates.py`).

Naive Bayes (independence assumed) is the 30 minute version; worth running once as a floor, but the structured net is the real extract because the gates are *not* independent (volume and extension correlate).

**Integration:** `ml/bayes_gate.py`; add to the Phase 2 baseline set in the main report (which becomes: always take, logistic, Bayes net, hand weighted score).

## Extract 3: Network Analysis (centrality, PageRank)

**What the course taught:** degree, closeness, betweenness centrality; hubs, bridges, PageRank.

**OROS application.** Build a correlation network over the 80 ticker universe (edges = trailing 90 day return correlation above threshold, or lead lag correlation for directed edges). Then:

- **Centrality as a meta model feature.** High degree tickers move with the herd (Ch. 8's "75% follow the market" applies hardest); low centrality names can buck a weak tape. Betweenness flags bridge stocks connecting sector clusters, often the rotation transmission path.
- **Directed lead lag PageRank** ranks influence: if NVDA leads and ORA follows historically, NVDA's fresh Stage 2 is early evidence for the followers. This becomes a feature: `leader_breakout_score` = weighted count of upstream leaders currently in confirmed Stage 2.
- **UI value:** the network graph itself is a strong HCI artifact for the Trader tab; cluster structure should visibly align with the 15 sector categories, and where it does not, that is information.

**Integration:** `ml/ticker_network.py` using `networkx` (tiny at n=80); recompute weekly; emit 3 features (degree centrality, cluster id, leader_breakout_score).

## Extract 4: Density and Hierarchy Clustering (DBSCAN, hierarchical)

**What the course taught:** KMeans (elbow, silhouette), hierarchical clustering with dendrograms, DBSCAN with noise points.

**OROS application.** The main report already listed k-means cohorts as a cheap add; the course materials upgrade that idea:

- **DBSCAN over journal feature vectors**, because its *noise* label is the feature: a pick whose decision time profile belongs to no historical cluster is out of distribution, and the meta model's confidence should be suppressed on it. This is a simpler, fully interpretable cousin of the Isolation Forest idea already in the report, and the two can vote.
- **Hierarchical clustering of tickers** on behavior features (beta, volatility, RS profile, stage duration statistics) produces a dendrogram, a natural cross check of the hand assigned 15 sector categories in `data/tickers.py`. Tickers that cluster away from their assigned category deserve a second look at which specialist agent handles them.

**Integration:** extend the Phase 2 anomaly tripwire with DBSCAN noise flagging (`sklearn.cluster.DBSCAN`, already installed); one off dendrogram audit notebook for the category assignments.

## Covered Already (no action)

Decision trees, regression trees, Random Forest, boosting, logistic regression, ROC/AUC and confusion matrix discipline, MLPs (deferred deliberately), KMeans cohorts, text preprocessing basics (superseded by FinBERT/DeBERTa + BERTopic plan).

## Set Aside, With Reasons

- **SVM** (Lectures X/XII): legitimate classifier, but on tabular journal features GBTs and logistic bracket it from above and below; adds a kernel/scaling tuning burden with no transparency benefit. Run once in the Phase 2 benchmark table, do not build around it.
- **PCA as preprocessing for the meta model**: trees do not need decorrelated inputs. The one keeper: first principal component of universe daily returns as a market breadth factor feature (a complement to Mansfield RS), nearly free to compute.

## Methodology Keeper from the Group Project

The MTA project's **scorecard pattern** (per model PR curves, side by side metric bars, a final composite scorecard with the champion model justified) is exactly the artifact `evaluation/test_meta_model.py` should produce each retraining cycle. Reuse the structure of `ML_Scorecard_Workforce.xlsx` for the model promotion gate in Phase 7.

---

## Updated Build Impact on Main Report (00)

- Phase 2 baselines expand: hand weighted score, logistic regression, **structured Bayes net**.
- Phase 2 anomaly tripwire: Isolation Forest **+ DBSCAN noise vote**.
- Phase 3 features add: market breadth PC1.
- New Phase 4b (parallel, small): `ml/ticker_network.py` centrality + leader breakout features.
- New Phase 5b (small): `ml/rule_miner.py` FP-Growth over journal, UI rule panel, rule match features.
- Phase 7 promotion gate adopts the scorecard artifact pattern.

New dependencies: `mlxtend`, `pgmpy`, `networkx`. All pure Python, no infrastructure change.
