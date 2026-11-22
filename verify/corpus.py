"""Catalog metadata and a reproducible seed for the demonstration corpus."""

import csv
import subprocess
from collections import defaultdict
from datetime import date, datetime
from urllib.parse import urlsplit

from django.conf import settings
from django.db import connection, transaction
from pgvector import Vector

from .models import EMBEDDING_SIZE, Chunk, Document
from .temporal import DATE_FIELDS

CATALOG_PATH = settings.BASE_DIR / "corpus/demo-sources.csv"
ARCHIVE_PATH = settings.BASE_DIR / "prawdowid-demo-2026-11-21.dump"
SOURCE_FIELDS = ("original_url", "publisher", "source_domain", "source_kind", "source_type")
SOURCE_ROLES = {"official", "primary", "secondary"}
CATALOG_FIELDS = {"url", "original_url", "domain", "publisher", "kind", "source_type", *DATE_FIELDS}
COPY_HEADERS = {
    'COPY public.verify_document (id, url, title, fetched_at, content_hash) FROM stdin;': "documents",
    'COPY public.verify_chunk (id, "position", text, embedding, document_id) FROM stdin;': "chunks",
}


def source_host(url):
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username is not None:
        raise ValueError("Niepoprawny URL źródła w katalogu.")
    return parts.hostname.lower().rstrip(".")


def default_domain(url):
    return source_host(url).removeprefix("www.")


def read_catalog(path):
    catalog = {}
    with path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        if set(reader.fieldnames or []) != CATALOG_FIELDS:
            raise ValueError("Niepoprawne kolumny katalogu korpusu.")
        for row in reader:
            url = row["url"]
            host = source_host(url)
            source_host(row["original_url"])
            domain = row["domain"]
            if url in catalog or not domain or not (host == domain or host.endswith("." + domain)):
                raise ValueError(f"Powtórzony URL lub niezgodna domena w wierszu {reader.line_num}.")
            if row["source_type"] not in SOURCE_ROLES or not row["publisher"] or not row["kind"]:
                raise ValueError(f"Brak metadanych źródła w wierszu {reader.line_num}.")

            dates = {
                "published_at": date.fromisoformat(row["published_at"]) if row["published_at"] else None,
                "valid_from": datetime.fromisoformat(row["valid_from"]) if row["valid_from"] else None,
                "valid_to": datetime.fromisoformat(row["valid_to"]) if row["valid_to"] else None,
            }
            start, end = dates["valid_from"], dates["valid_to"]
            if any(value and value.utcoffset() is None for value in (start, end)) or (
                start and end and start > end
            ):
                raise ValueError(f"Niepoprawny okres ważności w wierszu {reader.line_num}.")
            catalog[url] = {
                "original_url": row["original_url"],
                "publisher": row["publisher"],
                "source_domain": domain,
                "source_kind": row["kind"],
                "source_type": row["source_type"],
                **dates,
            }
    return catalog


def _archive_copy_blocks(path):
    """Extract COPY payloads only. PostgreSQL decodes its own text escapes."""
    restored = subprocess.run(
        ["pg_restore", "--data-only", "--table=verify_document", "--table=verify_chunk", "--file=-", str(path)],
        check=True, capture_output=True, timeout=60,
    )
    blocks = {}
    active = None
    for line in restored.stdout.split(b"\n"):
        if active is not None:
            if line == b"\\.":
                active = None
            else:
                blocks[active].append(line)
        elif line.startswith(b"COPY "):
            active = COPY_HEADERS.get(line.decode("ascii"))
            if active is None or active in blocks:
                raise ValueError("Nieoczekiwany blok COPY w zrzucie korpusu.")
            blocks[active] = []
    if active is not None or set(blocks) != {"documents", "chunks"}:
        raise ValueError("Niekompletny zrzut korpusu.")
    return blocks


def _read_seed(cursor, blocks):
    cursor.execute("""
        CREATE TEMP TABLE demo_seed_documents (
            id bigint PRIMARY KEY, url text NOT NULL UNIQUE, title text NOT NULL,
            fetched_at timestamptz NOT NULL, content_hash text NOT NULL
        ) ON COMMIT DROP
    """)
    cursor.execute(f"""
        CREATE TEMP TABLE demo_seed_chunks (
            id bigint PRIMARY KEY, position integer NOT NULL, text text NOT NULL,
            embedding vector({EMBEDDING_SIZE}) NOT NULL,
            document_id bigint NOT NULL REFERENCES demo_seed_documents(id),
            UNIQUE (document_id, position)
        ) ON COMMIT DROP
    """)
    with cursor.copy("COPY demo_seed_documents FROM STDIN") as copy:
        copy.write(b"\n".join(blocks["documents"]) + b"\n")
    with cursor.copy("COPY demo_seed_chunks FROM STDIN") as copy:
        copy.write(b"\n".join(blocks["chunks"]) + b"\n")
    cursor.execute("SELECT id, url, title, fetched_at, content_hash FROM demo_seed_documents ORDER BY url")
    documents = cursor.fetchall()
    cursor.execute("SELECT document_id, position, text, embedding::text FROM demo_seed_chunks ORDER BY position")
    chunks = defaultdict(dict)
    for document_id, position, text, embedding in cursor.fetchall():
        chunks[document_id][position] = (text, embedding)
    cursor.execute("DROP TABLE pg_temp.demo_seed_chunks, pg_temp.demo_seed_documents")
    return documents, chunks


def _restore_chunks(document, seed_chunks):
    existing = {chunk.position: chunk for chunk in document.chunks.all()}
    if any(position not in seed_chunks or chunk.text != seed_chunks[position][0]
           for position, chunk in existing.items()):
        return 0
    created, updated = [], []
    for position, (text, embedding) in seed_chunks.items():
        chunk = existing.get(position)
        if chunk is None:
            created.append(Chunk(document=document, position=position, text=text,
                                 embedding=Vector.from_text(embedding)))
        elif chunk.embedding is None:
            chunk.embedding = Vector.from_text(embedding)
            updated.append(chunk)
    if created:
        Chunk.objects.bulk_create(created)
    if updated:
        Chunk.objects.bulk_update(updated, ["embedding"])
    return len(created) + len(updated)


def load_demo_corpus(archive_path=ARCHIVE_PATH, catalog_path=CATALOG_PATH):
    catalog = read_catalog(catalog_path)
    blocks = _archive_copy_blocks(archive_path)
    counts = {"created_documents": 0, "updated_documents": 0, "indexed_chunks": 0, "different_versions": 0}
    with transaction.atomic():
        with connection.cursor() as cursor:
            documents, chunks = _read_seed(cursor, blocks)
        if set(catalog) != {row[1] for row in documents} or set(chunks) != {row[0] for row in documents}:
            raise ValueError("Katalog, dokumenty i fragmenty ze zrzutu nie są zgodne.")

        for old_id, url, title, fetched_at, digest in documents:
            metadata = catalog[url]
            document, created = Document.objects.select_for_update().get_or_create(
                url=url, defaults={"title": title, "fetched_at": fetched_at, "content_hash": digest, **metadata},
            )
            if created:
                counts["created_documents"] += 1
            same_version = document.content_hash == digest
            if not created:
                fields = (*SOURCE_FIELDS, *DATE_FIELDS) if same_version else SOURCE_FIELDS
                missing = {key: metadata[key] for key in fields if not getattr(document, key) and metadata[key]}
                if missing:
                    for key, value in missing.items():
                        setattr(document, key, value)
                    document.save(update_fields=missing)
                    counts["updated_documents"] += 1
            if same_version:
                counts["indexed_chunks"] += _restore_chunks(document, chunks[old_id])
            else:
                counts["different_versions"] += 1
    return counts
