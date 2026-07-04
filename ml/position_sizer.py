"""
position_sizer.py — Risk-budget position sizing for confirmed buys.

The Trader scan now flags entry quality, regime, earnings blackout, and
sentiment. None of that helps if the user enters too big and a clean
breakout fails. Weinstein Ch. 7: never risk more than 1 to 2% of capital
on one trade, and cap any single position at a sensible fraction of the
portfolio so concentration risk does not sneak in via large bets.

This module is a pure function. No I/O. The Trader Mode card reads the
output of size_position(...) and renders three values:

    shares       — round-lot whole shares to buy
    dollar_risk  — dollars at risk if the stop trips
    bound_by     — "risk", "position cap", "capital available",
                   or "no setup" so the user sees which limit bound it.

If the trade has no usable entry / stop (Stage 1, 3, or 4, or any
missing price), the sizer returns 0 shares and a "no setup" reason
instead of guessing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class PositionSize:
    shares: int = 0
    dollar_exposure: float = 0.0      # shares * entry
    dollar_risk: float = 0.0          # shares * risk_per_share
    risk_per_share: float = 0.0
    bound_by: str = "no setup"
    entry_price: float = 0.0
    stop_loss: float = 0.0
    note: str = ""


def size_position(capital: float,
                  risk_pct: float,
                  max_position_pct: float,
                  entry_price: float,
                  stop_loss: float,
                  direction: str = "LONG") -> PositionSize:
    """Return a PositionSize for one trade.

    Parameters
    ----------
    capital : float
        Total portfolio capital in dollars.
    risk_pct : float
        Fraction of capital risked on this trade (0.01 = 1%).
    max_position_pct : float
        Fraction of capital allowed in any single name (0.05 = 5%).
    entry_price : float
        Buy-stop trigger for a long, sell-stop trigger for a short.
    stop_loss : float
        Where the position is invalidated (the 30-week MA, for
        Weinstein classic).
    direction : str
        "LONG" or "SHORT". Determines the risk_per_share sign.

    Returns
    -------
    PositionSize with shares = 0 and bound_by = "no setup" when the
    inputs are not a usable trade (missing prices, stop on the wrong
    side of entry, zero capital).
    """
    out = PositionSize(entry_price=entry_price, stop_loss=stop_loss)

    if capital <= 0 or risk_pct <= 0 or max_position_pct <= 0:
        out.note = "capital or risk inputs must be positive"
        return out

    if entry_price <= 0 or stop_loss <= 0:
        out.note = "no entry or stop on this candidate"
        return out

    if direction == "LONG":
        if stop_loss >= entry_price:
            out.note = "long stop must sit below the entry"
            return out
        risk_per_share = entry_price - stop_loss
    elif direction == "SHORT":
        if stop_loss <= entry_price:
            out.note = "short stop must sit above the entry"
            return out
        risk_per_share = stop_loss - entry_price
    else:
        out.note = f"unsupported direction {direction!r}"
        return out

    out.risk_per_share = risk_per_share

    # Three independent caps, take the smallest.
    dollar_risk_budget = capital * risk_pct
    shares_by_risk = int(dollar_risk_budget // risk_per_share)

    position_dollar_cap = capital * max_position_pct
    shares_by_position = int(position_dollar_cap // entry_price)

    shares_by_capital = int(capital // entry_price)

    candidates = {
        "risk":             shares_by_risk,
        "position cap":     shares_by_position,
        "capital available": shares_by_capital,
    }
    bound_by = min(candidates, key=lambda k: candidates[k])
    shares = max(0, candidates[bound_by])

    out.shares = shares
    out.dollar_exposure = round(shares * entry_price, 2)
    out.dollar_risk = round(shares * risk_per_share, 2)
    out.bound_by = bound_by if shares > 0 else "below 1 share"
    out.note = (
        f"risk per share ${risk_per_share:.2f} "
        f"(entry ${entry_price:.2f} to stop ${stop_loss:.2f})"
    )
    return out


def size_from_result(r,
                     capital: float,
                     risk_pct: float,
                     max_position_pct: float
                     ) -> PositionSize:
    """Read entry and stop directly off a StageResult and size it.

    Returns a PositionSize with bound_by = "no setup" when the result
    has no TradeSetup attached.
    """
    setup = getattr(r, "trade_setup", None)
    if setup is None or not getattr(setup, "applicable", False):
        return PositionSize(note="no actionable trade setup")
    return size_position(
        capital=capital,
        risk_pct=risk_pct,
        max_position_pct=max_position_pct,
        entry_price=float(getattr(setup, "entry_price", 0.0) or 0.0),
        stop_loss=float(getattr(setup, "stop_loss", 0.0) or 0.0),
        direction=getattr(setup, "direction", "LONG"),
    )


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # $100K portfolio, 1% risk per trade, 5% max position.
    p = size_position(100000, 0.01, 0.05,
                      entry_price=50.0, stop_loss=45.0, direction="LONG")
    print(p)
    p2 = size_position(100000, 0.01, 0.05,
                       entry_price=5.0, stop_loss=4.5, direction="LONG")
    # Tight stop, small denominator, should be bound by position cap
    print(p2)
