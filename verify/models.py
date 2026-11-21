from django.db import models
from pgvector.django import VectorField

EMBEDDING_SIZE = 1024


class Document(models.Model):
    url = models.URLField(max_length=2048, unique=True)
    title = models.TextField()
    fetched_at = models.DateTimeField()
    published_at = models.DateField(null=True, blank=True)
    valid_from = models.DateTimeField(null=True, blank=True)
    valid_to = models.DateTimeField(null=True, blank=True)
    content_hash = models.CharField(max_length=64)


class Chunk(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="chunks")
    position = models.PositiveIntegerField()
    text = models.TextField()
    embedding = VectorField(dimensions=EMBEDDING_SIZE, null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["document", "position"], name="unique_chunk_position")
        ]
