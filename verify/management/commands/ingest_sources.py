from pathlib import Path
from time import sleep
from urllib.parse import urlsplit

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from verify.fetch import fetch
from verify.ingest import index_document, snapshot_documents


class Command(BaseCommand):
    help = "Indeksuje skonfigurowane strony lub lokalną migawkę oficjalnego korpusu."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group()
        group.add_argument("--snapshot", type=Path)
        group.add_argument("--sources", type=Path, default=Path(settings.BASE_DIR) / "sources.txt")

    def handle(self, *args, **options):
        if directory := options["snapshot"]:
            rows = snapshot_documents(directory)
        else:
            rows = self.fetch_sources(options["sources"])
        count = 0
        for row in rows:
            index_document(*row)
            count += 1
        self.stdout.write(f"Przetworzono dokumentów: {count}.")

    def fetch_sources(self, path):
        failed = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            url = line.strip()
            if not url or url.startswith("#"):
                continue
            try:
                final_url, title, text = fetch(url, allowed_hosts={urlsplit(url).hostname})
            except ValueError as exc:
                failed += 1
                self.stderr.write(f"{url}: {exc}")
            else:
                yield final_url, title, text, timezone.now()
            sleep(1)
        if failed:
            raise CommandError(f"Liczba niedostępnych stron: {failed}.")
