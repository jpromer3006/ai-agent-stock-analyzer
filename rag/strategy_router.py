"""
strategy_router.py - Map a portfolio mandate + market regime to active strategies.

The library spans seven books and several frameworks. Blending all of them on
every decision produces contradictions, so the router scopes which books and
concepts speak for a given mandate, and the market regime tilts how much each
strategy is trusted right now. This is the integration spine that turns many
strategies into one coherent, regime-aware selection rule feeding the portfolio
engine.

Pure logic, no ML and no network: fully testable.

    PYTHONPATH=. python3 rag/strategy_router.py
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Archetypes: mandate -> books in scope, concept focus, base strategy weights.
# Strategy keys: weinstein, momentum (oneil/minervini/swing), ipo, value (graham),
# growth (lynch/oneil).
# ---------------------------------------------------------------------------
@dataclass
class Archetype:
    key: str
    horizon: str
    books: list[str]
    concepts: list[str]
    weights: dict[str, float]      # base blend, before regime tilt


# Weights are over INDIVIDUAL strategy keys from ml.strategy_registry, so every
# book is a peer and any can be weighted to zero. Nothing is hard-coded as a
# foundation: Weinstein is just the "weinstein" key.
ARCHETYPES: dict[str, Archetype] = {
    "fast_momentum": Archetype(
        "fast_momentum", "1 day to a few days",
        books=["minervini", "oneil", "weinstein", "technical", "swingtrade"],
        concepts=["breakout", "relative_strength", "volume", "stage", "ipo"],
        weights={"weinstein": 0.10, "sepa": 0.22, "canslim": 0.18, "technical": 0.10,
                 "swing": 0.15, "value": 0.00, "growth": 0.05, "ipo": 0.20},
    ),
    "swing_momentum": Archetype(
        "swing_momentum", "days to weeks",
        books=["weinstein", "minervini", "oneil", "technical", "swingtrade"],
        concepts=["stage", "breakout", "relative_strength", "volume"],
        weights={"weinstein": 0.18, "sepa": 0.20, "canslim": 0.16, "technical": 0.12,
                 "swing": 0.12, "value": 0.04, "growth": 0.08, "ipo": 0.10},
    ),
    "growth_hold": Archetype(
        "growth_hold", "medium to long",
        books=["lynch", "oneil", "weinstein"],
        concepts=["growth", "relative_strength", "stage"],
        weights={"weinstein": 0.12, "sepa": 0.10, "canslim": 0.20, "technical": 0.06,
                 "swing": 0.04, "value": 0.10, "growth": 0.30, "ipo": 0.08},
    ),
    "value_hold": Archetype(
        "value_hold", "long",
        books=["graham", "lynch"],
        concepts=["valuation", "risk_management", "growth"],
        weights={"weinstein": 0.08, "sepa": 0.04, "canslim": 0.06, "technical": 0.04,
                 "swing": 0.02, "value": 0.50, "growth": 0.22, "ipo": 0.04},
    ),
}


# ---------------------------------------------------------------------------
# Regime tilts. The market regime (from ml/market_context) scales how much each
# strategy is trusted and how much capital is deployed. Weinstein Ch. 8:
# "Know the market's stage first."
# ---------------------------------------------------------------------------
@dataclass
class RegimeTilt:
    description: str
    multipliers: dict[str, float]   # multiply the archetype weights
    max_invested: float             # fraction of capital deployable (rest = cash)


# Multipliers per strategy key. The market regime decides which strategies are
# trusted, so they work interchangeably rather than one always leading.
REGIME_TILTS: dict[str, RegimeTilt] = {
    "bull": RegimeTilt(
        "SPY Stage 2, momentum tailwind: favor offense.",
        {"sepa": 1.3, "canslim": 1.3, "swing": 1.2, "ipo": 1.4, "technical": 1.1,
         "weinstein": 1.1, "growth": 1.0, "value": 0.7},
        max_invested=1.00,
    ),
    "neutral": RegimeTilt(
        "Mixed / transitioning: balanced, partial deployment.",
        {"sepa": 1.0, "canslim": 1.0, "swing": 0.9, "ipo": 0.8, "technical": 1.0,
         "weinstein": 1.0, "growth": 1.0, "value": 1.1},
        max_invested=0.70,
    ),
    "bear": RegimeTilt(
        "SPY Stage 4 / breaking down: defense and capital preservation.",
        {"sepa": 0.5, "canslim": 0.5, "swing": 0.4, "ipo": 0.2, "technical": 0.7,
         "weinstein": 0.8, "growth": 0.9, "value": 1.5},
        max_invested=0.30,
    ),
}


@dataclass
class StrategyPlan:
    mandate: str
    regime: str
    books: list[str]
    concepts: list[str]
    weights: dict[str, float]       # normalized, regime-tilted
    max_invested: float
    rationale: str = ""


def _normalize(w: dict[str, float]) -> dict[str, float]:
    s = sum(v for v in w.values() if v > 0) or 1.0
    return {k: round(max(v, 0) / s, 3) for k, v in w.items()}


def route(mandate: str = "swing_momentum", regime: str = "bull") -> StrategyPlan:
    """Resolve a mandate + regime into the active books, concepts, and weights."""
    arch = ARCHETYPES.get(mandate, ARCHETYPES["swing_momentum"])
    tilt = REGIME_TILTS.get(regime, REGIME_TILTS["neutral"])
    tilted = {k: arch.weights.get(k, 0.0) * tilt.multipliers.get(k, 1.0)
              for k in set(arch.weights) | set(tilt.multipliers)}
    tilted = {k: v for k, v in tilted.items() if v > 0}
    return StrategyPlan(
        mandate=arch.key, regime=regime, books=arch.books, concepts=arch.concepts,
        weights=_normalize(tilted), max_invested=tilt.max_invested,
        rationale=f"{arch.key} under {regime} regime: {tilt.description}",
    )


def _selftest() -> None:
    for mandate in ARCHETYPES:
        for regime in REGIME_TILTS:
            p = route(mandate, regime)
            print(f"{mandate:15} | {regime:7} | invested<= {p.max_invested:.0%} "
                  f"| weights {p.weights}")
        print()


if __name__ == "__main__":
    _selftest()
