import csv
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Q

from verify.corpus import default_domain
from verify.models import Document

COLUMNS = (
    "document_id", "url", "original_url", "domain", "title", "publisher", "kind", "source_type",
    "published_at", "retrieved_at", "valid_from", "valid_to", "status", "chunks", "indexed_chunks",
    "included_in_metrics",
)


def audit_rows():
    documents = Document.objects.annotate(
        chunks_count=Count("chunks"),
        indexed_count=Count("chunks", filter=Q(chunks__embedding__isnull=False)),
    ).order_by("pk")
    seen_hashes = set()
    for document in documents:
        indexed = document.indexed_count
        if not indexed:
            status = "unindexed"
        elif indexed == document.chunks_count:
            status = "indexed"
        else:
            status = "partial"
        included = status == "indexed" and document.content_hash not in seen_hashes
        if included:
            seen_hashes.add(document.content_hash)
        yield {
            "document_id": document.pk,
            "url": document.url,
            "original_url": document.original_url or document.url,
            "domain": document.source_domain or default_domain(document.url),
            "title": document.title,
            "publisher": document.publisher,
            "kind": document.source_kind,
            "source_type": document.source_type,
            "published_at": document.published_at,
            "retrieved_at": document.fetched_at,
            "valid_from": document.valid_from,
            "valid_to": document.valid_to,
            "status": status,
            "chunks": document.chunks_count,
            "indexed_chunks": indexed,
            "included_in_metrics": included,
        }


def safe_csv_value(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


class Command(BaseCommand):
    help = "Liczy dokumenty, domeny i typy w aktywnej bazie korpusu oraz opcjonalnie eksportuje rejestr."

    def add_arguments(self, parser):
        parser.add_argument("--csv", type=Path, help="Ścieżka CSV dla pełnego rejestru źródeł.")
        parser.add_argument("--check", action="store_true", help="Zwróć błąd, jeśli korpus nie spełnia minimów.")

    def handle(self, *args, **options):
        rows = list(audit_rows())
        included = [row for row in rows if row["included_in_metrics"]]
        roles = {row["source_type"] for row in included if row["source_type"]}
        summary = {
            "registered_documents": len(rows),
            "documents": len(included),
            "distinct_source_domains": len({row["domain"] for row in included}),
            "distinct_source_types": len({row["kind"] for row in included if row["kind"]}),
            "source_roles": sorted(roles),
            "indexed_chunks": sum(row["indexed_chunks"] for row in included),
            "known_publication_dates": sum(row["published_at"] is not None for row in included),
            "known_validity_periods": sum(bool(row["valid_from"] and row["valid_to"]) for row in included),
        }
        summary["meets_minima"] = (
            summary["documents"] >= 100 and summary["distinct_source_domains"] >= 10
            and summary["distinct_source_types"] >= 2 and "secondary" in roles
            and bool(roles & {"official", "primary"})
        )
        if path := options["csv"]:
            try:
                with path.open("w", encoding="utf-8", newline="") as output:
                    writer = csv.DictWriter(output, fieldnames=COLUMNS)
                    writer.writeheader()
                    for row in rows:
                        writer.writerow({key: safe_csv_value(value) for key, value in row.items()})
            except OSError as exc:
                raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(summary, ensure_ascii=False))
        if options["check"] and not summary["meets_minima"]:
            raise CommandError("Korpus nie spełnia minimów liczebności, domen lub rodzajów źródeł.")
