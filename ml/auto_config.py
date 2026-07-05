"""
auto_config.py - OROS chooses its own portfolio settings (autonomous mode).

Instead of the user hand-tuning sliders, this module derives the build settings
from the data OROS already gathers and processes:

  - Mandate: the paper-trade scoreboard (ml/portfolio_journal.compare_by_mandate)
    picks the mandate with the best realized forward return once enough proposals
    have been scored; until then the regime decides (bull -> swing_momentum,
    neutral -> canslim-style discipline via swing_momentum, bear -> defensive).
  - Risk / deployment: the regime scales risk-per-trade and position count down
    as conditions worsen (Weinstein: fight in Stage 2, preserve in Stage 4).
  - Account size: the last journaled account value, so the unattended run sizes
    the same book the user last worked with (default $100k paper account).

Everything is deterministic and auditable. Educational, not financial advice.

    PYTHONPATH=. python3 ml/auto_config.py
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Regime-conditioned defaults. Bear mode cuts risk and breadth (capital
# preservation); bull mode lets the book breathe.
_REGIME_PRESETS = {
    "bull":    {"max_pos": 12, "risk_pct": 0.0125, "min_liq_m": 25, "max_per_sector": 3},
    "neutral": {"max_pos": 10, "risk_pct": 0.0100, "min_liq_m": 40, "max_per_sector": 3},
    "bear":    {"max_pos": 6,  "risk_pct": 0.0075, "min_liq_m": 60, "max_per_sector": 2},
}

_FALLBACK_MANDATE = "swing_momentum"
_MIN_SCORED = 3          # proposals with realized outcomes before we trust a mandate


def best_mandate(regime: str) -> tuple[str, str]:
    """Pick the mandate with the best realized forward return on the scoreboard.
    Returns (mandate, reason). Falls back to the regime default when the forward
    track record is still too thin to mean anything."""
    try:
        from ml.portfolio_journal import compare_by_mandate
        board = compare_by_mandate()
    except Exception as e:  # noqa: BLE001
        logger.warning("scoreboard unavailable: %s", e)
        board = {}
    def _metric(v: dict):
        """Prefer expectancy (stop-aware, per-position) over the older
        proposal-average when the new ruler has graded enough positions."""
        if v.get("expectancy_pct") is not None and v.get("positions_scored", 0) >= 10:
            return v["expectancy_pct"], "expectancy"
        return v.get("avg_realized_pct"), "avg realized"

    scored = {m: v for m, v in board.items()
              if v.get("scored", 0) >= _MIN_SCORED
              and _metric(v)[0] is not None
              and m != "?"}
    # Only follow the scoreboard when the leader has actually MADE money.
    # Picking the least-bad of several losing mandates would institutionalize
    # a losing strategy; a flat-or-negative board means hold the regime default.
    winners = {m: v for m, v in scored.items() if _metric(v)[0] > 0}
    if winners:
        m = max(winners, key=lambda k: _metric(winners[k])[0])
        v = winners[m]
        val, kind = _metric(v)
        return m, (f"scoreboard leader: {val:+.2f}% {kind} "
                   f"over {v.get('positions_scored') or v['scored']} scored "
                   f"(hit rate {v.get('hit_rate_pct') or v.get('avg_hit_rate_pct') or 0:.0f}%)")
    if scored:
        return _FALLBACK_MANDATE, ("no mandate is positive on the forward "
                                   "scoreboard yet; regime default until one earns it")
    return _FALLBACK_MANDATE, (f"forward record too thin (<{_MIN_SCORED} scored "
                               f"proposals per mandate); regime default for '{regime}'")


def last_account(default: float = 100_000.0) -> float:
    """The most recently journaled account value, so unattended builds keep
    sizing against what the user last used."""
    try:
        from ml.portfolio_journal import list_proposals, load_proposal
        props = list_proposals()
        if props:
            acct = float(load_proposal(props[-1]).get("account", 0) or 0)
            if acct > 0:
                return acct
    except Exception:
        pass
    return default


def choose_auto_settings() -> dict:
    """Full autonomous configuration for a portfolio build. Deterministic."""
    try:
        from ml.portfolio_engine import detect_regime
        regime = detect_regime()
    except Exception:
        regime = "neutral"
    preset = _REGIME_PRESETS.get(regime, _REGIME_PRESETS["neutral"])
    mandate, why = best_mandate(regime)
    cfg = {
        "regime": regime,
        "mandate": mandate,
        "mandate_reason": why,
        "account": last_account(),
        "limit": 800,                       # Open-market universe cap
        "source": "Open market",
        **preset,
    }
    logger.info("auto settings: %s", cfg)
    return cfg


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    import json
    print(json.dumps(choose_auto_settings(), indent=2))
