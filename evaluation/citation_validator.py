"""
citation_validator.py — Strict inline citation enforcement.

Per Lecture 9 rubric, every numerical claim in a memo must be traceable
to a source. This validator:
    1. Extracts all numerical claims from a memo (dollar amounts, percentages, ratios)
    2. For each claim, checks if a citation marker appears within N chars
    3. Returns a structured report with total/cited/uncited claims

Citation markers accepted:
    [Source: ...]           ← preferred, explicit
    (Source: ...)
    (SEC 10-K ...)
    (XBRL ...)
    (10-K Item ...)
    (FY20XX)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Numerical claim patterns — what counts as a claim needing a citation
CLAIM_PATTERNS = [
    # Dollar amounts: $5.2B, $1,234M, $500K, $10 million, $3.5 billion
    r"\$\s?[\d,]+\.?\d*\s?(?:[BMK]|billion|million|thousand)\b",
    # Percentages: 12.5%, 0.8%, +3%, -5%
    r"[+\-]?\d+\.?\d*\s?%",
    # Ratios / multiples: 1.5x, 0.8x, 12.3x
    r"\d+\.\d+\s?x\b",
    # Large integer dollar amounts: $1,234,567 (without suffix)
    r"\$\s?\d{1,3}(?:,\d{3})+(?:\.\d+)?",
]

# Citation markers — anything that indicates a source
CITATION_PATTERNS = [
    # Explicit [Source: ...] or (Source: ...)
    r"[\[\(]\s*Source\s*:\s*[^\]\)]+[\]\)]",
    # (SEC 10-K ...) or (10-K Item 7) — no explicit "Source:"
    r"\(\s*(?:SEC\s+)?10-[KQ][^\)]*\)",
    # (XBRL ...) or (SEC XBRL ...)
    r"\(\s*(?:SEC\s+)?XBRL[^\)]*\)",
    # (FY2024) or (FY 2024) — year with FY prefix
    r"\(\s*FY\s?20\d{2}[^\)]*\)",
    # (Item 1A), (Item 7), etc.
    r"\(\s*Item\s+\d+[A-Z]?[^\)]*\)",
    # (MD&A), (Risk Factors)
    r"\(\s*(?:MD&A|Risk\s+Factors|Business|Properties)[^\)]*\)",
    # yfinance or yf citation
    r"\(\s*yfinance[^\)]*\)",
]


@dataclass
class ClaimMatch:
    text: str
    start: int
    end: int
    claim_type: str
    cited: bool = False
    citation_text: Optional[str] = None
    citation_distance: Optional[int] = None


@dataclass
class ValidationReport:
    total_claims: int
    cited_claims: int
    uncited_claims: int
    citation_coverage: float  # 0.0-1.0
    claims: list[ClaimMatch] = field(default_factory=list)
    passes_strict_threshold: bool = False

    def summary(self) -> str:
        lines = [
            f"Citation Coverage: {self.citation_coverage:.0%} "
            f"({self.cited_claims}/{self.total_claims} claims cited)",
        ]
        if self.uncited_claims > 0:
            lines.append(f"\n⚠️  {self.uncited_claims} uncited numerical claims:")
            for c in self.claims:
                if not c.cited:
                    lines.append(f"  - '{c.text}' at position {c.start}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core validator
# ---------------------------------------------------------------------------

def _find_claims(text: str) -> list[ClaimMatch]:
    """Extract all numerical claims from text."""
    claims: list[ClaimMatch] = []
    for i, pattern in enumerate(CLAIM_PATTERNS):
        claim_type = ["dollar", "percent", "ratio", "large_dollar"][i]
        for m in re.finditer(pattern, text):
            claims.append(ClaimMatch(
                text=m.group(0).strip(),
                start=m.start(),
                end=m.end(),
                claim_type=claim_type,
            ))
    # Sort by position, remove nested/overlapping (keep longer)
    claims.sort(key=lambda c: (c.start, -len(c.text)))
    deduped = []
    last_end = -1
    for c in claims:
        if c.start >= last_end:
            deduped.append(c)
            last_end = c.end
    return deduped


def _find_citations(text: str) -> list[tuple[int, int, str]]:
    """Return list of (start, end, text) for all citation markers in text."""
    cits: list[tuple[int, int, str]] = []
    for pattern in CITATION_PATTERNS:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            cits.append((m.start(), m.end(), m.group(0)))
    cits.sort()
    return cits


def validate_citations(
    text: str,
    max_distance: int = 150,
    strict_threshold: float = 0.80,
) -> ValidationReport:
    """
    Validate that numerical claims in text are cited.

    Parameters
    ----------
    text : str
        The memo text to validate.
    max_distance : int
        Max characters between a claim and a citation to count as "cited".
    strict_threshold : float
        Minimum citation coverage to be considered "passing" (0.0-1.0).

    Returns
    -------
    ValidationReport
    """
    claims = _find_claims(text)
    citations = _find_citations(text)

    # For each claim, find the nearest following citation within max_distance
    for claim in claims:
        nearest_distance = None
        nearest_text = None
        for cit_start, cit_end, cit_text in citations:
            # Citation must come AFTER the claim (or contain it)
            if cit_start < claim.end:
                continue
            distance = cit_start - claim.end
            if distance > max_distance:
                break  # citations are sorted, no later match will be closer
            if nearest_distance is None or distance < nearest_distance:
                nearest_distance = distance
                nearest_text = cit_text

        if nearest_distance is not None:
            claim.cited = True
            claim.citation_text = nearest_text
            claim.citation_distance = nearest_distance

    total = len(claims)
    cited = sum(1 for c in claims if c.cited)
    uncited = total - cited
    coverage = cited / total if total > 0 else 1.0

    return ValidationReport(
        total_claims=total,
        cited_claims=cited,
        uncited_claims=uncited,
        citation_coverage=coverage,
        claims=claims,
        passes_strict_threshold=(coverage >= strict_threshold),
    )


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Positive example — well-cited
    good = """
    Realty Income reported FY2025 revenue of $5,749M [Source: SEC XBRL FY2025],
    up 9.1% [Source: SEC XBRL] from $5,271M [Source: XBRL FY2024] in FY2024.
    Operating cash flow reached $3,995M (10-K Item 7 - MD&A).
    The dividend payout ratio of 73% (Source: computed from XBRL) is well-covered.
    """

    # Negative example — uncited claims
    bad = """
    Revenue was $5.7B and grew 9.1%. Net income reached $1,059M with a 18.4% margin.
    The company holds $435M in cash and carries $32B in total liabilities.
    """

    for name, memo in [("GOOD", good), ("BAD", bad)]:
        report = validate_citations(memo)
        print(f"\n=== {name} MEMO ===")
        print(report.summary())
        print(f"Passes strict threshold (>= 80%): {report.passes_strict_threshold}")
