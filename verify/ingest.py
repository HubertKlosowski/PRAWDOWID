import hashlib
from collections import defaultdict
from urllib.parse import urlsplit

from django.db import transaction

from .embeddings import embed
from .models import Chunk, Document
from .temporal import DATE_FIELDS, local_datetime, source_date


def chunk_text(text, size=1200, overlap=160):
    if not 0 <= overlap < size:
        raise ValueError("Nieprawidłowy rozmiar fragmentu.")
    text = " ".join(text.split())
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            cut = text.rfind(" ", start + max(overlap + 1, size // 2), end)
            if cut > start:
                end = cut
        yield text[start:end].strip()
        if end == len(text):
            break
        start = end - overlap
        if start and text[start - 1] != " ":
            boundary = text.find(" ", start, end)
            if boundary >= 0:
                start = boundary + 1


def index_document(url, title, text, fetched_at, *, published_at=None, valid_from=None, valid_to=None):
    chunks = list(chunk_text(text))
    if not chunks:
        raise ValueError("Pusty dokument.")
    digest = hashlib.sha256(text.encode()).hexdigest()
    dates = dict(published_at=published_at, valid_from=valid_from, valid_to=valid_to)
    with transaction.atomic():
        document, created = Document.objects.select_for_update().get_or_create(
            url=url, defaults={"title": title, "fetched_at": fetched_at, "content_hash": digest})
        if not created and fetched_at < document.fetched_at:
            # An older snapshot can fill missing dates for identical content.
            if digest == document.content_hash:
                missing = {field: value for field, value in dates.items()
                           if value is not None and getattr(document, field) is None}
                if missing:
                    for field, value in missing.items():
                        setattr(document, field, value)
                    document.save(update_fields=missing.keys())
            return
        if created or digest != document.content_hash:
            vectors = embed(chunks)
            document.chunks.all().delete()
            Chunk.objects.bulk_create([
                Chunk(document=document, position=i, text=part, embedding=vector)
                for i, (part, vector) in enumerate(zip(chunks, vectors, strict=True))
            ])
        previous_digest = document.content_hash
        document.title = title
        document.content_hash = digest
        document.fetched_at = fetched_at
        for field, value in dates.items():
            if value is not None or digest != previous_digest:
                setattr(document, field, value)
        document.save(update_fields=["title", "content_hash", "fetched_at", *DATE_FIELDS])


def snapshot_documents(directory):
    import pyarrow.parquet as pq

    groups = defaultdict(list)
    for filename in ("dokumenty.parquet", "pomiary_wod.parquet"):
        for row in pq.read_table(directory / filename).to_pylist():
            url = row["canonical_url"]
            parsed = urlsplit(url)
            if parsed.scheme != "https" or parsed.netloc not in {"www.gov.pl", "odyseusz.gov.pl"}:
                raise ValueError("Nieznana domena w migawce.")
            title = row.get("title") or f"Stan wody — {row['location_name']}"
            text = row.get("text")
            if text is None:
                text = (f"Rzeka: {row['river_name']}. Stan wody: {row['water_level_value']}. "
                        f"Stan ostrzegawczy: {row['water_level_warning_status_value']}. "
                        f"Stan alarmowy: {row['water_level_alarm_status_value']}.")
            metadata = [
                ("Data dokumentu", row.get("document_date")),
                ("Utworzono", row.get("created_at")), ("Ważne od", row["valid_from"]),
                ("Ważne do", row["valid_to"]), ("Województwo", ", ".join(row["regions"] or [])),
            ]
            details = " ".join(f"{label}: {value}." for label, value in metadata if value)
            dates = {
                "published_at": source_date(row.get("document_date")),
                "valid_from": local_datetime(row.get("valid_from")),
                "valid_to": local_datetime(row.get("valid_to")),
            }
            groups[url].append((title, f"{title}. {details} {text}", row["fetched_at"], dates))
    for url, rows in groups.items():
        shared_dates = {}
        for field in DATE_FIELDS:
            values = {row[3][field] for row in rows}
            shared_dates[field] = values.pop() if len(values) == 1 else None
        yield url, rows[0][0], "\n".join(row[1] for row in rows), max(row[2] for row in rows), shared_dates
