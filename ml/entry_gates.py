"""
entry_gates.py — Post-scan entry risk overlay.

The scan classifies each ticker with pure Weinstein stage analysis.
This overlay runs once afterward and answers a different question:
given everything we know, is this actually a good moment to enter?

It does three things, all annotated back onto each StageResult:

  1. Entry quality score (0-100, grade A to D). A transparent blend of
     signals already computed: extension above the 30-week MA, volume
     confirmation, how far the breakout has already run, RS strength,
     and (Step 3b) news sentiment on confirmed buys. The chase
     detector.

  2. Market regime gate. Weinstein Ch. 8: about 75% of stocks follow
     the market, so a Confirmed Buy in a topping or declining tape is
     downgraded. The gate sets entry_gate_status to pass, caution, or
     blocked, with a reason.

  3. (Step 3b) Earnings blackout and news sentiment checks, both
     scoped to confirmed buys only for cost discipline. Earnings inside
     the blackout window or a sufficiently negative news drumbeat
     downgrade pass to caution; neither blocks, because a single
     earnings print or a noisy news day shouldn't veto a clean
     technical setup. Both populate fields that the trade journal
     then freezes.

Runs before the journal snapshot so every gate decision is recorded.
Precedence: blocked > caution > pass.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# --- Entry quality sub-score weights (transparent, not back-fit) -----------
# Step 3b rebalance: sentiment introduced at 0.15. extension and volume
# trimmed by 0.05 each, freshness by 0.05. Total stays at 1.00.
_QUALITY_WEIGHTS = {
    "extension": 0.25,   # price not too far above the 30-week MA
    "volume":    0.25,   # breakout volume confirmed
    "freshness": 0.20,   # breakout has not already run far
    "rs":        0.15,   # relative strength vs SPY
    "sentiment": 0.15,   # VADER compound on latest headlines (3b)
}


def _extension_subscore(pct_above_ma: float) -> float:
    """1.0 when price sits modestly above the MA, decaying as it extends.
    Weinstein Ch. 5: do not chase a stock far from its 30-week MA."""
    p = pct_above_ma
    if p <= 0.08:
        return 1.0
    if p >= 0.40:
        return 0.0
    if p <= 0.25:
        return 1.0 - (p - 0.08) / (0.25 - 0.08) * 0.60   # 1.0 down to 0.40
    return 0.40 - (p - 0.25) / (0.40 - 0.25) * 0.40       # 0.40 down to 0.0


def _volume_subscore(volume_surge: float) -> float:
    """1.0 when breakout volume clears the 1.5x gate, poor below 1.0x."""
    v = volume_surge
    if v >= 1.5:
        return 1.0
    if v >= 1.0:
        return 0.30 + (v - 1.0) / 0.5 * 0.70
    return max(0.0, v) * 0.30


def _freshness_subscore(return_since_breakout: float) -> float:
    """1.0 for a breakout that has barely moved, decaying as it runs.
    A fresh entry sits near the breakout, not 40% past it."""
    r = return_since_breakout
    if r <= 0.10:
        return 1.0
    if r >= 0.40:
        return 0.20
    return 1.0 - (r - 0.10) / (0.40 - 0.10) * 0.80


def _rs_subscore(mansfield_rs: float) -> float:
    """RS must be positive for a confirmed buy at all; reward strength."""
    rs = mansfield_rs
    if rs <= 0.0:
        return 0.0
    if rs >= 6.0:
        return 1.0
    return 0.50 + (rs / 6.0) * 0.50


def _sentiment_subscore(compound) -> float:
    """VADER compound in [-1, +1] mapped to [0, 1] with neutral at 0.5.

    None (no headlines) is treated as neutral (0.5), not as a penalty:
    silence shouldn't punish a Stage 2 setup, only a clearly bad
    drumbeat should.
    """
    if compound is None:
        return 0.5
    c = max(-1.0, min(1.0, float(compound)))
    return 0.5 + c * 0.5


def _grade(score: float) -> str:
    if score >= 80:
        return "A"
    if score >= 65:
        return "B"
    if score >= 50:
        return "C"
    return "D"


def compute_entry_quality(r) -> tuple[float, str, list[str]]:
    """Return (score 0-100, grade A-D, reason notes) for one result.

    Reads `sentiment_score` from the result if Step 3b has populated
    it for this ticker (confirmed buys only). Otherwise the sentiment
    sub-score is neutral (0.5), so a non-confirmed Stage 2 name still
    gets a coherent grade.
    """
    ext = _extension_subscore(getattr(r, "pct_above_ma", 0.0))
    vol = _volume_subscore(getattr(r, "volume_surge", 0.0))
    fresh = _freshness_subscore(getattr(r, "return_since_breakout", 0.0))
    rs = _rs_subscore(getattr(r, "mansfield_rs", 0.0))
    sent = _sentiment_subscore(getattr(r, "sentiment_score", None))

    score01 = (
        _QUALITY_WEIGHTS["extension"] * ext
        + _QUALITY_WEIGHTS["volume"] * vol
        + _QUALITY_WEIGHTS["freshness"] * fresh
        + _QUALITY_WEIGHTS["rs"] * rs
        + _QUALITY_WEIGHTS["sentiment"] * sent
    )
    score = round(score01 * 100, 1)
    sent_raw = getattr(r, "sentiment_score", None)
    sent_note = (f"news sentiment compound {sent_raw:+.2f} (sub {sent:.2f})"
                 if sent_raw is not None
                 else f"news sentiment unavailable, treated as neutral "
                      f"(sub {sent:.2f})")
    notes = [
        f"extension {getattr(r, 'pct_above_ma', 0.0):+.1%} above 30wk MA "
        f"(sub {ext:.2f})",
        f"volume surge {getattr(r, 'volume_surge', 0.0):.2f}x (sub {vol:.2f})",
        f"breakout has run {getattr(r, 'return_since_breakout', 0.0):+.1%} "
        f"(sub {fresh:.2f})",
        f"Mansfield RS {getattr(r, 'mansfield_rs', 0.0):+.1f} (sub {rs:.2f})",
        sent_note,
    ]
    return score, _grade(score), notes


# --- Market regime gate ----------------------------------------------------
# Weinstein Ch. 8: in a topping or declining market, stop taking new longs.
_BLOCKING_REGIMES = {"BEAR", "BEAR_EARLY"}
_CAUTION_REGIMES = {"BULL_FADING", "NEUTRAL"}


def _is_confirmed_buy(r) -> bool:
    return (
        r.stage == 2
        and getattr(r, "weinstein_buy_confirmed", False)
        and getattr(r, "setup_type", "n/a") == "fresh"
    )


def _enrich_confirmed_buy(r) -> None:
    """Populate next_earnings_date + sentiment fields on one confirmed buy.

    Both lookups are quiet on failure: a missing earnings date or
    headline list must never block a scan. Runs before the entry
    quality score, so the sentiment sub-score sees the freshly
    populated value.
    """
    # Earnings calendar
    try:
        from ml.earnings_calendar import next_earnings
        iso, days = next_earnings(r.ticker)
        r.next_earnings_date = iso
        r.days_to_earnings = days
    except Exception as exc:
        logger.debug(f"entry_gates: earnings lookup failed for "
                     f"{r.ticker}: {exc}")
        r.next_earnings_date = None
        r.days_to_earnings = None

    # News sentiment
    try:
        from ml.sentiment_read import compute_sentiment
        compound, count, as_of = compute_sentiment(r.ticker)
        r.sentiment_score = compound
        r.sentiment_headline_count = count
        r.sentiment_as_of = as_of
    except Exception as exc:
        logger.debug(f"entry_gates: sentiment lookup failed for "
                     f"{r.ticker}: {exc}")
        r.sentiment_score = None
        r.sentiment_headline_count = 0
        r.sentiment_as_of = None


def apply_entry_gates(report) -> str:
    """Annotate every result in `report` with an entry quality score and
    a market-regime gate verdict. Mutates results in place.

    Returns the regime label used, for logging. The caller wraps this so
    a failure here never breaks the scan.
    """
    # 1. Market regime — computed once, applied to all long candidates.
    regime_label = "UNKNOWN"
    try:
        from ml.market_context import compute_market_regime
        regime = compute_market_regime(report)
        regime_label = "UNKNOWN" if regime.error else regime.regime
    except Exception as exc:
        logger.warning(f"entry_gates: regime lookup failed: {exc}")

    # 2. Pre-pass: fetch earnings + sentiment for confirmed buys only.
    # Doing this first lets compute_entry_quality see sentiment_score
    # so the quality grade reflects the news read.
    n_enriched = 0
    n_blackout = 0
    n_neg_sent = 0
    from ml.earnings_calendar import is_inside_blackout
    from ml.sentiment_read import caution_for_sentiment

    for r in report.results:
        if getattr(r, "error", None):
            continue
        if _is_confirmed_buy(r):
            _enrich_confirmed_buy(r)
            n_enriched += 1
            if is_inside_blackout(r.days_to_earnings):
                n_blackout += 1
            if caution_for_sentiment(r.sentiment_score):
                n_neg_sent += 1

    # 3. Per-result annotation.
    for r in report.results:
        if getattr(r, "error", None):
            continue

        # Entry quality score — for every Stage 2 name (the long side).
        if r.stage == 2:
            score, grade, _notes = compute_entry_quality(r)
            r.entry_quality_score = score
            r.entry_quality_grade = grade
        else:
            r.entry_quality_score = 0.0
            r.entry_quality_grade = ""

        # Regime gate — only meaningful for actionable long candidates,
        # i.e. fresh Stage 2 setups that already cleared both Weinstein
        # gates. Everything else stays "pass".
        status = "pass"
        reasons: list[str] = []
        if _is_confirmed_buy(r):
            # blocked dominates everything else.
            if regime_label in _BLOCKING_REGIMES:
                status = "blocked"
                reasons.append(
                    f"Market regime {regime_label}: Weinstein Ch. 8 says "
                    f"stop taking new longs when the market is topping or "
                    f"declining. About 75% of stocks follow the tape."
                )
            elif regime_label in _CAUTION_REGIMES:
                status = "caution"
                reasons.append(
                    f"Market regime {regime_label}: breadth is thinning. "
                    f"Be selective and reduce new risk."
                )

            # Stacked caution layers. Each adds a reason. None of these
            # override an existing blocked, but any one of them will
            # promote pass to caution.
            def _to_caution_with(reason: str) -> None:
                nonlocal status
                if status == "pass":
                    status = "caution"
                if status == "caution":
                    reasons.append(reason)

            # Earnings blackout.
            if is_inside_blackout(r.days_to_earnings):
                d = r.days_to_earnings
                date_str = r.next_earnings_date or "?"
                _to_caution_with(
                    f"Earnings blackout: next print {date_str} in "
                    f"{d} trading day{'s' if d != 1 else ''}. Avoid new "
                    f"entries inside the 5 day window."
                )

            # Negative news drumbeat.
            if caution_for_sentiment(r.sentiment_score):
                c = r.sentiment_score
                n = r.sentiment_headline_count
                _to_caution_with(
                    f"Sentiment compound {c:+.2f} across {n} headline"
                    f"{'s' if n != 1 else ''}: news drumbeat is negative "
                    f"ahead of entry."
                )

            # Weak entry quality is the last caution trigger.
            if status == "pass" and r.entry_quality_grade == "D":
                status = "caution"
                reasons.append(
                    f"Entry quality D ({r.entry_quality_score:.0f}/100): the "
                    f"entry is extended or unconfirmed even though the stage "
                    f"is valid."
                )

        r.entry_gate_status = status
        r.entry_gate_reasons = reasons

    blocked = sum(1 for r in report.results
                  if getattr(r, "entry_gate_status", "pass") == "blocked")
    cautioned = sum(1 for r in report.results
                    if getattr(r, "entry_gate_status", "pass") == "caution")
    logger.info(
        f"entry_gates: regime={regime_label}, enriched={n_enriched}, "
        f"earnings_blackout={n_blackout}, neg_sent={n_neg_sent}, "
        f"blocked={blocked}, caution={cautioned}"
    )
    return regime_label


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from ml.batch_scanner import scan_universe

    tickers = sys.argv[1:] if len(sys.argv) > 1 else ["WELL", "MTZ", "ORA", "GS"]
    report = scan_universe(tickers, use_cache=False)
    for r in report.results:
        if r.error:
            continue
        sent = (f"{r.sentiment_score:+.2f}" if r.sentiment_score is not None
                else "  na")
        earn = (f"{r.days_to_earnings}d" if r.days_to_earnings is not None
                else " na")
        print(f"{r.ticker:<6} stage {r.stage}  {r.action:<11} "
              f"Q {r.entry_quality_grade or '-'} ({r.entry_quality_score:.0f}) "
              f"sent {sent}  earn {earn}  gate={r.entry_gate_status}")
        for reason in r.entry_gate_reasons:
            print(f"        - {reason}")
