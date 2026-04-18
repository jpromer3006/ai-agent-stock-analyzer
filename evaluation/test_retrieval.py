"""
test_retrieval.py — Evaluate RAG retrieval quality on the golden question set.

For each golden question, checks:
    - Does the top-K retrieval include at least one passage from each
      expected 10-K section?
    - What is the section-match rate (precision of section coverage)?
    - What is the mean relevance score of returned passages?

Reports: precision@k, recall on expected_sections, section-match rate.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from rag.vector_store import search, index_ticker_10k, collection_exists


_GOLDEN_PATH = Path(__file__).parent / "golden_questions.json"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class RetrievalCase:
    question_id: str
    question: str
    ticker: str
    expected_sections: list[str]
    top_k: int = 5
    # Results
    retrieved_sections: list[str] = field(default_factory=list)
    retrieved_scores: list[float] = field(default_factory=list)
    sections_matched: int = 0
    section_coverage: float = 0.0   # recall over expected_sections
    any_expected_hit: bool = False
    avg_relevance: float = 0.0
    error: Optional[str] = None


@dataclass
class RetrievalReport:
    total_cases: int
    cases_with_expected: int
    cases_matching_any: int
    mean_section_coverage: float
    mean_relevance: float
    cases: list[RetrievalCase] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def evaluate_case(case: RetrievalCase) -> RetrievalCase:
    """Run retrieval for a case and score it."""
    try:
        # Ensure the ticker's 10-K is indexed
        if not collection_exists(case.ticker):
            index_ticker_10k(case.ticker)

        hits = search(case.ticker, case.question, top_k=case.top_k)
    except Exception as exc:
        case.error = str(exc)
        return case

    case.retrieved_sections = [h["section"] for h in hits]
    case.retrieved_scores = [h["relevance"] for h in hits]
    case.avg_relevance = (
        sum(case.retrieved_scores) / len(case.retrieved_scores)
        if case.retrieved_scores else 0.0
    )

    if not case.expected_sections:
        # Question doesn't depend on specific 10-K sections
        case.section_coverage = 1.0
        case.any_expected_hit = True
        case.sections_matched = 0
        return case

    retrieved_set = set(case.retrieved_sections)
    matched = [s for s in case.expected_sections if s in retrieved_set]
    case.sections_matched = len(matched)
    case.section_coverage = len(matched) / len(case.expected_sections)
    case.any_expected_hit = case.sections_matched > 0
    return case


def run_retrieval_evaluation(
    sectors: Optional[list[str]] = None,
    tickers_per_sector: int = 1,
) -> RetrievalReport:
    """
    Run retrieval evaluation across the golden question set.

    Parameters
    ----------
    sectors : list of sector names to test (default: all)
    tickers_per_sector : how many test_tickers to evaluate per sector
    """
    with open(_GOLDEN_PATH) as f:
        golden = json.load(f)

    target_sectors = sectors or list(golden["sectors"].keys())
    all_cases: list[RetrievalCase] = []

    for sector in target_sectors:
        sector_data = golden["sectors"].get(sector)
        if not sector_data:
            continue
        test_tickers = sector_data["test_tickers"][:tickers_per_sector]

        for ticker in test_tickers:
            for q in sector_data["questions"]:
                case = RetrievalCase(
                    question_id=q["id"],
                    question=q["question"],
                    ticker=ticker,
                    expected_sections=q.get("expected_sections", []),
                )
                case = evaluate_case(case)
                all_cases.append(case)
                _print_case_result(sector, case)

    # Build report
    cases_with_expected = [c for c in all_cases if c.expected_sections]
    report = RetrievalReport(
        total_cases=len(all_cases),
        cases_with_expected=len(cases_with_expected),
        cases_matching_any=sum(1 for c in cases_with_expected if c.any_expected_hit),
        mean_section_coverage=(
            sum(c.section_coverage for c in cases_with_expected) / len(cases_with_expected)
            if cases_with_expected else 0.0
        ),
        mean_relevance=(
            sum(c.avg_relevance for c in all_cases) / len(all_cases)
            if all_cases else 0.0
        ),
        cases=all_cases,
    )
    return report


# ---------------------------------------------------------------------------
# Pretty printing
# ---------------------------------------------------------------------------

def _print_case_result(sector: str, case: RetrievalCase):
    if case.error:
        print(f"  [{sector}] {case.question_id} {case.ticker}: ERROR — {case.error}")
        return

    hit_mark = "✓" if case.any_expected_hit else "✗"
    coverage = f"{case.section_coverage:.0%}"
    relevance = f"{case.avg_relevance:.2f}"
    print(
        f"  [{sector}] {case.question_id} {case.ticker}: {hit_mark} "
        f"coverage={coverage} avg_rel={relevance}"
    )


def print_report(report: RetrievalReport):
    print("\n" + "=" * 70)
    print("RETRIEVAL EVALUATION SUMMARY")
    print("=" * 70)
    print(f"Total cases:                  {report.total_cases}")
    print(f"Cases with expected sections: {report.cases_with_expected}")
    print(
        f"Cases matching any expected:  {report.cases_matching_any} / "
        f"{report.cases_with_expected} "
        f"({report.cases_matching_any / max(1, report.cases_with_expected):.0%})"
    )
    print(f"Mean section coverage:        {report.mean_section_coverage:.1%}")
    print(f"Mean relevance score:         {report.mean_relevance:.3f}")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--sectors", nargs="*", default=None,
                    help="Which sectors to evaluate (default all)")
    ap.add_argument("--tickers-per-sector", type=int, default=1)
    ap.add_argument("--out", default=None, help="Optional JSON output path")
    args = ap.parse_args()

    print("Running retrieval evaluation on golden question set...")
    print()
    report = run_retrieval_evaluation(
        sectors=args.sectors,
        tickers_per_sector=args.tickers_per_sector,
    )
    print_report(report)

    if args.out:
        out = {
            "total_cases": report.total_cases,
            "cases_matching_any": report.cases_matching_any,
            "mean_section_coverage": report.mean_section_coverage,
            "mean_relevance": report.mean_relevance,
            "cases": [
                {
                    "question_id": c.question_id,
                    "ticker": c.ticker,
                    "expected_sections": c.expected_sections,
                    "retrieved_sections": c.retrieved_sections,
                    "section_coverage": c.section_coverage,
                    "avg_relevance": c.avg_relevance,
                    "any_expected_hit": c.any_expected_hit,
                    "error": c.error,
                }
                for c in report.cases
            ],
        }
        Path(args.out).write_text(json.dumps(out, indent=2))
        print(f"Wrote detailed results to {args.out}")
