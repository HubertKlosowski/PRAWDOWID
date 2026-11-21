import os

from pgvector.django import CosineDistance

from .embeddings import embed
from .models import Chunk


def retrieve(text):
    if not Chunk.objects.exists():
        return []
    query_vector = embed([text], query=True)[0]
    limit = max(1, min(30, int(os.getenv("RAG_TOP_K", "5"))))
    rows = (Chunk.objects.select_related("document")
            .order_by(CosineDistance("embedding", query_vector), "pk")[:limit])
    return [
        {"id": str(row.pk), "text": row.text, "title": row.document.title,
         "url": row.document.url, "fetched_at": row.document.fetched_at.isoformat()}
        for row in rows
    ]
