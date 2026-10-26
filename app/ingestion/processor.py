import hashlib
import json
import os
import sys
import uuid

import logfire
from qdrant_client.http import models

from app.config import settings
from app.ingestion.chunking.splitter import chunk_text
from app.ingestion.loaders.html import parse_html
from app.ingestion.loaders.pdf import parse_pdf
from app.ingestion.loaders.text import parse_text
from app.services.retrieval.embeddings import embed_texts, get_embedding_dim
from app.services.retrieval.qdrant_service import get_client

# Generated artefacts (git-ignored): parsed chunks as JSON for debugging.
PROCESSED_DATA_DIR = "processed_data"
SUPPORTED = {"pdf", "html", "htm", "txt", "docx", "pptx"}
_NAMESPACE = uuid.UUID("6f1c2d6e-8b1f-4c9e-9f0a-3b6d6a1f5e11")


def point_id(source_type: str, filename: str, index: int, chunk: str) -> str:
    """Deterministic ID: re-ingesting the same file overwrites instead of duplicating."""
    digest = hashlib.sha1(chunk.encode("utf-8")).hexdigest()[:16]
    return str(uuid.uuid5(_NAMESPACE, f"{source_type}/{filename}/{index}/{digest}"))


def save_processed_locally(data: dict, source_type: str, filename: str) -> str:
    folder = os.path.join(PROCESSED_DATA_DIR, source_type)
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, f"{filename}.json")
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return dest


def extract_text(file_path: str, ext: str) -> str:
    if ext == "pdf":
        return parse_pdf(file_path)
    if ext in ("html", "htm"):
        return parse_html(file_path)
    if ext == "txt":
        return parse_text(file_path)
    from app.ingestion.loaders.office import parse_office  # heavy import, only when needed

    return parse_office(file_path)


def process_file(file_path: str, filename: str, source_type: str) -> int:
    """Ingest one file. Returns the number of chunks indexed (0 if skipped or failed)."""
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if ext not in SUPPORTED:
        logfire.warning(f"Skipping unsupported file type: {filename}")
        return 0

    with logfire.span("Processing File", file=filename, source=source_type):
        try:
            text = extract_text(file_path, ext)
            if not text or not text.strip():
                logfire.warning(f"No text extracted from {filename} — skipping.")
                return 0

            chunks = chunk_text(text)
            if not chunks:
                return 0

            save_processed_locally(
                {"filename": filename, "source_type": source_type, "chunks": chunks},
                source_type,
                filename,
            )

            vectors = embed_texts(chunks)
            points = [
                models.PointStruct(
                    id=point_id(source_type, filename, i, chunk),
                    vector=vector,
                    payload={
                        "text": chunk,
                        "source": filename,
                        "source_type": source_type,
                        "chunk_index": i,
                    },
                )
                for i, (chunk, vector) in enumerate(zip(chunks, vectors))
            ]
            get_client().upsert(collection_name=settings.QDRANT_COLLECTION_NAME, points=points)
            logfire.info(f"Indexed {len(points)} chunks from {filename}.")
            return len(points)
        except Exception as e:  # noqa: BLE001
            logfire.error(f"Failed to process {filename}: {e}")
            return -1


def process_directory(dir_path: str, source_type: str) -> dict:
    stats = {"files": 0, "chunks": 0, "failed": []}
    with logfire.span("Scanning Directory", path=dir_path, source=source_type):
        files = sorted(f for f in os.listdir(dir_path) if os.path.isfile(os.path.join(dir_path, f)))
        for filename in files:
            n = process_file(os.path.join(dir_path, filename), filename, source_type)
            if n < 0:
                stats["failed"].append(filename)
            elif n > 0:
                stats["files"] += 1
                stats["chunks"] += n
    return stats


def ensure_collection(wipe: bool) -> None:
    client = get_client()
    name = settings.QDRANT_COLLECTION_NAME
    if wipe and client.collection_exists(name):
        client.delete_collection(name)
        logfire.info(f"Collection '{name}' deleted.")

    dim = get_embedding_dim()
    if not client.collection_exists(name):
        client.create_collection(
            collection_name=name,
            vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
        )
        logfire.info(f"Created collection '{name}' ({dim}-dim, cosine, provider={settings.EMBEDDING_PROVIDER}).")
        return

    existing = client.get_collection(name).config.params.vectors.size
    if existing != dim:
        raise RuntimeError(
            f"Collection '{name}' has {existing}-dim vectors but the active embedding model "
            f"produces {dim}-dim. Re-run with --wipe or fix EMBEDDING_PROVIDER."
        )


def _source_type_for(name: str) -> str:
    n = name.lower()
    return "true" if "true" in n else "noisy" if "noisy" in n else name


def run_universal_ingestion(base_dir: str, explicit_source_type: str | None = None, wipe: bool = False) -> dict:
    """Ingest base_dir; each sub-folder becomes a source_type (or base_dir itself if flat)."""
    totals = {"files": 0, "chunks": 0, "failed": []}
    with logfire.span("Universal Ingestion", base_directory=base_dir):
        ensure_collection(wipe)

        subdirs = [d for d in sorted(os.listdir(base_dir)) if os.path.isdir(os.path.join(base_dir, d))]
        targets = (
            [(base_dir, explicit_source_type or _source_type_for(os.path.basename(os.path.normpath(base_dir))))]
            if not subdirs
            else [(os.path.join(base_dir, d), _source_type_for(d)) for d in subdirs]
        )
        for path, source_type in targets:
            s = process_directory(path, source_type)
            totals["files"] += s["files"]
            totals["chunks"] += s["chunks"]
            totals["failed"] += s["failed"]

        if totals["chunks"] or wipe:
            # new/changed knowledge => cached answers and retrievals are stale
            try:
                from app.cache.answer_cache import bump_corpus_version

                bump_corpus_version()
            except Exception as e:  # noqa: BLE001
                logfire.warning(f"Could not invalidate caches after ingestion: {e}")
    return totals


if __name__ == "__main__":
    logfire.configure(service_name="enterprise-ingestion-service", send_to_logfire="if-token-present")
    wipe_requested = "--wipe" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--wipe"]

    target_dir = args[0] if args else "DATA"
    explicit_type = args[1] if len(args) > 1 else None

    if not os.path.isdir(target_dir):
        print(f"Error: directory '{target_dir}' does not exist.")
        sys.exit(1)

    result = run_universal_ingestion(target_dir, explicit_source_type=explicit_type, wipe=wipe_requested)
    print(f"Done: {result['files']} files, {result['chunks']} chunks, {len(result['failed'])} failed.")
    for name in result["failed"]:
        print(f"  FAILED: {name}")
    sys.exit(1 if result["failed"] else 0)
