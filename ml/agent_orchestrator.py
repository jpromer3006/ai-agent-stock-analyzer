"""
agent_orchestrator.py - Explicit multi-agent orchestration for portfolio construction.

OROS was already an implicit multi-agent system; this makes it explicit. Named agents
each own one responsibility and share an OrchestrationContext. The Orchestrator runs
them in order and records an agent trace, so every portfolio comes with a transparent
"who did what" log (the project's agent-trace design rule, and the Lecture 11 framing).

Agents (each a single responsibility):
  DataAgent          - the candidate universe (count, benchmarks removed)
  RegimeAgent        - market regime from SPY stage + VIX
  RouterAgent        - regime -> base strategy weights (the book ensemble's mandate)
  MemoryAgent        - tilt the weights by what worked in similar past market states
  ConstructionAgent  - the strategy ensemble scores, news enriches, the book is sized
  RiskAgent          - post-construction checks: sector concentration, cash, single-name

It delegates the heavy scoring/sizing to the tested `build_portfolio` (passing the
regime and weights the agents computed, so nothing is recomputed), and adds the agent
structure and trace around it. No behavior change to the engine; this is a coordination
and transparency layer.

    PYTHONPATH=. python3 ml/agent_orchestrator.py
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from ml.portfolio_engine import (PortfolioConfig, PortfolioPlan, build_portfolio,
                                  detect_regime, _current_market_state)
from rag.strategy_router import route

logger = logging.getLogger(__name__)


@dataclass
class AgentStep:
    agent: str
    role: str
    summary: str
    detail: dict = field(default_factory=dict)


@dataclass
class OrchestrationResult:
    plan: Optional[PortfolioPlan]
    trace: list[AgentStep] = field(default_factory=list)


class _Ctx:
    def __init__(self, symbols, config):
        self.symbols = symbols
        self.config = config or PortfolioConfig()
        self.regime = None
        self.base_weights = None
        self.weights = None
        self.max_invested = 1.0
        self.plan = None
        self.trace: list[AgentStep] = []

    def log(self, agent, role, summary, **detail):
        self.trace.append(AgentStep(agent, role, summary, detail))


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------
class DataAgent:
    name, role = "DataAgent", "universe & data"

    def run(self, ctx: _Ctx):
        bench = {"SPY", "QQQ", "DIA", "IWM", "VOO", "VTI"}
        ctx.symbols = [s for s in ctx.symbols if s not in bench]
        ctx.log(self.name, self.role, f"{len(ctx.symbols)} candidate names (benchmarks removed)",
                n_candidates=len(ctx.symbols))


class RegimeAgent:
    name, role = "RegimeAgent", "market regime"

    def run(self, ctx: _Ctx):
        ctx.regime = detect_regime()
        ctx.log(self.name, self.role, f"regime = {ctx.regime.upper()}", regime=ctx.regime)


class RouterAgent:
    name, role = "RouterAgent", "strategy mandate weights"

    def run(self, ctx: _Ctx):
        plan = route(ctx.config.mandate, ctx.regime)
        ctx.base_weights = dict(plan.weights)
        ctx.max_invested = plan.max_invested
        top = sorted(plan.weights.items(), key=lambda x: -x[1])[:3]
        ctx.log(self.name, self.role,
                "base weights: " + ", ".join(f"{k} {v:.0%}" for k, v in top),
                weights=plan.weights, deploy_cap=plan.max_invested)


class MemoryAgent:
    name, role = "MemoryAgent", "experience-weighted tilt"

    def run(self, ctx: _Ctx):
        weights = dict(ctx.base_weights)
        note = "memory unavailable (no tilt)"
        if ctx.config.use_memory:
            try:
                from ml.market_memory import memory_tilt
                state = _current_market_state(ctx.symbols)
                tilt = memory_tilt(state)
                weights = {k: weights.get(k, 0.0) * tilt.get(k, 1.0) for k in weights}
                s = sum(weights.values()) or 1.0
                weights = {k: v / s for k, v in weights.items()}
                top = sorted(tilt.items(), key=lambda x: -x[1])[:2]
                note = "boosted " + ", ".join(f"{k} x{v:.2f}" for k, v in top)
            except Exception:
                weights = dict(ctx.base_weights)
        ctx.weights = weights
        ctx.log(self.name, self.role, note, weights=weights)


class ConstructionAgent:
    name, role = "ConstructionAgent", "strategy ensemble + sizing"

    def run(self, ctx: _Ctx):
        ctx.plan = build_portfolio(ctx.symbols, ctx.config,
                                   regime=ctx.regime, weights=ctx.weights)
        p = ctx.plan
        ctx.log(self.name, self.role,
                f"{len(p.holdings)} holdings, {p.invested/ctx.config.account_size:.0%} invested, "
                f"{len(p.active_strategies)} strategies",
                holdings=[h.ticker for h in p.holdings])


class RiskAgent:
    name, role = "RiskAgent", "post-construction risk"

    def run(self, ctx: _Ctx):
        p = ctx.plan
        if not p or not p.holdings:
            ctx.log(self.name, self.role, "no holdings to check")
            return
        from collections import Counter
        sec_wt = Counter()
        for h in p.holdings:
            sec_wt[h.sector] += h.weight_pct
        top_sec, top_w = max(sec_wt.items(), key=lambda x: x[1])
        max_name = max(p.holdings, key=lambda h: h.weight_pct)
        flags = []
        if top_w > 35:
            flags.append(f"sector concentration {top_sec} {top_w:.0f}%")
        if max_name.weight_pct > 15:
            flags.append(f"{max_name.ticker} {max_name.weight_pct:.0f}% single-name")
        msg = ("; ".join(flags) if flags else "within limits") + \
              f"; cash {p.cash/ctx.config.account_size:.0%}"
        ctx.log(self.name, self.role, msg, top_sector=top_sec, top_sector_wt=round(top_w, 1))


AGENTS = [DataAgent(), RegimeAgent(), RouterAgent(), MemoryAgent(),
          ConstructionAgent(), RiskAgent()]


# ---------------------------------------------------------------------------
def run_orchestrated(symbols, config: Optional[PortfolioConfig] = None) -> OrchestrationResult:
    ctx = _Ctx(symbols, config)
    for agent in AGENTS:
        try:
            agent.run(ctx)
        except Exception as e:  # noqa: BLE001
            logger.exception("agent %s failed", agent.name)
            ctx.log(agent.name, getattr(agent, "role", ""), f"error: {e}")
    return OrchestrationResult(plan=ctx.plan, trace=ctx.trace)


def _demo() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    from data import price_cache
    sample = ["NVDA", "AMD", "AAPL", "MSFT", "AVGO", "JPM", "LLY", "XOM", "KO",
              "COST", "PWR", "ANET", "O", "HD", "AMAT"]
    price_cache.refresh_prices(sample)
    res = run_orchestrated(sample, PortfolioConfig(max_positions=8, use_social=False))
    print("AGENT TRACE")
    print("=" * 64)
    for s in res.trace:
        print(f"  [{s.agent:18}] {s.role:26} {s.summary}")
    print("=" * 64)
    if res.plan:
        print("Portfolio:", [(h.ticker, h.sector) for h in res.plan.holdings])


if __name__ == "__main__":
    _demo()
