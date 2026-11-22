import json
import subprocess

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from verify.corpus import load_demo_corpus


class Command(BaseCommand):
    help = "Importuje pełny katalog i indeks demonstracyjny z plików projektu."

    def handle(self, *args, **options):
        try:
            summary = load_demo_corpus()
        except (DatabaseError, OSError, ValueError, subprocess.SubprocessError) as exc:
            raise CommandError(f"Import korpusu nie powiódł się. {exc}") from exc
        self.stdout.write(json.dumps(summary, ensure_ascii=False))
