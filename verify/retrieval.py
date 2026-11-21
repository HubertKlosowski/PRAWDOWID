import os

from pgvector.django import CosineDistance

from .analysis import select_relevant
from .embeddings import embed
from .models import Chunk


def retrieve(text):
    indexed_chunks = Chunk.objects.filter(embedding__isnull=False)
    if not indexed_chunks.exists():
        return []
    query_vector = embed([text], query=True)[0]
    limit = max(1, min(30, int(os.getenv("RAG_TOP_K", "5"))))
    candidate_limit = min(40, limit * 3)
    rows = (indexed_chunks.select_related("document")
            .order_by(CosineDistance("embedding", query_vector), "pk")[:candidate_limit])
    candidates = [
        {"id": str(row.pk), "text": row.text, "title": row.document.title,
         "url": row.document.url, "fetched_at": row.document.fetched_at.isoformat()}
        for row in rows
    ]
    selected = select_relevant(text, candidates, limit)
    # An empty selection can miss useful evidence. Let the final assessment
    # check the strongest pgvector matches against its citation requirements.
    return selected or candidates[:limit]
