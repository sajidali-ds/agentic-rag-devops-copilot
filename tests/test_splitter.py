from app.ingestion.chunking.splitter import chunk_text, clean_text


def test_empty_text_gives_no_chunks():
    assert chunk_text("   \n\n ") == []


def test_chunks_respect_size_even_without_paragraph_breaks():
    # PDF-style text: one giant block, only single newlines. The old splitter returned
    # a single chunk of the whole document.
    text = "\n".join(f"Sentence number {i} talks about SR-IOV and virtual functions." for i in range(400))
    chunks = chunk_text(text, chunk_size=500, overlap=50)
    assert len(chunks) > 10
    assert all(len(c) <= 500 for c in chunks)


def test_single_oversized_word_run_is_hard_split():
    chunks = chunk_text("x" * 5000, chunk_size=1000, overlap=100)
    assert len(chunks) >= 5
    assert all(len(c) <= 1000 for c in chunks)


def test_overlap_carries_context_between_chunks():
    words = [f"word{i}" for i in range(600)]
    chunks = chunk_text(" ".join(words), chunk_size=400, overlap=80)
    assert len(chunks) > 2
    for a, b in zip(chunks, chunks[1:]):
        assert set(a.split()[-5:]) & set(b.split()[:15]), "no overlap between consecutive chunks"


def test_no_content_lost():
    words = [f"w{i}" for i in range(500)]
    joined = " ".join(chunk_text(" ".join(words), chunk_size=300, overlap=50))
    assert all(w in joined.split() for w in words)


def test_overlap_must_be_smaller_than_size():
    import pytest

    with pytest.raises(ValueError):
        chunk_text("hello", chunk_size=100, overlap=100)


def test_clean_text_dehyphenates_and_collapses_whitespace():
    assert clean_text("net-\nwork   stack\n\n\n\nnext") == "network stack\n\nnext"
