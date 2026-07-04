"""
confidence_scorer.py — Structure B weighted Setup Confidence Score
with two hard caps.

The Chartist Strategist (sixteenth specialist) uses this to translate
the raw technical, fundamental, and timing signals into a single
0 to 100 score that the user can read in one glance.

Weighted inputs (six total, sum to 100):
    Chapter 4 ceiling (resistance grade)        22 percent
    Market weather (SPY stage + momentum)       22 percent
    Volume and RS gate magnitude                18 percent
    Sector specialist fundamentals verdict      18 percent
    Sector RS rank (Chapter 8 lightweight)      10 percent
    Calendar window (Chapter 3)                 10 percent

Hard caps applied AFTER the weighted score:
    Cap 1   if setup_type == "extended"     →   score capped at 40
            (Chapter 5: do not chase a stock that has already run)
    Cap 2   if SPY is in Stage 4 declining  →   score capped at 50
            (Chapter 8: no isolationism — never buy a strong stock into
            a declining broad market)

Verdict bands:
    85-100   High Conviction       all signals align
    70-84    Favorable             most signals align, minor cautions
    50-69    Mixed                 real conflicts between signals
    30-49    Weak                  multiple signals say wait
    0-29     Avoid                 most signals against entry

Public interface:
    compute_confidence(inputs: ConfidenceInputs) -> ConfidenceResult
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Configuration — weights, caps, bands
# ---------------------------------------------------------------------------

WEIGHTS = {
    "ceiling":      0.20,
    "weather":      0.20,
    "gates":        0.17,
    "fundamentals": 0.16,
    "sentiment":    0.10,    # NEW — Sentiment Analyst (17th specialist)
    "sector_rs":    0.09,
    "calendar":     0.08,
}
# Sanity check — should sum to 1.0
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9, (
    f"weight sum {sum(WEIGHTS.values())}"
)

CAP_EXTENDED = 40.0      # extended Stage 2 ceiling
CAP_SPY_STAGE4 = 50.0    # bear-market ceiling

BANDS = [
    (85.0, "High Conviction", "#3fb950"),
    (70.0, "Favorable",       "#56d364"),
    (50.0, "Mixed",           "#ffc107"),
    (30.0, "Weak",            "#ff7b1a"),
    (0.0,  "Avoid",           "#f85149"),
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ConfidenceInputs:
    """All the signals the scorer needs, gathered from upstream modules.

    Optional fields default to neutral so partial inputs still produce a
    meaningful score (with a smaller swing).
    """
    ticker: str

    # From StageResult (always present)
    stage: int = 2
    setup_type: str = "n/a"               # fresh / watching / extended / n/a
    resistance_grade: str = "n/a"         # A+, A, B, C, n/a
    volume_surge: float = 1.0             # 5d max / 50d avg
    mansfield_rs: float = 0.0             # signed
    weinstein_buy_confirmed: bool = False

    # From market_regime (likely present)
    spy_stage: int = 0
    momentum_tailwind: bool = False

    # From calendar_context (likely present)
    current_month_pct: float = 0.5
    weak_month: bool = False

    # From sector_rs (likely present)
    sector_rank: int = 0                  # 1-11, 0 if unknown
    sector_total: int = 11
    sector_is_tailwind: bool = False
    sector_is_headwind: bool = False

    # From sector specialist (optional — may be None if not yet run)
    fundamentals_support_hold: Optional[bool] = None

    # From sentiment_analyst (optional — may be None if not yet run)
    sentiment_score_0_100: Optional[float] = None    # the analyst's 0-100 score
    sentiment_band: Optional[str] = None             # e.g. "Bullish"


@dataclass
class ConfidenceResult:
    ticker: str
    score: float = 50.0                       # 0-100
    band: str = "Mixed"
    band_color: str = "#ffc107"
    rationale: str = ""
    component_scores: dict = field(default_factory=dict)
    caps_applied: list = field(default_factory=list)
    raw_score_before_caps: float = 50.0       # for transparency


# ---------------------------------------------------------------------------
# Per-component scoring (each returns 0-100)
# ---------------------------------------------------------------------------

def _score_ceiling(grade: str) -> float:
    """Chapter 4 resistance grade to 0-100 score."""
    return {
        "A+": 95.0,
        "A":  78.0,
        "B":  55.0,
        "C":  25.0,
        "n/a": 50.0,
    }.get(grade, 50.0)


def _score_weather(spy_stage: int, momentum_tailwind: bool) -> float:
    """SPY stage and momentum to weather score."""
    if spy_stage == 2:
        return 90.0 if momentum_tailwind else 70.0
    if spy_stage == 1:
        return 50.0
    if spy_stage == 3:
        return 40.0
    if spy_stage == 4:
        return 20.0
    return 50.0  # unknown


def _score_gates(volume_surge: float, mansfield_rs: float,
                 buy_confirmed: bool) -> float:
    """Volume + RS magnitude score. Assumes user is looking at a BUY
    candidate so the binary pass/fail is already filtered; this scores
    the strength of the magnitude.
    """
    if not buy_confirmed:
        # Both gates need to pass to be in confirmed-BUY territory.
        # If they don't, score reflects that.
        return 30.0
    # Vol component
    if volume_surge >= 3.0:
        vol_score = 100.0
    elif volume_surge >= 2.0:
        vol_score = 85.0
    elif volume_surge >= 1.5:
        vol_score = 65.0
    else:
        vol_score = 40.0
    # RS component
    if mansfield_rs >= 10.0:
        rs_score = 100.0
    elif mansfield_rs >= 5.0:
        rs_score = 80.0
    elif mansfield_rs >= 1.0:
        rs_score = 60.0
    elif mansfield_rs > 0:
        rs_score = 50.0
    else:
        rs_score = 30.0
    return (vol_score + rs_score) / 2.0


def _score_fundamentals(supports_hold: Optional[bool]) -> float:
    """Sector specialist's fundamentals verdict to score."""
    if supports_hold is None:
        return 50.0  # neutral when specialist hasn't run
    return 80.0 if supports_hold else 25.0


def _score_sentiment(sentiment_score: Optional[float]) -> float:
    """Sentiment Analyst's 0-100 score is already on the right scale.

    The Sentiment Analyst produces a 0-100 reading directly, so we
    pass it through. None (analyst not run) is neutral 50.
    """
    if sentiment_score is None:
        return 50.0
    return max(0.0, min(100.0, float(sentiment_score)))


def _score_sector_rs(rank: int, total: int,
                     is_tailwind: bool, is_headwind: bool) -> float:
    """Chapter 8 sector rotation score."""
    if rank == 0 or total == 0:
        return 50.0
    if rank <= 2:
        return 90.0
    if rank <= 4:
        return 75.0
    if rank <= 7:
        return 50.0
    if rank <= 9:
        return 30.0
    return 15.0


def _score_calendar(month_pct: float, weak_month: bool) -> float:
    """Chapter 3 calendar window to score."""
    if month_pct >= 0.70:
        base = 85.0
    elif month_pct >= 0.60:
        base = 65.0
    elif month_pct >= 0.55:
        base = 55.0
    elif month_pct >= 0.50:
        base = 45.0
    elif month_pct >= 0.45:
        base = 35.0
    else:
        base = 20.0
    # Small additional drag for explicit weak-month flag
    if weak_month and month_pct < 0.60:
        base -= 5.0
    return max(0.0, min(100.0, base))


# ---------------------------------------------------------------------------
# Band lookup
# ---------------------------------------------------------------------------

def _band_for(score: float) -> tuple[str, str]:
    """Return (band_name, color_hex) for a score."""
    for threshold, name, color in BANDS:
        if score >= threshold:
            return name, color
    return "Avoid", "#f85149"


# ---------------------------------------------------------------------------
# Rationale composer (plain English)
# ---------------------------------------------------------------------------

def _build_rationale(score: float, band: str,
                     components: dict[str, float],
                     inputs: ConfidenceInputs,
                     caps_applied: list[str],
                     raw_before_caps: float) -> str:
    """Compose a 3-5 sentence rationale paragraph the dossier can render."""
    parts: list[str] = []
    parts.append(f"Setup Confidence: {score:.0f}%. {band}.")

    # Identify top 2 positive and bottom 2 negative components
    sorted_components = sorted(components.items(), key=lambda x: -x[1])
    positives = [(k, v) for k, v in sorted_components if v >= 65.0]
    negatives = [(k, v) for k, v in sorted_components if v <= 40.0]

    pretty_names = {
        "ceiling":      "the ceiling above current price",
        "weather":      "the broader market context",
        "gates":        "volume and relative strength",
        "fundamentals": "the fundamentals story",
        "sentiment":    "market sentiment (news, analysts, insiders, earnings)",
        "sector_rs":    "the sector rotation",
        "calendar":     "the calendar window",
    }

    if positives:
        items = ", ".join(pretty_names[k] for k, _ in positives[:3])
        parts.append(f"The strongest signals are {items}.")
    if negatives:
        items = ", ".join(pretty_names[k] for k, _ in negatives[:2])
        parts.append(f"The weakest signals are {items}.")

    # Cap explanations
    if "extended" in caps_applied:
        parts.append(
            f"Hard cap applied: {inputs.ticker} is Late Stage 2 — even with "
            f"a raw weighted score of {raw_before_caps:.0f}, Chapter 5 says "
            f"do not chase a stock that has already run, so the score is "
            f"capped at {CAP_EXTENDED:.0f} percent."
        )
    if "spy_stage4" in caps_applied:
        parts.append(
            f"Hard cap applied: SPY is in Stage 4 declining. Per Chapter 8 "
            f"(no isolationism), no stock buy gets full credit in a bear "
            f"market — confidence capped at {CAP_SPY_STAGE4:.0f} percent."
        )

    # Action guidance per band
    guidance = {
        "High Conviction": (
            "Most signals say yes. Consider a full position with a "
            "GTC stop at the 30-week MA."
        ),
        "Favorable": (
            "Setup is good with minor concerns. Take a starter "
            "position and add on confirmation."
        ),
        "Mixed": (
            "Real conflicts between signals. Wait for the weakest "
            "factor to improve, or size very small."
        ),
        "Weak": (
            "Multiple signals are flashing warning lights. Better "
            "to wait for the next setup."
        ),
        "Avoid": (
            "Most signals are against entry. Per Weinstein, "
            "patience now beats a forced trade."
        ),
    }
    parts.append(guidance.get(band, ""))

    return " ".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def compute_confidence(inputs: ConfidenceInputs) -> ConfidenceResult:
    """Compute the Setup Confidence Score for a single ticker.

    Pure function — no I/O, no API calls. Wire upstream modules to
    construct the ConfidenceInputs first, then call this.
    """
    # Per-component scores
    components = {
        "ceiling":      _score_ceiling(inputs.resistance_grade),
        "weather":      _score_weather(inputs.spy_stage,
                                        inputs.momentum_tailwind),
        "gates":        _score_gates(inputs.volume_surge,
                                      inputs.mansfield_rs,
                                      inputs.weinstein_buy_confirmed),
        "fundamentals": _score_fundamentals(inputs.fundamentals_support_hold),
        "sentiment":    _score_sentiment(inputs.sentiment_score_0_100),
        "sector_rs":    _score_sector_rs(inputs.sector_rank,
                                          inputs.sector_total,
                                          inputs.sector_is_tailwind,
                                          inputs.sector_is_headwind),
        "calendar":     _score_calendar(inputs.current_month_pct,
                                         inputs.weak_month),
    }

    # Weighted sum
    raw_score = sum(WEIGHTS[k] * v for k, v in components.items())

    # Hard caps
    caps_applied: list[str] = []
    final_score = raw_score
    if inputs.setup_type == "extended":
        if final_score > CAP_EXTENDED:
            caps_applied.append("extended")
            final_score = CAP_EXTENDED
    if inputs.spy_stage == 4:
        if final_score > CAP_SPY_STAGE4:
            caps_applied.append("spy_stage4")
            final_score = CAP_SPY_STAGE4

    # Band lookup
    band, color = _band_for(final_score)

    rationale = _build_rationale(
        score=final_score,
        band=band,
        components=components,
        inputs=inputs,
        caps_applied=caps_applied,
        raw_before_caps=raw_score,
    )

    return ConfidenceResult(
        ticker=inputs.ticker,
        score=round(final_score, 1),
        band=band,
        band_color=color,
        rationale=rationale,
        component_scores={k: round(v, 1) for k, v in components.items()},
        caps_applied=caps_applied,
        raw_score_before_caps=round(raw_score, 1),
    )


# ---------------------------------------------------------------------------
# CLI test — three illustrative cases
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("Case 1: ORA-style Confirmed BUY, virgin territory, fresh setup")
    r = compute_confidence(ConfidenceInputs(
        ticker="ORA",
        stage=2,
        setup_type="fresh",
        resistance_grade="A+",
        volume_surge=3.0,
        mansfield_rs=6.4,
        weinstein_buy_confirmed=True,
        spy_stage=2,
        momentum_tailwind=True,
        current_month_pct=0.48,
        weak_month=True,
        sector_rank=3,
        sector_total=11,
        sector_is_tailwind=True,
        fundamentals_support_hold=True,
    ))
    print(f"  Score: {r.score:.1f}  Band: {r.band}")
    print(f"  Components: {r.component_scores}")
    print(f"  Rationale: {r.rationale}\n")

    print("Case 2: MTSI-style Late Stage 2 — extended cap should fire")
    r = compute_confidence(ConfidenceInputs(
        ticker="MTSI",
        stage=2,
        setup_type="extended",
        resistance_grade="A+",
        volume_surge=1.6,
        mansfield_rs=65.5,
        weinstein_buy_confirmed=True,
        spy_stage=2,
        momentum_tailwind=True,
        current_month_pct=0.62,
        weak_month=False,
        sector_rank=1,
        sector_total=11,
        sector_is_tailwind=True,
        fundamentals_support_hold=True,
    ))
    print(f"  Score: {r.score:.1f}  Band: {r.band}  (raw before caps: "
          f"{r.raw_score_before_caps:.1f})")
    print(f"  Caps applied: {r.caps_applied}")
    print(f"  Rationale: {r.rationale}\n")

    print("Case 3: Stage 4 SPY environment — should cap at 50")
    r = compute_confidence(ConfidenceInputs(
        ticker="STK",
        stage=2,
        setup_type="fresh",
        resistance_grade="A+",
        volume_surge=2.5,
        mansfield_rs=10.0,
        weinstein_buy_confirmed=True,
        spy_stage=4,                            # bear market!
        momentum_tailwind=False,
        current_month_pct=0.62,
        weak_month=False,
        sector_rank=2,
        sector_total=11,
        sector_is_tailwind=True,
        fundamentals_support_hold=True,
    ))
    print(f"  Score: {r.score:.1f}  Band: {r.band}  (raw before caps: "
          f"{r.raw_score_before_caps:.1f})")
    print(f"  Caps applied: {r.caps_applied}")
    print(f"  Rationale: {r.rationale}")
