"""
weinstein_book.py — ChromaDB + sentence-transformers semantic retrieval
over Stan Weinstein's "Secrets for Profiting in Bull and Bear Markets".

This grounds the agents in the ACTUAL book text rather than hardcoded
paraphrases. Any specialist can call the `weinstein_lookup` tool to pull
verbatim passages — Stage rules, the Ch.8 "No Isolationism" market-regime
doctrine, stop-loss placement, selling/shorting rules, etc.

Single collection: `weinstein_book`. Built once from the full-book PDF in
the `Weinstein /` folder, cached on disk at data/chromadb/ alongside the
10-K collections. Mirrors rag/vector_store.py conventions.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.utils import embedding_functions

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).parent.parent
_CHROMA_DIR = _PROJECT_ROOT / "data" / "chromadb"
_CHROMA_DIR.mkdir(parents=True, exist_ok=True)

_BOOK_DIR = _PROJECT_ROOT / "Weinstein "          # note: folder has a trailing space
_COLLECTION = "weinstein_book"
_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# Chunking — prose, not 10-K sections. ~1000 chars (~200 words) with overlap
# keeps each chunk self-contained while preserving cross-paragraph context.
_CHUNK_CHARS = 1000
_CHUNK_OVERLAP = 150

# ---------------------------------------------------------------------------
# Singleton client
# ---------------------------------------------------------------------------
_client = None
_embed_fn = None


def _get_client():
    global _client, _embed_fn
    if _client is None:
        _client = chromadb.PersistentClient(path=str(_CHROMA_DIR))
        _embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=_EMBED_MODEL
        )
    return _client, _embed_fn


def _get_or_create_collection():
    client, embed_fn = _get_client()
    return client.get_or_create_collection(
        name=_COLLECTION,
        embedding_function=embed_fn,
        metadata={"hnsw:space": "cosine"},
    )


def collection_exists() -> bool:
    client, _ = _get_client()
    try:
        col = client.get_collection(name=_COLLECTION)
        return col.count() > 0
    except Exception:
        return False


def delete_collection() -> None:
    client, _ = _get_client()
    try:
        client.delete_collection(name=_COLLECTION)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Source resolution
# ---------------------------------------------------------------------------
def _find_book_pdf() -> Optional[Path]:
    """Locate the full-book PDF in the Weinstein folder.

    Prefers the largest PDF whose filename mentions the book (so the
    partial Chapter-N fragment PDFs and the project paper are ignored).
    Falls back to the single largest PDF if no name match.
    """
    if not _BOOK_DIR.exists():
        return None
    pdfs = list(_BOOK_DIR.glob("*.pdf"))
    if not pdfs:
        return None
    named = [
        p for p in pdfs
        if re.search(r"secret|bull.*bear|weinstein", p.name, re.IGNORECASE)
    ]
    pool = named or pdfs
    return max(pool, key=lambda p: p.stat().st_size)


# ---------------------------------------------------------------------------
# Chunking with best-effort chapter tagging
# ---------------------------------------------------------------------------
def _detect_chapter(text: str, current: str) -> str:
    """Best-effort: if a page opens a new chapter, capture its number.

    The OCR renders headings like 'CHAPTER\\n2' or 'CHAPTER 2'. We only
    update when a clean chapter marker appears, so body text that merely
    mentions 'chapter' does not reset the tag.
    """
    m = re.search(r"\bCHAPTER\b\s*\n?\s*(\d{1,2})\b", text[:400], re.IGNORECASE)
    if m:
        return f"Chapter {int(m.group(1))}"
    return current


def _chunk_text(text: str) -> list[str]:
    """Sliding-window character chunks with overlap, split on whitespace
    boundaries so words are never cut mid-token."""
    text = re.sub(r"[ \t]+", " ", text).strip()
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + _CHUNK_CHARS, n)
        # back off to the nearest space to avoid splitting a word
        if end < n:
            sp = text.rfind(" ", start + _CHUNK_CHARS - _CHUNK_OVERLAP, end)
            if sp > start:
                end = sp
        chunk = text[start:end].strip()
        if len(chunk) > 60:                     # drop tiny fragments
            chunks.append(chunk)
        if end >= n:
            break
        start = max(end - _CHUNK_OVERLAP, start + 1)
    return chunks


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------
def ingest_book(force_refresh: bool = False) -> int:
    """Extract the full-book PDF, chunk it, embed into ChromaDB.
    Returns number of chunks indexed (0 if the PDF is missing).
    """
    if collection_exists() and not force_refresh:
        return _get_or_create_collection().count()
    if force_refresh:
        delete_collection()

    pdf_path = _find_book_pdf()
    if pdf_path is None:
        logger.warning("Weinstein book PDF not found in %s", _BOOK_DIR)
        return 0

    try:
        import pdfplumber
    except ImportError:
        logger.error("pdfplumber not installed — run: pip install pdfplumber")
        return 0

    ids: list[str] = []
    docs: list[str] = []
    metas: list[dict] = []
    current_chapter = "Front Matter"

    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            page_text = page.extract_text() or ""
            if not page_text.strip():
                continue
            current_chapter = _detect_chapter(page_text, current_chapter)
            for ci, chunk in enumerate(_chunk_text(page_text)):
                ids.append(f"wb_p{page_no}_c{ci}")
                docs.append(chunk)
                metas.append({"page": page_no, "chapter": current_chapter})

    if not docs:
        logger.warning("No extractable text in %s", pdf_path.name)
        return 0

    col = _get_or_create_collection()
    # Batch the add — Chroma handles large adds, but chunk to be safe
    B = 256
    for i in range(0, len(docs), B):
        col.add(ids=ids[i:i + B], documents=docs[i:i + B], metadatas=metas[i:i + B])

    logger.info("Indexed %d Weinstein book chunks from %s", len(docs), pdf_path.name)
    return len(docs)


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------
def search_book(query: str, top_k: int = 5,
                chapter_filter: Optional[str] = None) -> list[dict]:
    """Semantic search over the book. Auto-ingests on first call.

    Returns list of {text, page, chapter, relevance}.
    """
    if not collection_exists():
        if ingest_book() == 0:
            return []
    col = _get_or_create_collection()
    where = {"chapter": chapter_filter} if chapter_filter else None
    results = col.query(query_texts=[query], n_results=top_k, where=where)

    out: list[dict] = []
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    dists = results.get("distances", [[]])[0]
    for text, meta, dist in zip(docs, metas, dists):
        out.append({
            "text": text,
            "page": meta.get("page", 0),
            "chapter": meta.get("chapter", ""),
            "relevance": 1.0 - float(dist) if dist is not None else 0.0,
        })
    return out


def search_book_and_format(query: str, top_k: int = 4) -> str:
    """Search and return a formatted, citable string for the LLM."""
    hits = search_book(query, top_k=top_k)
    if not hits:
        return (
            f"No Weinstein book passage found for '{query}'. "
            f"(The book index may be empty — ensure the full-book PDF is in "
            f"the 'Weinstein ' folder.)"
        )
    lines = [f"=== Weinstein — \"Secrets for Profiting in Bull and Bear "
             f"Markets\" — query: '{query}' ==="]
    for i, hit in enumerate(hits, 1):
        lines.append(
            f"\n[{i}] {hit['chapter']}, p.{hit['page']} "
            f"(relevance {hit['relevance']:.2f}):"
        )
        lines.append(f"“{hit['text']}”")
    lines.append(
        "\n(Quote/paraphrase faithfully. This is the book's own text — treat "
        "it as the canonical Weinstein rule.)"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "no isolationism market stage"
    print(f"Book PDF: {_find_book_pdf()}")
    print(f"Ingesting (idempotent)...")
    n = ingest_book()
    print(f"Collection has {n} chunks.\n")
    print(search_book_and_format(q))
