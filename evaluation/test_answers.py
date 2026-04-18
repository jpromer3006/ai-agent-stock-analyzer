"""
test_answers.py — End-to-end answer quality evaluator.

For each golden question, runs the agent and scores the resulting answer on:
    - Completeness: does the answer contain all required regex patterns?
    - Citation coverage: via citation_validator.py
    - Tool usage: did the agent call all expected tools?
    - Length sanity: answer is neither trivially short nor rambling

Reports per-case and aggregate metrics.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from evaluation.citation_validator import validate_citations


_GOLDEN_PATH = Path(__file__).parent / "golden_questions.json"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class AnswerCase:
    question_id: str
    question: str
    ticker: str
    expected_tools: list[str]
    must_contain_regex: list[str]
    required_citation_count: int

    # Results
    answer_text: str = ""
    tools_called: list[str] = field(default_factory=list)
    tools_expected_called: int = 0
    tool_coverage: float = 0.0
    regex_matched: int = 0
    completeness: float = 0.0
    citation_coverage: float = 0.0
    total_claims: int = 0
    cited_claims: int = 0
    word_count: int = 0
    passed: bool = False
    error: Optional[str] = None


@dataclass
class AnswerReport:
    total_cases: int
    passed_cases: int
    mean_completeness: float
    mean_citation_coverage: float
    mean_tool_coverage: float
    cases: list[AnswerCase] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Per-case evaluation
# ---------------------------------------------------------------------------

def evaluate_case(case: AnswerCase, question_override: Optional[str] = None) -> AnswerCase:
    """
    Run the agent for a single question and score the answer.
    """
    from agents.orchestrator import run_agent

    prompt = question_override or case.question
    user_query = (
        f"Analyze {case.ticker} to answer this specific analyst question:\n\n"
        f"QUESTION: {prompt}\n\n"
        "Use your tools to pull the relevant data and produce a concise, "
        "data-driven answer with inline citations for every numerical claim."
    )

    try:
        for event in run_agent(case.ticker, user_query=user_query, max_steps=10):
            etype = event.get("type")
            if etype == "tool_call":
                case.tools_called.append(event["tool"])
            elif etype == "done":
                case.answer_text = event["memo"]
            elif etype == "error":
                case.error = event["message"]
                return case
    except Exception as exc:
        case.error = str(exc)
        return case

    # Tool coverage
    called_set = set(case.tools_called)
    if case.expected_tools:
        matched = [t for t in case.expected_tools if t in called_set]
        case.tools_expected_called = len(matched)
        case.tool_coverage = len(matched) / len(case.expected_tools)
    else:
        case.tool_coverage = 1.0

    # Completeness via regex
    if case.must_contain_regex:
        matches = [bool(re.search(p, case.answer_text, re.IGNORECASE))
                   for p in case.must_contain_regex]
        case.regex_matched = sum(matches)
        case.completeness = case.regex_matched / len(case.must_contain_regex)
    else:
        case.completeness = 1.0

    # Citation validation
    report = validate_citations(case.answer_text)
    case.citation_coverage = report.citation_coverage
    case.total_claims = report.total_claims
    case.cited_claims = report.cited_claims

    # Word count
    case.word_count = len(case.answer_text.split())

    # Pass threshold: completeness >= 0.67, citation coverage >= 0.70, tool coverage >= 0.5
    case.passed = (
        case.completeness >= 0.67
        and case.citation_coverage >= 0.70
        and case.tool_coverage >= 0.5
        and case.word_count >= 50
    )

    return case


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_answer_evaluation(
    sectors: Optional[list[str]] = None,
    tickers_per_sector: int = 1,
    questions_per_sector: Optional[int] = None,
) -> AnswerReport:
    """Run answer quality evaluation."""
    with open(_GOLDEN_PATH) as f:
        golden = json.load(f)

    target_sectors = sectors or list(golden["sectors"].keys())
    all_cases: list[AnswerCase] = []

    for sector in target_sectors:
        sector_data = golden["sectors"].get(sector)
        if not sector_data:
            continue
        test_tickers = sector_data["test_tickers"][:tickers_per_sector]
        questions = sector_data["questions"]
        if questions_per_sector:
            questions = questions[:questions_per_sector]

        for ticker in test_tickers:
            for q in questions:
                print(f"  [{sector}] {q['id']} {ticker}: running agent...")
                case = AnswerCase(
                    question_id=q["id"],
                    question=q["question"],
                    ticker=ticker,
                    expected_tools=q.get("expected_tools", []),
                    must_contain_regex=q.get("must_contain_regex", []),
                    required_citation_count=q.get("required_citation_count", 0),
                )
                case = evaluate_case(case)
                all_cases.append(case)
                _print_case(sector, case)

    # Aggregate
    non_error = [c for c in all_cases if not c.error]
    report = AnswerReport(
        total_cases=len(all_cases),
        passed_cases=sum(1 for c in all_cases if c.passed),
        mean_completeness=(
            sum(c.completeness for c in non_error) / len(non_error)
            if non_error else 0.0
        ),
        mean_citation_coverage=(
            sum(c.citation_coverage for c in non_error) / len(non_error)
            if non_error else 0.0
        ),
        mean_tool_coverage=(
            sum(c.tool_coverage for c in non_error) / len(non_error)
            if non_error else 0.0
        ),
        cases=all_cases,
    )
    return report


def _print_case(sector: str, case: AnswerCase):
    if case.error:
        print(f"    ✗ ERROR: {case.error[:100]}")
        return
    mark = "✓" if case.passed else "✗"
    print(
        f"    {mark} completeness={case.completeness:.0%} "
        f"citations={case.citation_coverage:.0%} "
        f"tools={case.tool_coverage:.0%} "
        f"words={case.word_count}"
    )


def print_report(report: AnswerReport):
    print("\n" + "=" * 70)
    print("ANSWER QUALITY EVALUATION SUMMARY")
    print("=" * 70)
    print(f"Total cases:           {report.total_cases}")
    print(f"Passed:                {report.passed_cases} / {report.total_cases} "
          f"({report.passed_cases / max(1, report.total_cases):.0%})")
    print(f"Mean completeness:     {report.mean_completeness:.1%}")
    print(f"Mean citation coverage:{report.mean_citation_coverage:.1%}")
    print(f"Mean tool coverage:    {report.mean_tool_coverage:.1%}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--sectors", nargs="*", default=None)
    ap.add_argument("--tickers-per-sector", type=int, default=1)
    ap.add_argument("--questions-per-sector", type=int, default=None,
                    help="Limit to first N questions per sector (for quick tests)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    print("Running answer quality evaluation on golden question set...")
    print()
    report = run_answer_evaluation(
        sectors=args.sectors,
        tickers_per_sector=args.tickers_per_sector,
        questions_per_sector=args.questions_per_sector,
    )
    print_report(report)

    if args.out:
        out = {
            "total_cases": report.total_cases,
            "passed_cases": report.passed_cases,
            "mean_completeness": report.mean_completeness,
            "mean_citation_coverage": report.mean_citation_coverage,
            "mean_tool_coverage": report.mean_tool_coverage,
            "cases": [
                {
                    "question_id": c.question_id,
                    "ticker": c.ticker,
                    "question": c.question,
                    "tools_called": c.tools_called,
                    "tool_coverage": c.tool_coverage,
                    "completeness": c.completeness,
                    "citation_coverage": c.citation_coverage,
                    "total_claims": c.total_claims,
                    "cited_claims": c.cited_claims,
                    "word_count": c.word_count,
                    "passed": c.passed,
                    "error": c.error,
                    "answer": c.answer_text[:500] + "..." if len(c.answer_text) > 500 else c.answer_text,
                }
                for c in report.cases
            ],
        }
        Path(args.out).write_text(json.dumps(out, indent=2))
        print(f"\nWrote detailed results to {args.out}")
