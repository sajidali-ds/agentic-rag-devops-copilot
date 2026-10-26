from app.ingestion import processor
from app.ingestion.loaders.html import parse_html
from app.ingestion.loaders.text import parse_text


def test_point_ids_are_deterministic_and_unique_per_chunk():
    a = processor.point_id("true", "doc.pdf", 0, "hello")
    assert a == processor.point_id("true", "doc.pdf", 0, "hello")  # re-ingest overwrites
    assert a != processor.point_id("true", "doc.pdf", 1, "hello")
    assert a != processor.point_id("true", "doc.pdf", 0, "changed")


def test_html_loader_strips_scripts(tmp_path):
    f = tmp_path / "p.html"
    f.write_text("<html><script>evil()</script><body><h1>Title</h1><p>Body text</p></body></html>")
    text = parse_html(str(f))
    assert "Body text" in text and "evil" not in text



def test_text_loader(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("héllo", encoding="utf-8")
    assert parse_text(str(f)) == "héllo"



def test_unsupported_extension_is_skipped(tmp_path):
    f = tmp_path / "x.exe"
    f.write_bytes(b"MZ")
    assert processor.process_file(str(f), "x.exe", "general") == 0


def test_process_file_indexes_chunks(tmp_path, monkeypatch):
    f = tmp_path / "a.txt"
    f.write_text("Alpha beta gamma. " * 200)
    monkeypatch.chdir(tmp_path)  # processed_data/ is written relative to cwd

    upserts = []

    class FakeClient:
        def upsert(self, collection_name, points):
            upserts.append(points)

    monkeypatch.setattr(processor, "get_client", lambda: FakeClient())
    monkeypatch.setattr(processor, "embed_texts", lambda chunks: [[0.1, 0.2]] * len(chunks))

    n = processor.process_file(str(f), "a.txt", "general")
    assert n > 1 and len(upserts[0]) == n
    payload = upserts[0][0].payload
    assert payload["source"] == "a.txt" and payload["chunk_index"] == 0


def test_ingestion_invalidates_caches_via_corpus_version(tmp_path, monkeypatch):
    from app.cache.answer_cache import corpus_version
    from app.cache.store import get_store

    (tmp_path / "docs").mkdir()
    monkeypatch.setattr(processor, "ensure_collection", lambda wipe: None)
    monkeypatch.setattr(processor, "process_directory", lambda path, st: {"files": 1, "chunks": 5, "failed": []})
    before = corpus_version(get_store())
    processor.run_universal_ingestion(str(tmp_path))
    assert corpus_version(get_store()) != before


def test_ingestion_without_new_chunks_keeps_caches(tmp_path, monkeypatch):
    from app.cache.answer_cache import corpus_version
    from app.cache.store import get_store

    (tmp_path / "docs").mkdir()
    monkeypatch.setattr(processor, "ensure_collection", lambda wipe: None)
    monkeypatch.setattr(processor, "process_directory", lambda path, st: {"files": 0, "chunks": 0, "failed": []})
    before = corpus_version(get_store())
    processor.run_universal_ingestion(str(tmp_path))
    assert corpus_version(get_store()) == before
