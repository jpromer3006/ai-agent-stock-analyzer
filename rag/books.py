"""
books.py - Multi-book knowledge base for OROS (Phase 3).

Generalizes the single-book `weinstein_book.py` into a pipeline that ingests the
whole finance library, tags every chunk with its book and the strategy concepts
it covers, and serves concept-scoped retrieval. One shared ChromaDB collection
(`finance_books`), separate from the per-ticker `{ticker}_filings` collections,
so framework knowledge and company filings never bleed together.

Design (matches the plan, section 5):
  * Accepts PDF, EPUB, and TXT. For a scanned book it prefers an OCR `.txt`
    sidecar next to the PDF (Minervini's case).
  * Bounded retrieval (top_k small) keeps per-query tokens flat as books grow.
  * Local SentenceTransformers embeddings -> zero Anthropic tokens to index.

The text pipeline (extract -> chunk -> tag) imports no ML deps, so it is unit
testable anywhere. ChromaDB/embeddings are imported lazily inside `ingest_books`
and `search_books`, so the one-time index build runs on the app machine:

    PYTHONPATH=. python3 rag/books.py --ingest      # build the index (on your Mac)
    PYTHONPATH=. python3 rag/books.py               # text-pipeline self-test (no ML)
"""

from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BOOKS_DIR = _PROJECT_ROOT / "Finance Books"
_COLLECTION = "finance_books"
_EMBED_MODEL = "all-MiniLM-L6-v2"
_CHROMA_DIR = _PROJECT_ROOT / "data" / "chromadb"

CHUNK_SIZE = 800
CHUNK_OVERLAP = 150


# ---------------------------------------------------------------------------
# Book registry: filename match -> identity + the frameworks it teaches.
# ---------------------------------------------------------------------------
@dataclass
class Book:
    key: str
    title: str
    author: str
    frameworks: list[str]            # strategy tags the router uses
    match: str                       # case-insensitive filename substring


BOOK_REGISTRY: list[Book] = [
    Book("weinstein", "Secrets for Profiting in Bull and Bear Markets", "Stan Weinstein",
         ["weinstein", "stage", "technical"], "weinstein"),
    Book("technical", "The Only Technical Analysis Book You Will Ever Need", "various",
         ["technical"], "only-technical-analysis"),
    Book("graham", "The Intelligent Investor", "Benjamin Graham",
         ["graham", "value"], "intelligent investor"),
    Book("oneil", "How to Make Money in Stocks", "William O'Neil",
         ["oneil", "canslim", "growth", "momentum"], "how to make money in stocks"),
    Book("minervini", "Trade Like a Stock Market Wizard", "Mark Minervini",
         ["minervini", "sepa", "momentum"], "trade-like-a-stock-market-wizard"),
    Book("lynch", "One Up On Wall Street", "Peter Lynch",
         ["lynch", "garp", "growth"], "one-up-on-wall-street"),
    Book("swingtrade", "How to Swing Trade", "various",
         ["swing", "momentum"], "how-to-swing-trade"),
]


# ---------------------------------------------------------------------------
# Concept tagging: keyword -> concept. Drives concept-scoped retrieval.
# ---------------------------------------------------------------------------
_CONCEPT_KEYWORDS: dict[str, list[str]] = {
    "stage": ["stage 1", "stage 2", "stage 3", "stage 4", "basing", "advancing",
              "topping", "declining", "30-week", "30 week"],
    "breakout": ["breakout", "break out", "breaks above", "pivot", "buy point",
                 "cup with handle", "cup and handle", "vcp", "contraction"],
    "relative_strength": ["relative strength", "mansfield", "rs line", "outperform",
                          "leadership", "leader"],
    "volume": ["volume", "accumulation", "distribution", "on volume"],
    "valuation": ["valuation", "intrinsic value", "margin of safety", "p/e",
                  "earnings yield", "book value", "undervalued"],
    "growth": ["earnings growth", "revenue growth", "fast grower", "ten bagger",
               "canslim", "annual earnings"],
    "risk_management": ["stop loss", "stop-loss", "cut losses", "risk", "protect",
                        "sell rule", "loss"],
    "position_sizing": ["position size", "how much", "allocate", "exposure",
                        "concentrate", "pyramid"],
    "market_regime": ["market direction", "general market", "bull market",
                      "bear market", "market trend", "follow-through"],
    "ipo": ["ipo", "new issue", "newly public", "recent offering", "young company"],
}


def tag_concepts(text: str) -> list[str]:
    low = text.lower()
    tags = [c for c, kws in _CONCEPT_KEYWORDS.items() if any(k in low for k in kws)]
    return tags or ["general"]


def available_books() -> list["Book"]:
    """Only books whose source file is actually present in Finance Books, so the RAG
    library never references a book that is not on disk. Drop a book's PDF back in and
    it re-appears automatically; no code change needed."""
    return [b for b in BOOK_REGISTRY if _resolve_source(b) is not None]


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------
def _extract_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        from PyPDF2 import PdfReader  # type: ignore
    reader = PdfReader(str(path))
    parts = []
    for pg in reader.pages:
        try:
            parts.append(pg.extract_text() or "")
        except Exception:
            parts.append("")
    return "\n".join(parts)


def _extract_epub(path: Path) -> str:
    try:
        from ebooklib import epub, ITEM_DOCUMENT
        from bs4 import BeautifulSoup
    except ImportError:
        logger.warning("EPUB support needs ebooklib + beautifulsoup4; skipping %s", path.name)
        return ""
    book = epub.read_epub(str(path))
    parts = []
    for item in book.get_items_of_type(ITEM_DOCUMENT):
        soup = BeautifulSoup(item.get_content(), "html.parser")
        parts.append(soup.get_text(" "))
    return "\n".join(parts)


def _resolve_source(book: Book) -> Optional[Path]:
    """Find the best file for a book. Prefers an OCR .txt sidecar for scans."""
    if not _BOOKS_DIR.exists():
        return None
    cands = [p for p in _BOOKS_DIR.iterdir()
             if book.match.lower() in p.name.lower()
             and p.suffix.lower() in (".pdf", ".epub", ".txt")]
    if not cands:
        return None
    # Prefer a sizeable .txt sidecar (OCR output) over a scanned PDF.
    txt = [p for p in cands if p.suffix.lower() == ".txt" and p.stat().st_size > 5000]
    if txt:
        return txt[0]
    for ext in (".epub", ".pdf"):
        m = [p for p in cands if p.suffix.lower() == ext and not p.stem.endswith("_OCR")]
        if m:
            return m[0]
    return cands[0]


def _extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return _extract_pdf(path)
    if ext == ".epub":
        return _extract_epub(path)
    if ext == ".txt":
        return path.read_text(encoding="utf-8", errors="replace")
    return ""


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------
def _chunk(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Whitespace-aware sliding window; reuses the project chunker if available."""
    try:
        from rag.chunker import _split_on_boundary
        return _split_on_boundary(text, chunk_size=size, overlap=overlap)
    except Exception:
        text = re.sub(r"\s+", " ", text).strip()
        out, start = [], 0
        while start < len(text):
            end = min(start + size, len(text))
            out.append(text[start:end].strip())
            start = max(end - overlap, start + 1)
        return [c for c in out if len(c) > 60]


@dataclass
class BookChunk:
    book: str
    title: str
    author: str
    frameworks: list[str]
    concepts: list[str]
    text: str
    idx: int


def iter_book_chunks(book: Book) -> Iterable[BookChunk]:
    """Extract, chunk, and concept-tag one book. No ML deps used."""
    src = _resolve_source(book)
    if src is None:
        logger.warning("No source file found for %s (match=%r)", book.key, book.match)
        return
    raw = _extract_text(src)
    if not raw or len(raw) < 500:
        logger.warning("Sparse/empty text for %s from %s (%d chars). "
                       "Scanned PDF without OCR?", book.key, src.name, len(raw))
        return
    for i, ch in enumerate(_chunk(raw)):
        yield BookChunk(
            book=book.key, title=book.title, author=book.author,
            frameworks=book.frameworks, concepts=tag_concepts(ch), text=ch, idx=i,
        )


# ---------------------------------------------------------------------------
# Indexing + retrieval (lazy ML imports)
# ---------------------------------------------------------------------------
def _get_collection():
    import chromadb
    from chromadb.utils import embedding_functions
    client = chromadb.PersistentClient(path=str(_CHROMA_DIR))
    embed = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=_EMBED_MODEL)
    return client.get_or_create_collection(
        name=_COLLECTION, embedding_function=embed,
        metadata={"hnsw:space": "cosine"},
    )


def ingest_books(force_refresh: bool = False, only: Optional[list[str]] = None) -> int:
    """Build the finance_books collection. Returns total chunks indexed."""
    col = _get_collection()
    if force_refresh:
        import chromadb
        client = chromadb.PersistentClient(path=str(_CHROMA_DIR))
        try:
            client.delete_collection(_COLLECTION)
        except Exception:
            pass
        col = _get_collection()

    total = 0
    for book in available_books():
        if only and book.key not in only:
            continue
        docs, ids, metas = [], [], []
        for ch in iter_book_chunks(book):
            docs.append(ch.text)
            ids.append(f"{ch.book}_{ch.idx}")
            metas.append({
                "book": ch.book, "title": ch.title, "author": ch.author,
                "frameworks": ",".join(ch.frameworks),
                "concepts": ",".join(ch.concepts),
            })
        if not docs:
            continue
        for i in range(0, len(docs), 256):       # batch the upserts
            col.upsert(documents=docs[i:i+256], ids=ids[i:i+256], metadatas=metas[i:i+256])
        total += len(docs)
        logger.info("indexed %d chunks from %s", len(docs), book.key)
    logger.info("finance_books total chunks: %d", total)
    return total


def search_books(query: str, books: Optional[list[str]] = None,
                 concepts: Optional[list[str]] = None, top_k: int = 5) -> list[dict]:
    """Concept-scoped retrieval over the knowledge base."""
    col = _get_collection()
    where = None
    clauses = []
    if books:
        clauses.append({"book": {"$in": books}})
    if clauses:
        where = clauses[0] if len(clauses) == 1 else {"$and": clauses}
    res = col.query(query_texts=[query], n_results=top_k, where=where)
    out = []
    for doc, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0]):
        if concepts and not (set(concepts) & set(meta.get("concepts", "").split(","))):
            continue
        out.append({"book": meta.get("book"), "title": meta.get("title"),
                    "concepts": meta.get("concepts"), "score": 1 - dist, "text": doc})
    return out


# ---------------------------------------------------------------------------
# Self-test (text pipeline only, no ML)
# ---------------------------------------------------------------------------
def _selftest() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(f"Books dir: {_BOOKS_DIR}\n")
    present = {b.key for b in available_books()}
    print(f"Present books: {sorted(present)}\n")
    grand = 0
    concept_tally: dict[str, int] = {}
    for book in available_books():
        chunks = list(iter_book_chunks(book))
        grand += len(chunks)
        sample_tags = set()
        for c in chunks[:200]:
            for t in c.concepts:
                concept_tally[t] = concept_tally.get(t, 0) + 1
                sample_tags.add(t)
        src = _resolve_source(book)
        srcname = src.name if src else "MISSING"
        print(f"  {book.key:11} {len(chunks):5} chunks  <- {srcname[:46]}")
        if chunks:
            print(f"              tags seen: {sorted(sample_tags)}")
    print(f"\nTotal chunks across library: {grand}")
    print(f"Concept distribution (first 200 chunks/book): "
          f"{dict(sorted(concept_tally.items(), key=lambda x: -x[1]))}")


if __name__ == "__main__":
    if "--ingest" in sys.argv:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
        n = ingest_books(force_refresh="--force" in sys.argv)
        print(f"Indexed {n} chunks into '{_COLLECTION}'.")
    else:
        _selftest()
