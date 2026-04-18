"""
_shared.py — Prompt fragments shared by all specialist agents.

Central place for citation rules, format requirements, and attribution
standards enforced across every specialist. Per Lecture 9 (Applied Finance
Projects) rubric: every numerical claim must have an inline source citation.
"""

from __future__ import annotations

CITATION_RULES = """
═══════════════════════════════════════════════════════════════════════
MANDATORY CITATION RULES — STRICTLY ENFORCED
═══════════════════════════════════════════════════════════════════════

Every numerical claim in your memo (dollar amounts, percentages, ratios,
multiples) MUST be followed by an inline citation. A post-generation
validator will reject memos with < 80% citation coverage.

ACCEPTED CITATION FORMATS (use these exactly):
  • [Source: SEC XBRL FY2024]           ← structured financials
  • [Source: SEC 10-K Item 7 - MD&A]    ← qualitative from 10-K
  • [Source: SEC 10-K Item 1A - Risk Factors]
  • [Source: SEC 10-K Item 1 - Business]
  • [Source: yfinance — live price]     ← market data
  • [Source: computed from SEC XBRL]    ← derived ratios

EXAMPLES OF CORRECT CITATION:

  ✅ CORRECT:
     "Revenue of $5,749M [Source: SEC XBRL FY2025], up 9.1%
     [Source: computed from SEC XBRL] from FY2024."

  ✅ CORRECT:
     "The company disclosed 32.2% tenant concentration
     [Source: SEC 10-K Item 1A - Risk Factors]."

  ❌ WRONG (no citation):
     "Revenue was $5.7B and grew 9.1%."

  ❌ WRONG (citation too far from claim):
     "Revenue was $5.7B. Operating cash flow reached $4B. Margins expanded.
     [Source: SEC XBRL FY2025]"   ← only cites last claim

RULES:
1. EVERY dollar amount, percentage, or ratio MUST have a citation within
   the same sentence or immediately after.
2. When you compute a ratio, cite it as "[Source: computed from SEC XBRL]".
3. When quoting text from a 10-K section, cite the specific Item.
4. If you use live price or market cap data, cite "[Source: yfinance — live]".
5. If a number comes from multiple sources, list them: "[Source: SEC XBRL FY2024; SEC 10-K Item 7]"

DO NOT use bare numbers without citations. The validator will flag them
and your memo will be marked incomplete.
═══════════════════════════════════════════════════════════════════════
"""


ANTI_HALLUCINATION_RULES = """
FACTUAL GROUNDING RULES:
- Only state facts that appear in the tool outputs you received
- If a specific number is not available, say "not disclosed" — never invent
- If data is from an older fiscal year, clearly state the year
- If you are uncertain, prefix with "Based on available data, ..."
- Never extrapolate or forecast beyond what the 10-K states
- If search_10k returned no relevant passages, say so explicitly
"""


MEMO_FORMAT_RULES = """
FORMAT RULES:
- Use markdown with ## headings for each section
- Keep paragraphs tight — every sentence must carry signal
- Use bullet points for lists of risks or metrics
- Format financial figures: $X,XXXM for millions, $X.XB for billions
- Format percentages with one decimal place: 12.5%, not 12%
- Express YoY changes with sign: +9.1% or -3.2%
"""


def build_system_prompt(persona_intro: str, workflow: str, memo_sections: list[str]) -> str:
    """Assemble a complete specialist system prompt with shared rules."""
    sections_list = "\n".join(f"## {s}" for s in memo_sections)
    return f"""\
{persona_intro}

YOUR WORKFLOW:
{workflow}

REQUIRED MEMO STRUCTURE:
Produce a memo with exactly these sections, in this order:

{sections_list}

{CITATION_RULES}

{ANTI_HALLUCINATION_RULES}

{MEMO_FORMAT_RULES}
"""
