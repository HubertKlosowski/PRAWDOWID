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


class VerificationSession(models.Model):
    key_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField(db_index=True)


class VerificationRun(models.Model):
    request_id = models.UUIDField(primary_key=True, editable=False)
    session = models.ForeignKey(VerificationSession, on_delete=models.CASCADE, related_name="runs")
    channel = models.CharField(max_length=8)
    mode = models.CharField(max_length=4)
    question = models.TextField()
    created_at = models.DateTimeField(db_index=True)
    status = models.CharField(max_length=24)
    outcomes = models.JSONField(default=list)
    elapsed_ms = models.PositiveIntegerField()
    stages = models.JSONField(default=dict)
    models_used = models.JSONField(default=dict)
    estimated_cost = models.DecimalField(max_digits=12, decimal_places=6, null=True)
    cost_method = models.CharField(max_length=24, default="unknown")
