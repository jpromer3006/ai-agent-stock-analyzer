"""
vector_store.py — ChromaDB + sentence-transformers semantic retrieval
over SEC 10-K filings.

One collection per ticker: `{ticker}_filings`. Populated on first query,
cached on disk at data/chromadb/.

Adapted from Fordham `rag_app/rag_engine.py` reference pattern.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.utils import embedding_functions

from rag.chunker import Chunk, chunk_10k_sections

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).parent.parent
_CHROMA_DIR = _PROJECT_ROOT / "data" / "chromadb"
_CHROMA_DIR.mkdir(parents=True, exist_ok=True)

_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

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


# ---------------------------------------------------------------------------
# Collection management
# ---------------------------------------------------------------------------
def _collection_name(ticker: str) -> str:
    return f"{ticker.upper()}_filings"


def _get_or_create_collection(ticker: str):
    client, embed_fn = _get_client()
    return client.get_or_create_collection(
        name=_collection_name(ticker),
        embedding_function=embed_fn,
        metadata={"hnsw:space": "cosine"},
    )


def collection_exists(ticker: str) -> bool:
    """Check if ticker has an indexed collection."""
    client, _ = _get_client()
    try:
        col = client.get_collection(name=_collection_name(ticker))
        return col.count() > 0
    except Exception:
        return False


def delete_collection(ticker: str):
    client, _ = _get_client()
    try:
        client.delete_collection(name=_collection_name(ticker))
    except Exception:
        pass


def get_stats() -> dict[str, int]:
    """Return {collection_name: count} for all collections."""
    client, _ = _get_client()
    stats: dict[str, int] = {}
    for col_meta in client.list_collections():
        name = col_meta.name if hasattr(col_meta, "name") else col_meta
        try:
            col = client.get_collection(name=name)
            stats[name] = col.count()
        except Exception:
            stats[name] = 0
    return stats


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------
def index_ticker_10k(ticker: str, force_refresh: bool = False) -> int:
    """
    Fetch the latest 10-K for a ticker, chunk it, and index in ChromaDB.
    Returns the number of chunks indexed.
    """
    ticker = ticker.upper()

    if collection_exists(ticker) and not force_refresh:
        col = _get_or_create_collection(ticker)
        return col.count()

    if force_refresh:
        delete_collection(ticker)

    # Lazy import to avoid cycles
    from data.sec_client import extract_10k_sections

    sections = extract_10k_sections(ticker)
    if not sections:
        logger.warning(f"No 10-K sections extracted for {ticker}")
        return 0

    chunks = chunk_10k_sections(ticker, sections)
    if not chunks:
        return 0

    col = _get_or_create_collection(ticker)
    col.add(
        ids=[f"{ticker}_{c.metadata['section']}_{c.metadata['chunk_index']}" for c in chunks],
        documents=[c.text for c in chunks],
        metadatas=[c.metadata for c in chunks],
    )
    logger.info(f"Indexed {len(chunks)} chunks for {ticker}")
    return len(chunks)


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------
def search(ticker: str, query: str, top_k: int = 5,
           section_filter: Optional[str] = None) -> list[dict]:
    """
    Semantic search over a ticker's 10-K chunks.

    Returns list of {text, section, chunk_index, distance}.
    """
    ticker = ticker.upper()

    # Auto-index if missing
    if not collection_exists(ticker):
        count = index_ticker_10k(ticker)
        if count == 0:
            return []

    col = _get_or_create_collection(ticker)

    where = {"section": section_filter} if section_filter else None

    results = col.query(
        query_texts=[query],
        n_results=top_k,
        where=where,
    )

    out = []
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    dists = results.get("distances", [[]])[0]
    for text, meta, dist in zip(docs, metas, dists):
        out.append({
            "text": text,
            "section": meta.get("section", ""),
            "chunk_index": meta.get("chunk_index", 0),
            "distance": float(dist) if dist is not None else 1.0,
            "relevance": 1.0 - float(dist) if dist is not None else 0.0,
        })
    return out


def search_and_format(ticker: str, query: str, top_k: int = 5) -> str:
    """Search and return a formatted string for the LLM."""
    hits = search(ticker, query, top_k=top_k)
    if not hits:
        return f"No 10-K content found for {ticker} matching '{query}'."

    lines = [f"=== 10-K Semantic Search — {ticker} — query: '{query}' ==="]
    for i, hit in enumerate(hits, 1):
        lines.append(
            f"\n[{i}] Section: {hit['section']}  "
            f"(relevance: {hit['relevance']:.2f})"
        )
        lines.append(hit["text"][:1200])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "O"
    query = sys.argv[2] if len(sys.argv) > 2 else "interest rate risk"

    print(f"Indexing 10-K for {ticker}...")
    count = index_ticker_10k(ticker)
    print(f"Indexed {count} chunks.\n")

    print(f"Searching: '{query}'")
    print()
    print(search_and_format(ticker, query))
