"""
resistance_analyzer.py — Chapter 4 "Refining The Buying Process".

Once a stock has passed the Stage 2 + Volume + RS gates from
stage_analyzer.py, Weinstein's Chapter 4 asks the next question: among
all confirmed BUY candidates, which one has the clearest path higher?
The answer is whichever has the least overhead resistance.

This module quantifies overhead resistance for a single ticker on the
weekly timeframe over a ten-year window:

    1. ZigZag swing-high detection at 5 percent reversal threshold
    2. Cluster nearby swing highs into resistance zones (±3% band)
    3. Recency-weighted overhead supply score
    4. Virgin territory flag — price at/above the 10-year high
    5. Final grade: A+, A, B, or C reflecting path clarity

Caching:
    Results are persisted to data/cache/resistance/{ticker}.json keyed
    by the latest weekly bar date so we recompute only when a fresh
    weekly close arrives.

Grade scheme:
    A+  Virgin territory (price at/above 10-year high — zero overhead)
    A   Nearest resistance > 25% above current price (clean runway)
    B   Nearest resistance 10–25% above (moderate overhead)
    C   Nearest resistance < 10% above (heavy near-term overhead)

Public interface:
    analyze_resistance(ticker, use_cache=True) -> ResistanceResult
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

ZIGZAG_THRESHOLD = 0.05            # 5% reversal confirms a swing
CLUSTER_BAND_PCT = 0.03            # swing highs within ±3% form one zone
RECENCY_DECAY_YEARS = 3.0          # exp(-age/3) weighting; old resistance fades
HISTORY_YEARS = 10                  # depth of the long-range view
VIRGIN_TERR_PROXIMITY = 0.98       # within 2% of 10y high counts as virgin

# Grade thresholds (cumulative; first matching wins)
GRADE_A_MIN_DISTANCE_PCT = 0.25    # nearest resistance > 25% above price
GRADE_B_MIN_DISTANCE_PCT = 0.10    # nearest resistance 10–25% above price
                                    # under 10% is grade C

_PROJECT_ROOT = Path(__file__).parent.parent
_CACHE_DIR = _PROJECT_ROOT / "data" / "cache" / "resistance"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ResistanceZone:
    """A clustered band of prior swing highs that acts as overhead supply."""
    price: float                # zone centroid in dollars
    touches: int                # number of distinct swing highs in this cluster
    age_years: float            # years since the most recent touch
    volume_sum: float           # sum of volume on the swing-high bars
    strength: float             # log(1+touches) * log(1+vol) * recency_weight


@dataclass
class ResistanceResult:
    """Output of analyze_resistance() for one ticker."""
    ticker: str
    last_close: float = 0.0
    last_bar_date: Optional[str] = None     # ISO date of latest weekly bar
    history_years: float = 0.0              # actual depth of data available

    zones: list[ResistanceZone] = field(default_factory=list)
    nearest_resistance: Optional[float] = None
    nearest_resistance_pct: Optional[float] = None    # signed, +0.10 = 10% above
    virgin_territory: bool = False
    ten_year_high: Optional[float] = None
    overhead_supply_score: float = 0.0

    grade: str = "n/a"                       # A+, A, B, C, or n/a
    grade_narrative: str = ""
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# yfinance loader
# ---------------------------------------------------------------------------

def _fetch_weekly(ticker: str, years: int = HISTORY_YEARS) -> Optional[pd.DataFrame]:
    """Fetch weekly OHLCV from yfinance. Returns None on any failure."""
    try:
        import yfinance as yf
        df = yf.Ticker(ticker).history(
            period=f"{years}y", interval="1wk", auto_adjust=True,
        )
        if df is None or df.empty or len(df) < 10:
            return None
        return df
    except Exception as exc:
        logger.warning(f"yfinance weekly fetch failed for {ticker}: {exc}")
        return None


# ---------------------------------------------------------------------------
# ZigZag swing-high detection
# ---------------------------------------------------------------------------

def _detect_swing_highs(weekly: pd.DataFrame,
                        threshold: float = ZIGZAG_THRESHOLD) -> list[dict]:
    """Classic ZigZag algorithm on weekly bars.

    Walks the High/Low series tracking the most extreme price seen since the
    last confirmed pivot. A pivot is confirmed when price reverses by at
    least `threshold` from that extreme. The function returns only the
    confirmed swing highs as a list of {date, price, volume} dicts.

    Returning swing highs (not lows) because Chapter 4 cares about overhead
    supply, which is created by prior peaks where buyers got trapped.
    """
    if weekly is None or weekly.empty or len(weekly) < 10:
        return []

    highs = weekly["High"].values
    lows = weekly["Low"].values
    volumes = weekly["Volume"].values
    dates = weekly.index

    swing_highs: list[dict] = []

    # State machine:
    #   direction =  1  → currently tracking a potential high (uptrend leg)
    #   direction = -1  → currently tracking a potential low  (downtrend leg)
    #   direction =  0  → uninitialized (first bar)
    direction = 0
    pivot_idx = 0
    pivot_high = highs[0]
    pivot_low = lows[0]

    # We need to track separate pivot indices for the high and low legs so
    # that confirming a swing high (which uses pivot_high_idx) doesn't get
    # corrupted by a same-bar new-low update.
    pivot_high_idx = 0
    pivot_low_idx = 0

    for i in range(1, len(highs)):
        # --- Looking-for-high branch ---
        if direction != -1:
            # 1) Update pivot if we made a new high this bar
            if highs[i] > pivot_high:
                pivot_high = highs[i]
                pivot_high_idx = i
                direction = 1
            # 2) Confirm pivot if this bar's low broke threshold below pivot.
            #    This is a SEPARATE check (not elif) so an outside bar that
            #    sets a new high AND a low below pivot*(1-threshold) still
            #    confirms a swing — the wide-range-bar case. Critical for
            #    catching parabolic runs that end in a spike-and-reverse week.
            if direction == 1 and lows[i] < pivot_high * (1 - threshold):
                swing_highs.append({
                    "date": dates[pivot_high_idx],
                    "price": float(pivot_high),
                    "volume": float(volumes[pivot_high_idx]),
                })
                direction = -1
                pivot_low = lows[i]
                pivot_low_idx = i
                # Skip the looking-for-low branch this iteration; we just
                # entered that state mid-bar.
                continue

        # --- Looking-for-low branch ---
        if direction == -1:
            if lows[i] < pivot_low:
                pivot_low = lows[i]
                pivot_low_idx = i
            # Same symmetry: independent check so an outside bar can flip us
            # back to tracking high in the same iteration.
            if highs[i] > pivot_low * (1 + threshold):
                direction = 1
                pivot_high = highs[i]
                pivot_high_idx = i

    return swing_highs


# ---------------------------------------------------------------------------
# Clustering swing highs into resistance zones
# ---------------------------------------------------------------------------

def _cluster_zones(swing_highs: list[dict],
                   current_price: float,
                   today: datetime) -> list[ResistanceZone]:
    """Group swing highs that lie within CLUSTER_BAND_PCT of one another.

    Only zones strictly ABOVE current_price are returned — those are the
    overhead supply. Zones below current price are former resistance that
    has been overcome (Weinstein: support once broken).

    Returns zones sorted by price ascending. The first zone is therefore
    the NEAREST resistance, which the grader uses for the distance test.
    """
    if not swing_highs:
        return []

    overhead = [sh for sh in swing_highs if sh["price"] > current_price]
    if not overhead:
        return []

    overhead.sort(key=lambda x: x["price"])

    # Greedy clustering — start a new cluster whenever the next price exceeds
    # the running centroid by more than the band width.
    clusters: list[list[dict]] = []
    current: list[dict] = [overhead[0]]
    for sh in overhead[1:]:
        centroid = sum(x["price"] for x in current) / len(current)
        if (sh["price"] - centroid) / centroid > CLUSTER_BAND_PCT:
            clusters.append(current)
            current = [sh]
        else:
            current.append(sh)
    clusters.append(current)

    zones: list[ResistanceZone] = []
    for cluster in clusters:
        prices = [x["price"] for x in cluster]
        vols = [x["volume"] for x in cluster]
        dates_list = [x["date"] for x in cluster]

        centroid = sum(prices) / len(prices)
        most_recent = max(dates_list)
        # most_recent is a pandas Timestamp — convert to datetime for arithmetic
        if hasattr(most_recent, "to_pydatetime"):
            most_recent_dt = most_recent.to_pydatetime()
            # Strip tzinfo if present so subtraction with today (naive) works
            if most_recent_dt.tzinfo is not None:
                most_recent_dt = most_recent_dt.replace(tzinfo=None)
        else:
            most_recent_dt = most_recent
        age_days = max(0, (today - most_recent_dt).days)
        age_years = age_days / 365.25

        total_vol = sum(vols)
        recency = math.exp(-age_years / RECENCY_DECAY_YEARS)
        # Strength: more touches and higher volume → stronger resistance;
        # older zones → recency-decayed. The log compresses runaway volume
        # so high-volume megacaps don't dominate.
        strength = (math.log(1.0 + len(cluster))
                    * math.log(1.0 + total_vol)
                    * recency)

        zones.append(ResistanceZone(
            price=round(centroid, 2),
            touches=len(cluster),
            age_years=round(age_years, 2),
            volume_sum=float(total_vol),
            strength=round(strength, 4),
        ))

    return zones


# ---------------------------------------------------------------------------
# Grade derivation
# ---------------------------------------------------------------------------

def _derive_grade(result: ResistanceResult) -> tuple[str, str]:
    """Map (virgin_territory, nearest_resistance_pct) to a grade and narrative.

    The narrative is plain English the UI can show under the grade chip.
    """
    if result.virgin_territory:
        return "A+", (
            "Open sky. Price is at or above the 10-year high — no overhead "
            "supply exists on the long-range chart. The ideal case: nobody "
            "is trapped long above, so buying meets no resistance."
        )
    if result.nearest_resistance_pct is None:
        # No zones above current price but not virgin (rare edge case — e.g.
        # very short price history with no swing highs detected yet).
        return "A", (
            "No measurable overhead resistance above current price within "
            "the available history. Treat as a fresh leadership candidate."
        )
    pct = result.nearest_resistance_pct
    if pct > GRADE_A_MIN_DISTANCE_PCT:
        return "A", (
            f"Nearest overhead resistance sits {pct:.0%} above current "
            f"price — well beyond the 25% threshold. Path higher is clear."
        )
    if pct >= GRADE_B_MIN_DISTANCE_PCT:
        return "B", (
            f"Nearest overhead resistance is {pct:.0%} above current "
            f"price — moderate ceiling. Stock will need fresh buying power "
            f"to push through this zone."
        )
    return "C", (
        f"Nearest overhead resistance is only {pct:.0%} above current "
        f"price — heavy near-term supply. Getting through this zone will "
        f"absorb most of a breakout's buying power."
    )


# ---------------------------------------------------------------------------
# Disk cache (keyed by latest weekly bar date)
# ---------------------------------------------------------------------------

def _cache_path(ticker: str) -> Path:
    return _CACHE_DIR / f"{ticker.upper()}.json"


def _load_cache(ticker: str, last_bar_date: str) -> Optional[ResistanceResult]:
    p = _cache_path(ticker)
    if not p.exists():
        return None
    try:
        with open(p) as f:
            data = json.load(f)
        if data.get("last_bar_date") != last_bar_date:
            return None  # stale — fresh weekly bar arrived
        zone_dicts = data.pop("zones", [])
        zones = [ResistanceZone(**z) for z in zone_dicts]
        r = ResistanceResult(**data)
        r.zones = zones
        return r
    except Exception as exc:
        logger.debug(f"resistance cache read failed for {ticker}: {exc}")
        return None


def _save_cache(ticker: str, result: ResistanceResult) -> None:
    p = _cache_path(ticker)
    try:
        with open(p, "w") as f:
            json.dump(asdict(result), f, default=str)
    except Exception as exc:
        logger.warning(f"resistance cache write failed for {ticker}: {exc}")


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def analyze_resistance(ticker: str, use_cache: bool = True) -> ResistanceResult:
    """Compute resistance grade and supporting metrics for `ticker`.

    Parameters
    ----------
    ticker : str
    use_cache : if True, return cached result when the latest weekly bar
        date matches the cached run. Caches live in data/cache/resistance/.

    Returns
    -------
    ResistanceResult with grade, zones, virgin_territory, and narrative.
    On failure, returns a result with .error set and grade="n/a".
    """
    ticker = ticker.upper()
    weekly = _fetch_weekly(ticker, years=HISTORY_YEARS)
    if weekly is None or weekly.empty:
        return ResistanceResult(
            ticker=ticker,
            error="no weekly history available from yfinance",
        )

    last_bar_date = weekly.index[-1].strftime("%Y-%m-%d")
    last_close = float(weekly["Close"].iloc[-1])

    # Cache short-circuit
    if use_cache:
        cached = _load_cache(ticker, last_bar_date)
        if cached is not None:
            return cached

    history_years = (weekly.index[-1] - weekly.index[0]).days / 365.25
    swing_highs = _detect_swing_highs(weekly, threshold=ZIGZAG_THRESHOLD)
    ten_year_high = float(weekly["High"].max())

    # Virgin territory: current price within 2% of (or above) the highest
    # swing high seen in the lookback window. We use 98% of the max as the
    # threshold so a name that pulled back 1% from an all-time high still
    # counts as virgin.
    if swing_highs:
        max_swing = max(sh["price"] for sh in swing_highs)
        virgin = last_close >= max_swing * VIRGIN_TERR_PROXIMITY
    else:
        # No swing highs detected (one-way trend or very short history) —
        # compare directly against the 10-year high.
        virgin = last_close >= ten_year_high * VIRGIN_TERR_PROXIMITY

    today = datetime.now()
    zones = _cluster_zones(swing_highs, last_close, today)

    nearest = None
    nearest_pct = None
    if zones and not virgin:
        nearest = zones[0].price
        nearest_pct = (nearest - last_close) / last_close

    overhead_supply = sum(z.strength for z in zones)

    result = ResistanceResult(
        ticker=ticker,
        last_close=round(last_close, 2),
        last_bar_date=last_bar_date,
        history_years=round(history_years, 2),
        zones=zones,
        nearest_resistance=round(nearest, 2) if nearest is not None else None,
        nearest_resistance_pct=(
            round(nearest_pct, 4) if nearest_pct is not None else None
        ),
        virgin_territory=virgin,
        ten_year_high=round(ten_year_high, 2),
        overhead_supply_score=round(overhead_supply, 4),
    )

    grade, narrative = _derive_grade(result)
    result.grade = grade
    result.grade_narrative = narrative

    if use_cache:
        _save_cache(ticker, result)

    return result


def clear_resistance_cache() -> int:
    """Wipe the disk cache. Returns count of files removed."""
    count = 0
    for p in _CACHE_DIR.glob("*.json"):
        try:
            p.unlink()
            count += 1
        except Exception:
            pass
    return count


def format_result(r: ResistanceResult) -> str:
    """One-line summary for the CLI test harness."""
    if r.error:
        return f"{r.ticker}: ERROR — {r.error}"
    nearest_str = "n/a"
    if r.nearest_resistance is not None:
        nearest_str = (
            f"${r.nearest_resistance:,.2f} "
            f"({r.nearest_resistance_pct:+.1%})"
        )
    return (
        f"{r.ticker:<6}  Grade {r.grade:<3}  "
        f"close ${r.last_close:>8.2f}  "
        f"10y high ${r.ten_year_high:>8.2f}  "
        f"virgin {str(r.virgin_territory):<5}  "
        f"nearest {nearest_str:<22}  "
        f"zones {len(r.zones):>2}  "
        f"overhead {r.overhead_supply_score:>6.2f}"
    )


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    tickers = sys.argv[1:] if len(sys.argv) > 1 else [
        "NVDA",   # likely virgin / A+
        "O",      # mature REIT, mid grade
        "INTC",   # heavy overhead, likely C
        "AAPL",
        "TSLA",
        "PLTR",
    ]
    print(f"Resistance analysis on {len(tickers)} tickers...\n")
    for t in tickers:
        r = analyze_resistance(t, use_cache=False)
        print(format_result(r))
        if not r.error:
            print(f"          {r.grade_narrative}")
