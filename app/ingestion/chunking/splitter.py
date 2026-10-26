import re
from typing import List

import logfire

from app.config import settings

# Coarse -> fine. The splitter tries the coarsest separator that keeps pieces small.
_SEPARATORS = ["\n\n", "\n", ". ", "? ", "! ", "; ", ", ", " "]


def clean_text(text: str) -> str:
    """Normalise extraction noise without destroying structure."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)  # de-hyphenate line-wrapped words
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _split(text: str, size: int, seps: list[str]) -> list[str]:
    """Split ``text`` into pieces no longer than ``size`` using the coarsest separator first."""
    if len(text) <= size:
        return [text]
    if not seps:  # nothing left to split on: hard cut
        return [text[i : i + size] for i in range(0, len(text), size)]

    sep, rest = seps[0], seps[1:]
    parts = text.split(sep)
    if len(parts) == 1:
        return _split(text, size, rest)

    pieces: list[str] = []
    for i, part in enumerate(parts):
        piece = part + (sep if i < len(parts) - 1 else "")
        if len(piece) > size:
            pieces.extend(_split(piece, size, rest))
        elif piece:
            pieces.append(piece)
    return pieces


def chunk_text(text: str, chunk_size: int | None = None, overlap: int | None = None) -> List[str]:
    """Split ``text`` into chunks of at most ``chunk_size`` chars with ``overlap`` carry-over."""
    chunk_size = chunk_size or settings.CHUNK_SIZE
    overlap = settings.CHUNK_OVERLAP if overlap is None else overlap
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")

    with logfire.span("Text Chunking", text_length=len(text)):
        text = clean_text(text)
        if not text:
            return []

        pieces = _split(text, chunk_size - overlap if overlap else chunk_size, _SEPARATORS)

        # Greedily pack pieces; the tail of each chunk is repeated at the head of the next.
        chunks: list[str] = []
        current = ""
        for piece in pieces:
            if current and len(current) + len(piece) > chunk_size:
                chunks.append(current.strip())
                tail = current[-overlap:] if overlap else ""
                # start the overlap at a word boundary so we don't begin mid-word
                if tail and " " in tail:
                    tail = tail.split(" ", 1)[1]
                current = tail + piece
            else:
                current += piece
        if current.strip():
            chunks.append(current.strip())

        # Overlap can push a chunk slightly past the limit; trim defensively.
        chunks = [c[:chunk_size] if len(c) > chunk_size else c for c in chunks if c.strip()]
        logfire.info(f"Generated {len(chunks)} chunks")
        return chunks
