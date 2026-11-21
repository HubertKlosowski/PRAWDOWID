from django.core.management.base import BaseCommand
from django.db import transaction

from verify.embeddings import embed
from verify.models import Chunk


class Command(BaseCommand):
    help = "Wylicza brakujące embeddingi istniejących fragmentów korpusu."

    def handle(self, *args, **options):
        pending = Chunk.objects.filter(embedding__isnull=True).order_by("pk")
        count = 0
        while True:
            with transaction.atomic():
                chunks = list(pending.select_for_update()[:4])
                if not chunks:
                    break
                vectors = embed([chunk.text for chunk in chunks])
                for chunk, vector in zip(chunks, vectors, strict=True):
                    chunk.embedding = vector
                Chunk.objects.bulk_update(chunks, ["embedding"])
                count += len(chunks)
        self.stdout.write(f"Uzupełniono embeddingi fragmentów: {count}.")
