"""
chunker.py — Section-aware chunking for SEC filings.

Respects 10-K item boundaries so semantic retrieval returns coherent context,
not fragments split mid-paragraph. Based on patterns from Fordham Lecture 6
(Intro to RAG) and the rag_app reference implementation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Chunk:
    text: str
    metadata: dict


def _split_on_boundary(text: str, chunk_size: int = 800, overlap: int = 150) -> list[str]:
    """
    Split text into chunks, preferring paragraph → sentence → newline boundaries.
    """
    if len(text) <= chunk_size:
        return [text.strip()]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))

        if end < len(text):
            # Try paragraph boundary
            para_end = text.rfind("\n\n", start, end)
            if para_end > start + chunk_size // 2:
                end = para_end
            else:
                # Try sentence boundary
                sent_end = max(
                    text.rfind(". ", start, end),
                    text.rfind("! ", start, end),
                    text.rfind("? ", start, end),
                )
                if sent_end > start + chunk_size // 2:
                    end = sent_end + 1
                else:
                    # Try single newline
                    nl = text.rfind("\n", start, end)
                    if nl > start + chunk_size // 2:
                        end = nl

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= len(text):
            break
        # Apply overlap
        start = max(end - overlap, start + 1)

    return chunks


def chunk_10k_sections(ticker: str, sections: dict[str, str],
                       chunk_size: int = 800, overlap: int = 150) -> list[Chunk]:
    """
    Chunk each section of a 10-K filing separately, with metadata per chunk.

    Parameters
    ----------
    ticker : str
    sections : dict[str, str]
        Output of sec_client.extract_10k_sections()
    chunk_size : int
    overlap : int

    Returns
    -------
    list[Chunk] — each with text + metadata {ticker, section, chunk_index}
    """
    chunks: list[Chunk] = []
    for section_name, section_text in sections.items():
        for i, piece in enumerate(_split_on_boundary(section_text, chunk_size, overlap)):
            if not piece:
                continue
            chunks.append(Chunk(
                text=piece,
                metadata={
                    "ticker": ticker.upper(),
                    "section": section_name,
                    "chunk_index": i,
                    "source": "10-K",
                },
            ))
    return chunks
