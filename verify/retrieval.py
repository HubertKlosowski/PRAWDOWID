from pgvector.django import CosineDistance

from .corpus import SOURCE_FIELDS, default_domain
from .embeddings import embed
from .models import Chunk
from .temporal import DATE_FIELDS


def search(text, limit):
    """Return ranked corpus fragments and their source metadata."""
    indexed_chunks = Chunk.objects.filter(embedding__isnull=False)
    if not indexed_chunks.exists():
        return []
    query_vector = embed([text], query=True)[0]
    rows = (indexed_chunks.select_related("document")
            .order_by(CosineDistance("embedding", query_vector), "pk")[:limit])
    return [
        {"id": str(row.pk), "document_id": row.document_id, "position": row.position,
         "text": row.text, "title": row.document.title,
         "url": row.document.url, "fetched_at": row.document.fetched_at.isoformat(),
         "retrieved_at": row.document.fetched_at.isoformat(),
         "original_url": row.document.original_url or row.document.url,
         "source_domain": row.document.source_domain or default_domain(row.document.url),
         **{field: getattr(row.document, field) for field in SOURCE_FIELDS
            if field not in ("original_url", "source_domain")},
         **{field: value.isoformat() if (value := getattr(row.document, field)) else None
            for field in DATE_FIELDS}}
        for row in rows
    ]
