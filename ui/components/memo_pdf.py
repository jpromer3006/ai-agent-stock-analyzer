"""
memo_pdf.py — render a markdown research memo to PDF bytes for download.

Pipeline: markdown -> HTML (python-markdown) -> PDF (xhtml2pdf / pisa).
Both libs are pure Python with no system dependencies, so this works on a
plain macOS / pip environment without cairo, pango, or wkhtmltopdf.

``memo_to_pdf_bytes`` returns ``None`` if the optional libs are missing or
rendering fails, so the caller can fall back to the markdown download instead
of crashing the Research tab.
"""

from __future__ import annotations

import io
import re
from datetime import datetime
from typing import Optional

# Emoji / pictographs render as missing-glyph boxes in the core PDF fonts, so
# strip them before conversion. Covers the common emoji blocks plus the
# variation selectors and zero-width joiner that follow them.
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF\U00002B00-\U00002BFF︀-️‍]",
    flags=re.UNICODE,
)

_PRINT_CSS = """
@page { size: letter; margin: 2.2cm 1.9cm; }
body { font-family: Helvetica, Arial, sans-serif; font-size: 10.5pt;
       line-height: 1.45; color: #1a1a1a; }
h1 { font-size: 19pt; margin: 0 0 4pt 0; }
h2 { font-size: 13.5pt; margin: 14pt 0 4pt 0; border-bottom: 1px solid #cccccc;
     padding-bottom: 2pt; }
h3 { font-size: 11.5pt; margin: 10pt 0 3pt 0; }
p  { margin: 4pt 0; }
ul, ol { margin: 4pt 0 4pt 14pt; }
li { margin: 2pt 0; }
hr { border: none; border-top: 1px solid #cccccc; margin: 10pt 0; }
strong { color: #000000; }
.masthead { color: #6b7280; font-size: 8.5pt; margin-bottom: 10pt; }
"""


def _strip_emoji(text: str) -> str:
    return _EMOJI_RE.sub("", text)


def memo_to_pdf_bytes(memo_md: str, ticker: str) -> Optional[bytes]:
    """Convert a markdown memo to PDF bytes. Returns None on any failure."""
    if not memo_md:
        return None
    try:
        import markdown
        from xhtml2pdf import pisa
    except Exception:
        return None

    try:
        body_html = markdown.markdown(
            _strip_emoji(memo_md),
            extensions=["extra", "sane_lists", "nl2br"],
        )
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        masthead = (
            f"<div class='masthead'>OROS Ai-Agent Stock Analyzer &middot; "
            f"{ticker.upper()} research memo &middot; generated {stamp}</div>"
        )
        html = (
            f"<html><head><meta charset='utf-8'><style>{_PRINT_CSS}</style></head>"
            f"<body>{masthead}{body_html}</body></html>"
        )
        buf = io.BytesIO()
        result = pisa.CreatePDF(src=html, dest=buf, encoding="utf-8")
        if result.err:
            return None
        data = buf.getvalue()
        return data if data[:5] == b"%PDF-" else None
    except Exception:
        return None
