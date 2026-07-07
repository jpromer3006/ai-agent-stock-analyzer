"""
my_portfolio.py - "My Holdings": the user's REAL portfolio, tracked only.

The feature JP asked for (2026-07-06): the owner registers their actual
positions (here, the Chase book) in data/journal/my_portfolio.json and OROS
watches it every morning with the same discipline it applies to its own paper
book. House rule: EVERY position has a stop.

What it does daily, token-free:
  - P&L per holding vs cost basis, plus the day's move.
  - Margin math made honest: net equity, leverage ratio, and how much a
    10% market drop would move the equity.
  - Stage analysis per holding -> Today's Actions for the REAL book
    (raise stop / review Stage 3 / exit on invalidation or stop breach).
  - Stop RATCHET: when the 30-week MA rises above the stored stop, the stop
    moves up and is written back to the JSON (never down) - the journal
    commit makes the ratchet persistent across cloud runs.

OROS never trades, never logs into any brokerage, and this module gives
system readings, not personalized advice. The decisions stay human.

    PYTHONPATH=. python3 ml/my_portfolio.py
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_BOOK = Path(__file__).resolve().parent.parent / "data" / "journal" / "my_portfolio.json"


def load_book() -> dict | None:
    try:
        return json.loads(_BOOK.read_text()) if _BOOK.exists() else None
    except Exception as e:  # noqa: BLE001
        logger.warning("my_portfolio unreadable: %s", e)
        return None


def _save_book(book: dict) -> None:
    try:
        book["updated"] = f"{datetime.now():%Y-%m-%d}"
        _BOOK.write_text(json.dumps(book, indent=2))
    except Exception as e:  # noqa: BLE001
        logger.warning("my_portfolio save failed: %s", e)


def analyze_book() -> dict | None:
    """Full daily read of the real book. Returns None when no book exists."""
    book = load_book()
    if not book or not book.get("holdings"):
        return None
    from data import price_cache
    from ml.stage_analyzer import analyze_stage

    tickers = [h["ticker"] for h in book["holdings"]]
    try:
        price_cache.refresh_prices(tickers)
    except Exception:
        pass

    rows, actions = [], []
    mv = day_total = 0.0
    cost_total = 0.0
    ratcheted = False
    for h in book["holdings"]:
        t = h["ticker"].upper()
        df = price_cache.get_history(t)
        if df is None or len(df) < 2 or "Close" not in df.columns:
            continue
        c = df["Close"].dropna()
        last, prev = float(c.iloc[-1]), float(c.iloc[-2])
        shares = float(h.get("shares") or 0)
        basis = float(h.get("cost_basis") or last)
        stop = float(h.get("stop") or 0.0)

        stage, setup, invalid = None, "", False
        try:
            r = analyze_stage(t, df=df, use_cache=True)
            stage, setup, invalid = r.stage, r.setup_type, bool(r.is_invalidated)
            # Stop ratchet: Weinstein trails the stop up under the rising
            # 30-week MA. Never down, and never above the current price.
            new_stop = round(float(r.ma_30w), 2)
            if new_stop > stop * 1.005 and new_stop < last:
                actions.append(f"RAISE STOP {t}: {stop:.2f} -> {new_stop:.2f} "
                               "(ratchet under the rising 30-week MA)")
                h["stop"] = new_stop
                stop = new_stop
                ratcheted = True
        except Exception:
            pass

        # Action logic, most urgent first.
        if stop and last < stop:
            actions.append(f"EXIT {t}: closed {last:.2f}, below your stop {stop:.2f}")
        elif invalid or stage == 4:
            actions.append(f"EXIT {t}: completed weekly close below the 30-week MA "
                           f"(Stage {stage} - Weinstein invalidation)")
        elif stage == 3:
            actions.append(f"REVIEW {t}: Stage 3 (topping) - consider selling into "
                           "strength; no new money")

        val = shares * last
        mv += val
        cost_total += shares * basis
        day_total += shares * (last - prev)
        rows.append({
            "ticker": t, "shares": shares, "basis": basis, "last": last,
            "stop": stop, "stage": stage if stage is not None else "?",
            "day_pct": 100 * (last / prev - 1), "day_usd": shares * (last - prev),
            "pnl_pct": 100 * (last / basis - 1) if basis else 0.0,
            "pnl_usd": val - shares * basis,
            "below_stop": bool(stop) and last < stop,
        })
    if ratcheted:
        _save_book(book)

    debt = float(book.get("margin_debt") or 0.0)
    equity = mv - debt
    leverage = mv / equity if equity > 0 else float("inf")
    # Loss if every stop were honored tomorrow (the real book's "heat").
    stop_loss_total = sum(r["shares"] * max(0.0, r["last"] - r["stop"])
                          for r in rows if r["stop"])
    rows.sort(key=lambda r: -r["pnl_usd"])
    return {
        "name": book.get("name", "My Holdings"),
        "rows": rows, "actions": actions,
        "market_value": mv, "margin_debt": debt, "equity": equity,
        "leverage": leverage,
        "day_usd": day_total,
        "total_pnl_usd": mv - cost_total,
        "total_pnl_pct": 100 * (mv / cost_total - 1) if cost_total else 0.0,
        "loss_to_stops": stop_loss_total,
        "loss_to_stops_pct_equity": 100 * stop_loss_total / equity if equity > 0 else 0.0,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    b = analyze_book()
    if not b:
        print("no book on file")
    else:
        print(f"{b['name']}: equity ${b['equity']:,.0f} "
              f"(positions ${b['market_value']:,.0f} - margin ${b['margin_debt']:,.0f}) "
              f"leverage {b['leverage']:.2f}x")
        print(f"day {b['day_usd']:+,.0f} | total vs basis {b['total_pnl_usd']:+,.0f} "
              f"({b['total_pnl_pct']:+.1f}%)")
        for r in b["rows"]:
            print(f"  {r['ticker']:6} stage {r['stage']}  last {r['last']:>9.2f} "
                  f"stop {r['stop']:>9.2f}  day {r['day_pct']:+5.1f}%  "
                  f"P&L ${r['pnl_usd']:+8,.0f} ({r['pnl_pct']:+.1f}%)")
        for a in b["actions"]:
            print("  !", a)
